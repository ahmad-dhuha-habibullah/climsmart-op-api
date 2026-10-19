"""Build an APSIM .met for a recent run: founder's history + Open-Meteo archive + Open-Meteo forecast.

Variables and units match the founder's file: year, day-of-year, radn (MJ/m2/day), maxt, mint (deg C),
rain (mm/day). Nothing is invented: a missing value in any appended day aborts the build.

KNOWN LIMITS (also written into the result file):
- The founder's history is for station "Merbau Selatan" (lat 2.56, lon 99.65), about 8-9 km from the
  Mambang Muda site (2.6235, 99.6922); the appended data are an Open-Meteo model grid cell for the site.
  Two different sources are spliced; step changes at the join are possible.
- Open-Meteo free tier is non-commercial (founder decision: demo use only).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
DAILY = "temperature_2m_max,temperature_2m_min,precipitation_sum,shortwave_radiation_sum"
ARCHIVE_LAG_DAYS = 7  # archive values this recent may be missing or preliminary; use forecast API instead


@dataclass(frozen=True)
class MetRow:
    day: date
    radn: float
    maxt: float
    mint: float
    rain: float


def parse_met(path: Path) -> tuple[list[str], list[MetRow]]:
    """Return (header lines up to and including the 'year day ...' line, rows)."""
    header: list[str] = []
    rows: list[MetRow] = []
    in_data = False
    for raw in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.rstrip()
        if not in_data:
            header.append(line)
            if re.match(r"^\s*year\s+day\s+radn", line):
                in_data = True
            continue
        parts = line.split()
        if len(parts) < 6 or not parts[0].isdigit():
            if "(MJ/m2)" in line and not rows:
                header.append(line)  # the units line belongs to the header and must be written back
            continue  # blank
        y, doy = int(parts[0]), int(parts[1])
        rows.append(MetRow(date(y, 1, 1) + timedelta(days=doy - 1), *(float(p) for p in parts[2:6])))
    if not in_data:
        raise ValueError("no 'year day radn maxt mint rain' line found in .met")
    return header, rows


def _fetch(url: str, params: dict, client: httpx.Client | None) -> dict:
    c = client or httpx.Client(timeout=60)
    try:
        r = c.get(url, params=params)
        r.raise_for_status()
        return r.json()
    finally:
        if client is None:
            c.close()


def _rows_from(data: dict, first: date, last: date) -> list[MetRow]:
    d = data["daily"]
    out: list[MetRow] = []
    for i, t in enumerate(d["time"]):
        day = date.fromisoformat(t)
        if day < first or day > last:
            continue
        vals = (d["shortwave_radiation_sum"][i], d["temperature_2m_max"][i],
                d["temperature_2m_min"][i], d["precipitation_sum"][i])
        if any(v is None for v in vals):
            raise ValueError(f"Open-Meteo has a missing value on {t}; refusing to invent weather")
        out.append(MetRow(day, *(float(v) for v in vals)))
    return out


def fetch_recent(lat: float, lon: float, first: date, today: date, forecast_days: int = 16,
                 client: httpx.Client | None = None) -> tuple[list[MetRow], dict]:
    """Archive for first..today-ARCHIVE_LAG_DAYS-1, then forecast API (analysis + forecast) to today+forecast_days-1."""
    arch_last = today - timedelta(days=ARCHIVE_LAG_DAYS + 1)
    arch = _rows_from(_fetch(ARCHIVE_URL, {
        "latitude": lat, "longitude": lon, "start_date": first.isoformat(), "end_date": arch_last.isoformat(),
        "daily": DAILY, "timezone": "UTC"}, client), first, arch_last)
    fc_first = arch_last + timedelta(days=1)
    past = (today - fc_first).days
    fc_data = _fetch(FORECAST_URL, {
        "latitude": lat, "longitude": lon, "daily": DAILY, "timezone": "UTC",
        "past_days": past, "forecast_days": forecast_days}, client)
    fc_last = today + timedelta(days=forecast_days - 1)
    fc = _rows_from(fc_data, fc_first, fc_last)
    info = {"archive_first": arch[0].day.isoformat() if arch else None, "archive_last": arch_last.isoformat(),
            "forecast_api_first": fc_first.isoformat(), "forecast_api_last": fc_last.isoformat(),
            "grid_lat": fc_data.get("latitude"), "grid_lon": fc_data.get("longitude"),
            "sources": ["Open-Meteo archive API (ERA5-based, CC BY 4.0)", "Open-Meteo forecast API (past_days + forecast_days)"]}
    return arch + fc, info


def check_consecutive(rows: list[MetRow]) -> None:
    for a, b in zip(rows, rows[1:]):
        if (b.day - a.day).days != 1:
            raise ValueError(f"weather gap between {a.day} and {b.day}")


def write_met(path: Path, header: list[str], rows: list[MetRow], extra_comment: list[str]) -> None:
    check_consecutive(rows)
    hdr = list(header)
    # comments go right after the [weather.met.weather] line; '!' lines are ignored by APSIM
    hdr[1:1] = ["!" + c for c in extra_comment]
    lines = hdr + [f"{r.day.year:8d}{r.day.timetuple().tm_yday:8d}{r.radn:8.2f}{r.maxt:8.2f}{r.mint:8.2f}{r.rain:8.2f}"
                   for r in rows]
    Path(path).write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")


def build_recent_met(founder_met: Path, out_path: Path, lat: float, lon: float, today: date | None = None,
                     forecast_days: int = 16, client: httpx.Client | None = None) -> dict:
    """Splice founder history (to its last day) + recent Open-Meteo data; return a description dict."""
    today = today or datetime.now(timezone.utc).date()
    header, hist = parse_met(founder_met)
    check_consecutive(hist)
    first_new = hist[-1].day + timedelta(days=1)
    recent, info = fetch_recent(lat, lon, first_new, today, forecast_days, client)
    rows = hist + recent
    write_met(out_path, header, rows, [
        f"Spliced by ClimSmart-OP on {datetime.now(timezone.utc).isoformat(timespec='seconds')}: founder history "
        f"{hist[0].day}..{hist[-1].day} (station Merbau Selatan, 2.56/99.65) + Open-Meteo {first_new}..{rows[-1].day} "
        f"(grid cell {info.get('grid_lat')}/{info.get('grid_lon')}). Two sources spliced; see ClimSmart docs."])
    return {"founder_history_first": hist[0].day.isoformat(), "founder_history_last": hist[-1].day.isoformat(),
            "appended_first": first_new.isoformat(), "last_day": rows[-1].day.isoformat(),
            "today_utc": today.isoformat(), "rows": len(rows), "open_meteo": info,
            "founder_station": {"name": "Merbau Selatan", "lat": 2.56, "lon": 99.65},
            "site": {"lat": lat, "lon": lon}}


def overlap_stats(hist: list[MetRow], lat: float, lon: float, first: date, last: date,
                  client: httpx.Client | None = None) -> dict:
    """Compare founder's station data with Open-Meteo archive over a period both cover (a measure of the
    splice mismatch). Reports annual-mean differences and daily rain/temperature correlations."""
    om = {r.day: r for r in _rows_from(_fetch(ARCHIVE_URL, {
        "latitude": lat, "longitude": lon, "start_date": first.isoformat(), "end_date": last.isoformat(),
        "daily": DAILY, "timezone": "UTC"}, client), first, last)}
    pairs = [(h, om[h.day]) for h in hist if h.day in om]
    n = len(pairs)
    if n == 0:
        return {"error": "no overlap"}
    years = n / 365.25

    def mean(f):
        return sum(f(p) for p in pairs) / n

    def corr(fa, fb):
        ma, mb = mean(fa), mean(fb)
        num = sum((fa(p) - ma) * (fb(p) - mb) for p in pairs)
        den = (sum((fa(p) - ma) ** 2 for p in pairs) * sum((fb(p) - mb) ** 2 for p in pairs)) ** 0.5
        return num / den if den else None

    return {
        "period": f"{first}..{last}", "days": n,
        "annual_rain_mm": {"founder_station": round(sum(h.rain for h, _ in pairs) / years),
                           "open_meteo": round(sum(o.rain for _, o in pairs) / years)},
        "mean_tmax_c": {"founder_station": round(mean(lambda p: p[0].maxt), 2), "open_meteo": round(mean(lambda p: p[1].maxt), 2)},
        "mean_tmin_c": {"founder_station": round(mean(lambda p: p[0].mint), 2), "open_meteo": round(mean(lambda p: p[1].mint), 2)},
        "mean_radn_mj_m2": {"founder_station": round(mean(lambda p: p[0].radn), 2), "open_meteo": round(mean(lambda p: p[1].radn), 2)},
        "daily_correlation": {"rain": round(corr(lambda p: p[0].rain, lambda p: p[1].rain), 2),
                              "tmax": round(corr(lambda p: p[0].maxt, lambda p: p[1].maxt), 2),
                              "radn": round(corr(lambda p: p[0].radn, lambda p: p[1].radn), 2)},
    }
