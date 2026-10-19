"""Caching wrapper for any weather provider: coarse cache, shared in-flight fetches, stale and snapshot fallback.

Why this exists: the free Open-Meteo API allows ONE concurrent request per IP (up to 5 more are queued, then HTTP 429
"Too many concurrent requests") and a per-IP minute/hour/day budget. A serverless host shares egress IPs, and one page
load fans out into several calls, so the provider must be called as rarely as possible and a failure must not become an
error card when we hold older data.

Order of answers for one request:
  1. fresh in-memory entry (younger than `ttl_seconds`) covering the requested window;
  2. a fetch from the inner provider, shared by every request that arrives while it runs (one upstream call);
  3. if that fails: an in-memory entry up to `stale_max_seconds` old, marked stale;
  4. else a committed snapshot file for the ~11 km cell (public weather only), marked stale, if it still has forecast days;
  5. else the provider's error.
Stale answers keep the real fetch time (`fetched_at`); their days are re-labelled past/forecast against today's date at
the site, so a day that has already happened is never shown as a forecast.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .base import DailyWeather, ProviderError, WeatherProvider, WeatherSeries

log = logging.getLogger("climsmart.provider")
SNAPSHOT_MAX_AGE_SECONDS = 7 * 86400


def cell_key(lat: float, lon: float, decimals: int = 1) -> tuple[float, float]:
    return (round(lat, decimals), round(lon, decimals))


def snapshot_name(lat: float, lon: float, decimals: int = 1) -> str:
    la, lo = cell_key(lat, lon, decimals)
    return f"{la:.{decimals}f}_{lo:.{decimals}f}.json"


def series_to_json(s: WeatherSeries, past_days: int, forecast_days: int) -> dict:
    return {"latitude": s.latitude, "longitude": s.longitude, "elevation_m": s.elevation_m, "timezone": s.timezone,
            "source_name": s.source_name, "source_url": s.source_url, "source_attribution": s.source_attribution,
            "source_license_note": s.source_license_note, "fetched_at": s.fetched_at, "past_days": past_days,
            "forecast_days": forecast_days, "extra": s.extra,
            "days": [{"date": d.date.isoformat(), "is_forecast": d.is_forecast, "rain_mm": d.rain_mm, "et0_mm": d.et0_mm,
                      "temp_max_c": d.temp_max_c, "temp_min_c": d.temp_min_c,
                      "rain_probability_pct": d.rain_probability_pct} for d in s.days]}


def series_from_json(j: dict) -> tuple[WeatherSeries, int, int]:
    days = [DailyWeather(date.fromisoformat(d["date"]), bool(d["is_forecast"]), float(d["rain_mm"]), float(d["et0_mm"]),
                         d.get("temp_max_c"), d.get("temp_min_c"), d.get("rain_probability_pct")) for d in j["days"]]
    s = WeatherSeries(j["latitude"], j["longitude"], j.get("elevation_m"), j["timezone"], days, j["source_name"],
                      j["source_url"], j["source_attribution"], j["source_license_note"], j["fetched_at"],
                      dict(j.get("extra") or {}))
    return s, int(j["past_days"]), int(j["forecast_days"])


def realign(series: WeatherSeries, today: date) -> WeatherSeries:
    """Label each day past/forecast against `today` (site-local). Values are untouched."""
    days = [replace(d, is_forecast=d.date >= today,
                    rain_probability_pct=d.rain_probability_pct if d.date >= today else None) for d in series.days]
    return replace(series, days=days)


def window(series: WeatherSeries, past_days: int, forecast_days: int) -> WeatherSeries:
    """The `past_days` before the first forecast day plus `forecast_days` from it (fewer if the series has fewer)."""
    i0 = next((i for i, d in enumerate(series.days) if d.is_forecast), None)
    if i0 is None:
        return series
    return replace(series, days=series.days[max(0, i0 - past_days): i0 + forecast_days])


def _site_today(series: WeatherSeries) -> date:
    try:
        return datetime.now(ZoneInfo(series.timezone)).date()
    except Exception:  # unknown zone name: use UTC rather than fail
        return datetime.now(timezone.utc).date()


class CachedProvider(WeatherProvider):
    """See the module docstring.

    `standard_window=(past, forecast)`: when set, every upstream call asks for at least this window and one entry per
    cell serves every smaller request (outlook, fertiliser timing and the forecast endpoint share ONE upstream call).
    When None, entries are keyed by the exact (past, forecast) pair, as before.
    """

    def __init__(self, inner: WeatherProvider, ttl_seconds: int = 3600, max_items: int = 512,
                 stale_max_seconds: int = 48 * 3600, clock=time.monotonic, decimals: int = 1,
                 standard_window: tuple[int, int] | None = None, snapshot_dir: Path | None = None,
                 realign_stale: bool = False, today_fn=None, upstream_concurrency: int = 2,
                 cooldown_seconds: float = 20.0):
        self.inner = inner
        self.name = inner.name
        self.ttl = ttl_seconds
        self.max_items = max_items
        self.stale_max = max(stale_max_seconds, ttl_seconds)
        self._clock = clock
        self.decimals = decimals
        self.std = standard_window
        self.snapshot_dir = snapshot_dir
        self.realign_stale = realign_stale
        self._today_fn = today_fn or _site_today
        self._store: dict[tuple, tuple[float, WeatherSeries, int, int]] = {}
        self._inflight: dict[tuple, tuple[int, int, asyncio.Task]] = {}
        self._sem: asyncio.Semaphore | None = None
        self._sem_n = upstream_concurrency
        self.cooldown = cooldown_seconds  # after a failure, leave the upstream alone this long (retries would only add load)
        self._cool: dict[tuple, tuple[float, ProviderError]] = {}
        self.upstream_calls = 0  # for tests and logs

    # -- helpers
    def _key(self, lat, lon, p, f) -> tuple:
        k = cell_key(lat, lon, self.decimals)
        return k if self.std else (*k, p, f)

    @staticmethod
    def _covers(p: int, f: int, have_p: int, have_f: int) -> bool:
        return have_p >= p and have_f >= f

    async def _fetch(self, key, lat, lon, p, f) -> WeatherSeries:
        if self._sem is None:  # created lazily inside the running loop
            self._sem = asyncio.Semaphore(self._sem_n)
        async with self._sem:
            self.upstream_calls += 1
            series = await self.inner.get_daily(lat, lon, p, f)
        now = self._clock()
        for k in [k for k, v in self._store.items() if now - v[0] > self.stale_max]:
            del self._store[k]
        if len(self._store) >= self.max_items and key not in self._store:
            oldest = min(self._store, key=lambda k: self._store[k][0])
            self._store.pop(oldest, None)
        self._store[key] = (now, series, p, f)
        return series

    def _stale(self, series: WeatherSeries, age: int, origin: str, p: int, f: int) -> WeatherSeries | None:
        if self.realign_stale:
            series = realign(series, self._today_fn(series))
            if not any(d.is_forecast for d in series.days):
                return None  # every day has already happened: nothing honest to show as a forecast
        return window(replace(series, extra={**series.extra, "stale": True, "age_seconds": age, "origin": origin}), p, f)

    def _snapshot(self, lat, lon, p, f) -> WeatherSeries | None:
        if not self.snapshot_dir:
            return None
        try:
            j = json.loads((Path(self.snapshot_dir) / snapshot_name(lat, lon, self.decimals)).read_text("utf-8"))
            series, sp, sf = series_from_json(j)
            fetched = datetime.fromisoformat(series.fetched_at)
            age = int((datetime.now(timezone.utc) - fetched).total_seconds())
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if age > SNAPSHOT_MAX_AGE_SECONDS or not self._covers(p, f, sp, sf):
            return None
        series = realign(series, self._today_fn(series))  # a file is always old: always re-label
        if not any(d.is_forecast for d in series.days):
            return None
        return window(replace(series, extra={**series.extra, "stale": True, "age_seconds": age, "origin": "snapshot"}), p, f)

    # -- main
    async def get_daily(self, lat, lon, past_days, forecast_days) -> WeatherSeries:
        p, f = past_days, forecast_days
        key = self._key(lat, lon, p, f)
        now = self._clock()
        hit = self._store.get(key)
        if hit and now - hit[0] < self.ttl and self._covers(p, f, hit[2], hit[3]):
            return window(hit[1], p, f) if self.std else hit[1]
        cool = self._cool.get(key)
        if cool and now < cool[0]:  # provider failed a moment ago: answer from the fallbacks without another upstream attempt
            return self._fallback(key, lat, lon, p, f, cool[1])
        # one upstream call at a time per cell: join one already running if it covers this request
        run = self._inflight.get(key)
        if run and self._covers(p, f, run[0], run[1]):
            task = run[2]
        else:
            want_p, want_f = (max(p, self.std[0]), max(f, self.std[1])) if self.std else (p, f)
            if hit and self.std:
                want_p, want_f = max(want_p, hit[2]), max(want_f, hit[3])
            task = asyncio.ensure_future(self._fetch(key, lat, lon, want_p, want_f))
            self._inflight[key] = (want_p, want_f, task)

            def _done(t, key=key):
                if self._inflight.get(key, (0, 0, None))[2] is t:
                    self._inflight.pop(key, None)
                if not t.cancelled():
                    t.exception()  # mark retrieved: every waiter handles its own copy of the error
            task.add_done_callback(_done)
        try:
            series = await asyncio.shield(task)
        except ProviderError as e:
            if self.cooldown > 0:
                wait = min(float(e.retry_after), 60.0) if e.retry_after else 0.0
                self._cool[key] = (self._clock() + max(self.cooldown, wait), e)
            return self._fallback(key, lat, lon, p, f, e)
        self._cool.pop(key, None)
        return window(series, p, f) if self.std else series

    def _fallback(self, key, lat, lon, p, f, e: ProviderError) -> WeatherSeries:
        hit = self._store.get(key)
        if hit and self._covers(p, f, hit[2], hit[3]) and self._clock() - hit[0] <= self.stale_max:
            out = self._stale(hit[1], int(self._clock() - hit[0]), "memory", p, f)
            if out is not None:
                log.warning("serving stale cache (%ss old) after provider failure: %s", out.extra["age_seconds"], e.kind)
                return out
        snap = self._snapshot(lat, lon, p, f)
        if snap is not None:
            log.warning("serving committed snapshot (%ss old) after provider failure: %s", snap.extra["age_seconds"], e.kind)
            return snap
        raise e
