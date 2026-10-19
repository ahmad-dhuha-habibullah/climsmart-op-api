"""Client identification, optional API-key auth and a bounded in-memory rate limiter."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import time
from collections import deque

from fastapi import HTTPException, Request

_SALT = os.urandom(8).hex()  # per-process salt so logged IP hashes are not trivially reversible


def parse_networks(items: list[str]) -> list:
    """Parse trusted-proxy entries (IPs or CIDRs). Raises ValueError on a bad entry (fail fast)."""
    return [ipaddress.ip_network(i, strict=False) for i in items]


def _in(ip: str, nets) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(a in n for n in nets)


def client_ip(peer: str | None, xff: str | None, trusted) -> str:
    """Real client IP. X-Forwarded-For is believed ONLY when the direct peer is a trusted proxy.

    Walk the header from the right (closest hop first), skipping trusted proxies; the first
    untrusted address is the client. Entries a client put at the left of the header are ignored.
    """
    if not peer:
        return "unknown"
    if not trusted or not _in(peer, trusted) or not xff:
        return peer
    hops = [h.strip() for h in xff.split(",") if h.strip()]
    for h in reversed(hops):
        try:
            ipaddress.ip_address(h)
        except ValueError:
            return peer  # malformed header: do not trust any of it
        if not _in(h, trusted):
            return h
    return hops[0] if hops else peer


def _h(value: str, salt: str = "") -> str:
    return hashlib.sha256((salt + value).encode()).hexdigest()[:12]


def identify(request: Request) -> str:
    """Stable, non-reversible client id used for rate limiting and logs (never the raw API key).

    A supplied X-API-Key counts as identity only when it is a configured, valid key. Otherwise the
    caller is identified by IP, so inventing keys cannot dodge the limit.
    """
    st = request.app.state
    key = request.headers.get("x-api-key")
    if key and st.settings.api_keys and any(hmac.compare_digest(key, k) for k in st.settings.api_keys):
        return "key:" + _h(key)
    peer = request.client.host if request.client else None
    ip = client_ip(peer, request.headers.get("x-forwarded-for"), st.trusted_networks)
    return "ip:" + _h(ip, _SALT)


class RateLimiter:
    """Sliding window per client, in memory, bounded (single process; fine for a demo).

    Memory bound: stale clients are swept every `sweep_interval` s, and at most `max_clients`
    are tracked (the least recently active is evicted when full).
    """

    def __init__(self, per_minute: int, max_clients: int = 10000, sweep_interval: float = 30.0,
                 clock=time.monotonic):
        self.per_minute = per_minute
        self.max_clients = max_clients
        self.sweep_interval = sweep_interval
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._last_sweep = clock()

    def _sweep(self, now: float) -> None:
        self._last_sweep = now
        for k in [k for k, q in self._hits.items() if not q or now - q[-1] > 60]:
            del self._hits[k]

    def __len__(self) -> int:
        return len(self._hits)

    def check(self, client: str) -> None:
        if self.per_minute <= 0:
            return
        now = self._clock()
        if now - self._last_sweep >= self.sweep_interval:
            self._sweep(now)
        q = self._hits.get(client)
        if q is None:
            if len(self._hits) >= self.max_clients:
                self._sweep(now)
                if len(self._hits) >= self.max_clients:
                    oldest = min(self._hits, key=lambda k: self._hits[k][-1])
                    del self._hits[oldest]
            q = self._hits[client] = deque()
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= self.per_minute:
            retry = max(1, int(60 - (now - q[0])))
            raise HTTPException(429, "Rate limit exceeded", headers={"Retry-After": str(retry)})
        q.append(now)


async def guard(request: Request) -> None:
    """Dependency for /v1 routes: optional API key, then rate limit."""
    state = request.app.state
    keys: list[str] = state.settings.api_keys
    if keys:
        key = request.headers.get("x-api-key")
        if not key or not any(hmac.compare_digest(key, k) for k in keys):
            raise HTTPException(401, "Missing or invalid API key (send header X-API-Key)")
    state.limiter.check(identify(request))
