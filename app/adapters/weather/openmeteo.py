"""Open-Meteo daily adapter for plots: archive (ERA5-based) for older days + forecast API for recent/forecast days.

Same sources and variable mapping as climsmart/apsim/weather.py (the v1 MET builder), but for any period and any
point, and it never fills a missing value: a null stays None and is reported by the MET validator.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone

import httpx

from app.core.errors import ApiError
from climsmart.apsim.weather import ARCHIVE_LAG_DAYS, ARCHIVE_URL, FORECAST_URL

VARS = {  # Open-Meteo daily variable -> canonical name (MET units)
    "shortwave_radiation_sum": "radn",  # MJ/m2/day
    "temperature_2m_max": "maxt",  # degC
    "temperature_2m_min": "mint",  # degC
    "precipitation_sum": "rain",  # mm/day
    "et0_fao_evapotranspiration": "et0",  # mm/day (not written to MET; shown in the explorer)
}
UNITS = {"radn": "MJ/m2/day", "maxt": "degC", "mint": "degC", "rain": "mm/day", "et0": "mm/day"}
MAX_FORECAST_DAYS = 16
ATTRIBUTION = "Weather data by Open-Meteo.com (CC BY 4.0). Free tier is non-commercial use only."
_cache: dict[tuple, tuple[float, dict]] = {}
CACHE_TTL = 1800


def _get(client: httpx.Client, url: str, params: dict) -> dict:
    try:
        r = client.get(url, params=params)
    except httpx.TimeoutException:
        raise ApiError(502, "weather_upstream_timeout", "Open-Meteo did not answer in time")
    except httpx.TransportError as e:
        raise ApiError(502, "weather_upstream_unreachable", f"Open-Meteo unreachable ({e.__class__.__name__})")
    if r.status_code == 429:
        raise ApiError(503, "weather_upstream_rate_limited", "Open-Meteo is rate-limiting this service; retry later")
    if r.status_code >= 400:
        reason = None
        try:
            reason = r.json().get("reason")
        except ValueError:
            pass
        raise ApiError(502, "weather_upstream_error", f"Open-Meteo returned HTTP {r.status_code}",
                       {"reason": reason})
    return r.json()


def _rows(data: dict, source: str, first: date, last: date) -> list[dict]:
    d = data.get("daily") or {}
    out = []
    for i, t in enumerate(d.get("time", [])):
        day = date.fromisoformat(t)
        if day < first or day > last:
            continue
        row = {"date": day.isoformat(), "source": source}
        for k, name in VARS.items():
            vals = d.get(k) or []
            v = vals[i] if i < len(vals) else None
            row[name] = None if v is None else float(v)
        out.append(row)
    return out


def fetch_daily(lat: float, lon: float, start: date, end: date, *, api_key: str | None = None,
                timeout: float = 30.0, today: date | None = None, client: httpx.Client | None = None) -> dict:
    """Daily weather for [start, end] at a point. Returns {"days": [...], "provenance": {...}}."""
    today = today or datetime.now(timezone.utc).date()
    if end < start:
        raise ApiError(422, "invalid_period", "end must not be before start")
    max_end = today + timedelta(days=MAX_FORECAST_DAYS - 1)
    if end > max_end:
        raise ApiError(422, "invalid_period", f"end is beyond the forecast horizon ({max_end})")
    if start < date(1940, 1, 1):
        raise ApiError(422, "invalid_period", "Open-Meteo archive starts in 1940")
    key = (round(lat, 4), round(lon, 4), start, end, today)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]
    arch_last = today - timedelta(days=ARCHIVE_LAG_DAYS + 1)
    daily = ",".join(VARS)
    own = client is None
    c = client or httpx.Client(timeout=timeout, headers={"User-Agent": "ClimSmart-OP/2 (plot weather)"})
    requests: list[dict] = []
    days: list[dict] = []
    grid = {}
    try:
        if start <= arch_last:
            a_end = min(end, arch_last)
            p = {"latitude": lat, "longitude": lon, "start_date": start.isoformat(), "end_date": a_end.isoformat(),
                 "daily": daily, "timezone": "UTC"}
            j = _get(c, ARCHIVE_URL, p)
            days += _rows(j, "open-meteo-archive", start, a_end)
            grid = {"grid_lat": j.get("latitude"), "grid_lon": j.get("longitude"), "elevation_m": j.get("elevation")}
            requests.append({"url": ARCHIVE_URL, "start_date": p["start_date"], "end_date": p["end_date"]})
        if end > arch_last:
            f_start = max(start, arch_last + timedelta(days=1))
            p = {"latitude": lat, "longitude": lon, "start_date": f_start.isoformat(), "end_date": end.isoformat(),
                 "daily": daily, "timezone": "UTC"}
            if api_key:
                p["apikey"] = api_key
            j = _get(c, FORECAST_URL, p)
            fc = _rows(j, "open-meteo-forecast", f_start, end)
            for r in fc:  # days after today are forecasts; today and before are the forecast model's analysis
                r["kind"] = "forecast" if date.fromisoformat(r["date"]) > today else "analysis"
            days += fc
            grid = grid or {"grid_lat": j.get("latitude"), "grid_lon": j.get("longitude"),
                            "elevation_m": j.get("elevation")}
            requests.append({"url": FORECAST_URL, "start_date": p["start_date"], "end_date": p["end_date"]})
    finally:
        if own:
            c.close()
    for r in days:
        r.setdefault("kind", "reanalysis")
    result = {"days": days, "units": UNITS,
              "provenance": {"provider": "open-meteo", "attribution": ATTRIBUTION, "requests": requests,
                             "point": {"lat": lat, "lon": lon}, **grid, "today_utc": today.isoformat(),
                             "archive_lag_days": ARCHIVE_LAG_DAYS,
                             "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                             "note": "Gridded model data at the plot centroid, not on-site measurements."}}
    _cache[key] = (time.time(), result)
    return result
