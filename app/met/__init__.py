"""MET file generation and validation (APSIM weather format).

Columns/units follow the v1 builder (climsmart/apsim/weather.py): year, day, radn (MJ/m2), maxt, mint (degC),
rain (mm). tav/amp are computed the way APSIM's tav_amp utility does: tav = mean of monthly mean temperatures;
amp = mean over years of (warmest - coolest monthly mean). Missing values are NEVER filled: they make the file
invalid and no artifact is written for an invalid file.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

MET_VARS = ("radn", "maxt", "mint", "rain")
RANGES = {"radn": (0.0, 45.0), "maxt": (-10.0, 50.0), "mint": (-15.0, 40.0), "rain": (0.0, 500.0)}
STRATEGIES = {
    "model_only": "Gridded model weather only (Open-Meteo archive + forecast at the plot centroid). No observations.",
}
UNAVAILABLE_STRATEGIES = {
    "sensor_assimilation": "Sensor assimilation is not implemented in this release (no sensor/observation "
                           "ingestion yet). Use strategy 'model_only'.",
}


def tav_amp(rows: list[dict]) -> tuple[float | None, float | None, list[str]]:
    warnings = []
    monthly: dict[tuple[int, int], list[float]] = defaultdict(list)
    for r in rows:
        if r.get("maxt") is None or r.get("mint") is None:
            continue
        d = date.fromisoformat(r["date"])
        monthly[(d.year, d.month)].append((r["maxt"] + r["mint"]) / 2)
    if not monthly:
        return None, None, ["no temperature data for tav/amp"]
    means = {k: sum(v) / len(v) for k, v in monthly.items()}
    tav = sum(means.values()) / len(means)
    by_year: dict[int, list[float]] = defaultdict(list)
    for (y, _m), v in means.items():
        by_year[y].append(v)
    full = [max(v) - min(v) for v in by_year.values() if len(v) == 12]
    if full:
        amp = sum(full) / len(full)
    else:
        amp = max(means.values()) - min(means.values())
        warnings.append("amp computed from fewer than 12 months (no complete calendar year); "
                        "APSIM soil temperature uses it, treat as approximate")
    return round(tav, 2), round(amp, 2), warnings


def validate(rows: list[dict], start: date, end: date) -> dict:
    expected = (end - start).days + 1
    by_date = {r["date"]: r for r in rows}
    missing_days = [(start + timedelta(days=i)).isoformat() for i in range(expected)
                    if (start + timedelta(days=i)).isoformat() not in by_date]
    errors, warnings = [], []
    missing_values = defaultdict(list)
    out_of_range = []
    stats = {}
    for v in MET_VARS:
        vals = [r[v] for r in rows if r.get(v) is not None]
        for r in rows:
            x = r.get(v)
            if x is None:
                missing_values[v].append(r["date"])
            elif not (RANGES[v][0] <= x <= RANGES[v][1]):
                out_of_range.append({"date": r["date"], "variable": v, "value": x, "allowed": RANGES[v]})
        stats[v] = ({"min": round(min(vals), 2), "max": round(max(vals), 2), "mean": round(sum(vals) / len(vals), 2),
                     "n": len(vals)} if vals else {"n": 0})
    stats["rain"]["total"] = round(sum(r["rain"] for r in rows if r.get("rain") is not None), 1)
    inverted = [r["date"] for r in rows if r.get("maxt") is not None and r.get("mint") is not None
                and r["maxt"] < r["mint"]]
    if missing_days:
        errors.append({"code": "missing_days", "message": f"{len(missing_days)} day(s) absent from the source",
                       "dates": missing_days[:50]})
    for v, ds in missing_values.items():
        errors.append({"code": "missing_values", "variable": v,
                       "message": f"{len(ds)} missing {v} value(s); gaps are not filled", "dates": ds[:50]})
    if out_of_range:
        errors.append({"code": "out_of_range", "message": f"{len(out_of_range)} value(s) outside plausible range",
                       "items": out_of_range[:50]})
    if inverted:
        errors.append({"code": "maxt_below_mint", "message": f"{len(inverted)} day(s) with maxt < mint",
                       "dates": inverted[:50]})
    present = expected - len(missing_days)
    complete = sum(1 for r in rows if all(r.get(v) is not None for v in MET_VARS))
    if expected < 365:
        warnings.append("period shorter than one year; long-term APSIM oil-palm runs need multi-year weather")
    kinds = defaultdict(int)
    for r in rows:
        kinds[r.get("kind", "unknown")] += 1
    if kinds.get("forecast"):
        warnings.append(f"{kinds['forecast']} day(s) are forecasts, not observed/reanalysis weather")
    return {"status": "valid" if not errors else "invalid", "expected_days": expected, "present_days": present,
            "complete_days": complete, "coverage_pct": round(100 * complete / expected, 2) if expected else 0,
            "errors": errors, "warnings": warnings, "stats": stats, "day_kinds": dict(kinds),
            "ranges": {k: list(v) for k, v in RANGES.items()}}


def render(rows: list[dict], lat: float, lon: float, tav: float, amp: float, comments: list[str]) -> str:
    lines = ["[weather.met.weather]", *[f"! {c}" for c in comments],
             f"latitude = {lat:.6f} (DECIMAL DEGREES)", f"longitude = {lon:.6f} (DECIMAL DEGREES)",
             f"tav = {tav:.2f} (oC)", f"amp = {amp:.2f} (oC)",
             "", "year day radn maxt mint rain", "() () (MJ/m^2) (oC) (oC) (mm)"]
    for r in sorted(rows, key=lambda r: r["date"]):
        d = date.fromisoformat(r["date"])
        lines.append(f"{d.year:8d}{d.timetuple().tm_yday:8d}{r['radn']:8.2f}{r['maxt']:8.2f}{r['mint']:8.2f}"
                     f"{r['rain']:8.2f}")
    return "\r\n".join(lines) + "\r\n"


def parse_period(text: str) -> tuple[date, date, int]:
    """First/last date and row count of a rendered MET (used to cross-check configs)."""
    rows = [ln.split() for ln in text.splitlines() if ln[:8].strip().isdigit()]
    def d(p):
        return date(int(p[0]), 1, 1) + timedelta(days=int(p[1]) - 1)
    return d(rows[0]), d(rows[-1]), len(rows)
