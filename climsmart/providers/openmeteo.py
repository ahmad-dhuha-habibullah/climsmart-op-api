"""Open-Meteo provider (https://open-meteo.com).

LICENSING: the free Open-Meteo API is for NON-COMMERCIAL use only. Commercial use needs a
paid plan (set OPEN_METEO_API_KEY) or a different provider implementing WeatherProvider.
Data is licensed CC BY 4.0: attribution is required and is included in every response.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import date, datetime, timezone

import httpx

from .. import __version__
from .base import DailyWeather, ProviderError, WeatherProvider, WeatherSeries

log = logging.getLogger("climsmart.provider")
# Identify ourselves: Open-Meteo asks API users to be identifiable, and a shared serverless egress IP is easier to
# tell apart from abusive traffic when requests carry a real application name instead of the library default.
USER_AGENT = f"ClimSmart-OP/{__version__} (weather demo; https://climsmart-op-api.vercel.app)"
FREE_URL = "https://api.open-meteo.com/v1/forecast"
PAID_URL = "https://customer-api.open-meteo.com/v1/forecast"
DAILY_VARS = [
    "precipitation_sum",
    "et0_fao_evapotranspiration",
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_probability_max",
]


class OpenMeteoProvider(WeatherProvider):
    name = "open-meteo"

    MAX_RETRY_WAIT = 10.0  # never sleep longer than this between attempts, whatever Retry-After says
    MIN_ATTEMPT_SECONDS = 1.0  # do not start another attempt with less budget than this left

    def __init__(self, api_key: str | None = None, client: httpx.AsyncClient | None = None,
                 timeout: float = 6.0, retries: int = 2, backoff_seconds: float = 0.5,
                 sleep=asyncio.sleep, budget_seconds: float = 9.0, clock=time.monotonic):
        self.api_key = api_key
        self.url = PAID_URL if api_key else FREE_URL
        self._client = client
        self.timeout = timeout
        self.retries = max(0, retries)
        self.backoff = backoff_seconds
        self._sleep = sleep  # injectable for tests
        self.budget = budget_seconds  # total time for ALL attempts of one call (Vercel's function limit is far above this)
        self._clock = clock

    async def _once(self, params: dict, timeout: float) -> httpx.Response:
        headers = {"User-Agent": USER_AGENT}
        if self._client is not None:
            return await self._client.get(self.url, params=params, timeout=timeout, headers=headers)
        async with httpx.AsyncClient() as c:
            return await c.get(self.url, params=params, timeout=timeout, headers=headers)

    @staticmethod
    def _reason(r: httpx.Response) -> str | None:
        """Open-Meteo's 429/4xx body is JSON {"error": true, "reason": "..."}; keep a short copy for the log."""
        try:
            v = r.json().get("reason")
            return str(v)[:200] if v else None
        except (ValueError, AttributeError):
            return None

    @staticmethod
    def _retry_after(r: httpx.Response) -> int | None:
        v = r.headers.get("retry-after", "")
        return int(v) if v.isdigit() else None

    async def get_daily(self, lat, lon, past_days, forecast_days) -> WeatherSeries:
        params = {
            "latitude": lat,
            "longitude": lon,
            "daily": ",".join(DAILY_VARS),
            "past_days": past_days,
            "forecast_days": forecast_days,
            "timezone": "auto",
        }
        if self.api_key:
            params["apikey"] = self.api_key
        err: ProviderError | None = None
        data = None
        t0 = self._clock()
        for attempt in range(self.retries + 1):
            err = None
            remaining = self.budget - (self._clock() - t0)
            try:
                r = await self._once(params, max(0.5, min(self.timeout, remaining)))
            except httpx.TimeoutException:
                err = ProviderError("Open-Meteo request timed out", kind="timeout")
            except httpx.TransportError as e:
                err = ProviderError(f"Open-Meteo unreachable: {e.__class__.__name__}")
            else:
                if r.status_code == 429:
                    reason = self._reason(r)
                    ra = self._retry_after(r)
                    if ra is None and reason:  # no header: the reason names the window that was exceeded
                        low = reason.lower()
                        ra = 86400 if "daily" in low else 3600 if "hourly" in low else 60 if "minutely" in low else None
                    err = ProviderError("Open-Meteo rate limit reached", kind="rate_limited",
                                        status=429, retry_after=ra, reason=reason)
                elif r.status_code >= 500:
                    err = ProviderError(f"Open-Meteo server error {r.status_code}", status=r.status_code,
                                        reason=self._reason(r))
                elif r.status_code >= 400:  # our request is wrong: retrying will not help
                    raise ProviderError(f"Open-Meteo rejected the request ({r.status_code})",
                                        status=r.status_code, reason=self._reason(r))
                else:
                    try:
                        data = r.json()
                    except ValueError as e:
                        raise ProviderError("Open-Meteo returned invalid JSON", kind="data") from e
                    break
            if attempt < self.retries:
                wait = err.retry_after if err.retry_after is not None else \
                    self.backoff * (2 ** attempt) * (0.5 + random.random())
                wait = min(float(wait), self.MAX_RETRY_WAIT)
                left = self.budget - (self._clock() - t0)
                if wait + self.MIN_ATTEMPT_SECONDS > left:  # Retry-After longer than our budget: stop, do not hang
                    log.warning("provider gave up: %s status=%s retry_after=%s reason=%s (budget left %.1fs)",
                                err, err.status, err.retry_after, err.reason, left)
                    break
                await self._sleep(wait)
        if data is None:
            assert err is not None
            log.warning("provider failed: kind=%s status=%s retry_after=%s reason=%s", err.kind, err.status,
                        err.retry_after, err.reason)
            raise err
        return self._parse(data, past_days)

    @staticmethod
    def _parse(data: dict, past_days: int) -> WeatherSeries:
        try:
            d = data["daily"]
            times = d["time"]
            rain = d["precipitation_sum"]
            et0 = d["et0_fao_evapotranspiration"]
            tmax = d.get("temperature_2m_max") or [None] * len(times)
            tmin = d.get("temperature_2m_min") or [None] * len(times)
            prob = d.get("precipitation_probability_max") or [None] * len(times)
        except (KeyError, TypeError) as e:
            raise ProviderError("Open-Meteo response missing expected fields", kind="data") from e
        days: list[DailyWeather] = []
        trimmed = 0
        # Open-Meteo sometimes has no value yet for the LAST forecast day(s) (null). Those trailing forecast days are
        # dropped (never invented); a null anywhere earlier, or in the past part, is still an error below.
        end = len(times)
        while end > past_days + 1 and (rain[end - 1] is None or et0[end - 1] is None):
            end -= 1
        trailing = len(times) - end
        for i, t in enumerate(times[:end]):
            if rain[i] is None or et0[i] is None:
                if not days:
                    # Archive data can start later than requested at some points: drop the
                    # leading empty days (shorter spin-up). Never invent values.
                    trimmed += 1
                    continue
                raise ProviderError(f"Open-Meteo returned a gap on {t}", kind="data")
            days.append(DailyWeather(
                date=date.fromisoformat(t),
                is_forecast=i >= past_days,
                rain_mm=float(rain[i]),
                et0_mm=float(et0[i]),
                temp_max_c=None if tmax[i] is None else float(tmax[i]),
                temp_min_c=None if tmin[i] is None else float(tmin[i]),
                rain_probability_pct=None if i < past_days or prob[i] is None else float(prob[i]),
            ))
        return WeatherSeries(
            latitude=data.get("latitude"), longitude=data.get("longitude"),
            elevation_m=data.get("elevation"), timezone=data.get("timezone", "UTC"),
            days=days,
            source_name="Open-Meteo",
            source_url="https://open-meteo.com",
            source_attribution="Weather data by Open-Meteo.com (CC BY 4.0)",
            source_license_note=("Free tier is non-commercial use only; commercial use requires "
                                 "a paid Open-Meteo plan or another provider."),
            fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            extra={"leading_days_trimmed": trimmed, "trailing_days_trimmed": trailing},
        )
