"""Run APSIM-X on a prepared copy of the founder's model and distil a compact, labelled result file.

Offline only (never inside a web request). The API serves the result file.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from ..rules import ThresholdSet
from .model_copy import DAILY_REPORT_NAME, make_daily_copy
from .weather import MetRow, build_recent_met, check_consecutive, overlap_stats, parse_met, write_met

DEFAULT_HOME = Path(os.environ.get("CLIMSMART_APSIM_HOME", "/workspace/apsim-runtime/apsimng-8030"))
SITE = {"name": "Mambang Muda, North Sumatra, Indonesia", "lat": 2.6235, "lon": 99.6922}
HISTORY_DAYS = 14

LIMITATIONS = [
    "INDICATIVE, NOT VALIDATED: the APSIM-X oil-palm model has not been calibrated or checked against any "
    "observation for this site or for Sumatra (the founder's model notes say BunchSizeMax was fitted to Papua New "
    "Guinea data and RUE follows Henson & Dolmat 2003).",
    "Only the Mambang Muda point is supported: there is no APSIM run for any other location.",
    "Palm age is an assumption, not the estate's: the founder's file plants on 2001-01-01, but run past about 2021 "
    "the model breaks (palms 25 years old: leaf area goes negative and APSIM aborts). The copy used here plants on "
    "1 January of an assumed year (default 2012, so about 14.8 years old in Oct 2026) and starts the clock that day. "
    "Real block ages, density and management are unknown.",
    "Soil provenance is undocumented: layer values look partly measured and partly template defaults; a single "
    "2 m profile represents the whole point; initial water and nitrate in 2001 look implausible (spun up since).",
    "Weather: history 2000-2022 is a nearby station series (about 8-9 km away, source undocumented); 2023 onward is "
    "an Open-Meteo model grid cell. The two sources differ (see weather_inputs.overlap_check), so the splice can "
    "shift the water balance. Forecast days use forecast rainfall, which is uncertain.",
    "Free Open-Meteo tier is non-commercial; this is a demo.",
    "The class on APSIM numbers is taken from APSIM's water-stress index (1 - fw) using the same provisional "
    "0.3/0.5/0.7 cut-offs as the simple bucket (no citable source). It is a different quantity from the bucket's soil "
    "depletion: in the model fw = palm transpiration / demand, and transpiration is limited to a supply rate "
    "(sum over layers of KL x (soil water - LL15), about 3.5 mm/day in Aug 2026), so fw falls below 1 on high-demand "
    "days even when the soil is wetter than field capacity.",
    "Not a yield forecast. The model reports FFB yield in its annual outputs but that is not served or claimed.",
]


class ApsimRunError(RuntimeError):
    pass


def apsim_info(home: Path) -> dict:
    pinned = json.loads((home / "PINNED.json").read_text()) if (home / "PINNED.json").exists() else {}
    return {"name": "APSIM Next Generation", "version": pinned.get("apsim_version"),
            "build_date": pinned.get("apsim_build_date"), "docker_image_tag": pinned.get("image"),
            "docker_image_digest": pinned.get("image_digest_amd64"),
            "dotnet": pinned.get("dotnet_runtime"), "run_method": "dotnet Models.dll (no docker)"}


def run_models(apsimx: Path, home: Path = DEFAULT_HOME) -> tuple[float, str]:
    env = dict(os.environ, DOTNET_ROOT=str(home / "dotnet"), DOTNET_SYSTEM_GLOBALIZATION_INVARIANT="0",
               DOTNET_CLI_TELEMETRY_OPTOUT="1",
               LD_LIBRARY_PATH=f"{home / 'lib'}:{os.environ.get('LD_LIBRARY_PATH', '')}")
    cmd = [str(home / "dotnet" / "dotnet"), str(home / "apsim" / "Models.dll"), str(apsimx)]
    t0 = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=1800)
    dt = time.time() - t0
    log = (p.stdout or "") + (p.stderr or "")
    if p.returncode != 0 or "ERROR" in log:
        raise ApsimRunError(f"APSIM exited {p.returncode}; log tail:\n{log[-1500:]}")
    return dt, log


def read_daily(db: Path) -> list[dict]:
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    try:
        rows = c.execute(f'select * from {DAILY_REPORT_NAME} order by "Clock.Today"').fetchall()
    finally:
        pass
    out = []
    for r in rows:
        d = dict(r)
        d["date"] = d.pop("Clock.Today")[:10]
        out.append(d)
    c.close()
    return out


def read_annual(db: Path) -> list[dict]:
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    rows = c.execute('select "Clock.Today.Year" y, "OilPalm.Age" age, "OilPalm.LAI" lai, AnnualRain rain, '
                     'AnnualPotentialEvaporation eo, AnnualET et, "Calculations.Script.AnnualYield" ffb '
                     'from AnnualOutput order by y').fetchall()
    c.close()
    return [dict(r) for r in rows]


def _shape_day(r: dict, today: date, ts: ThresholdSet) -> dict:
    taw = r["DULMM"] - r["LL15MM"]
    avail = max(0.0, min(r["SoilWaterMM"], r["DULMM"]) - r["LL15MM"])  # capped at DUL: water above DUL is reported separately
    above_dul = max(0.0, r["SoilWaterMM"] - r["DULMM"])
    frac = max(0.0, min(1.0, avail / taw)) if taw > 0 else 0.0
    depl = 1.0 - frac
    d = date.fromisoformat(r["date"])
    stress = max(0.0, 1.0 - r["FW"])
    cls = ts.classify(stress)  # class from APSIM's water-stress index (1 - fw), NOT from soil depletion
    et = r["EsMM"] + r["EPMM"] + r["UnderstoryEPMM"]
    return {
        "date": r["date"], "kind": "forecast" if d >= today else "past",
        "rain_mm": round(r["RainMM"], 2), "eo_mm": round(r["EoMM"], 2), "et_mm": round(et, 2),
        "es_mm": round(r["EsMM"], 2), "ep_mm": round(r["EPMM"], 2),
        "potential_ep_mm": round(r["PotentialEPMM"], 2), "lai": round(r["LAI"], 2),
        "fw": round(r["FW"], 3), "stress_index": round(max(0.0, 1.0 - r["FW"]), 3),
        "understory_fw": round(r["UnderstoryFW"], 3),
        "soil_water_mm": round(r["SoilWaterMM"], 1), "available_water_mm": round(avail, 1),
        "water_above_dul_mm": round(above_dul, 1),
        "available_fraction": round(frac, 3), "depletion_fraction": round(depl, 3),
        "runoff_mm": round(r["RunoffMM"], 2), "drainage_mm": round(r["DrainageMM"], 2),
        "category": cls.name, "label": cls.label,
    }


def monthly_context(daily_all: list[dict], today: date, months: int = 12) -> list[dict]:
    """Month-by-month APSIM summary for the last `months` full months before today (context, not a forecast)."""
    first = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
    for _ in range(months - 1):
        first = (first - timedelta(days=1)).replace(day=1)
    out: dict[str, list[dict]] = {}
    for r in daily_all:
        d = date.fromisoformat(r["date"])
        if first <= d < today.replace(day=1):
            out.setdefault(r["date"][:7], []).append(r)
    res = []
    for m, rs in out.items():
        taw = rs[0]["DULMM"] - rs[0]["LL15MM"]
        res.append({"month": m, "rain_mm": round(sum(x["RainMM"] for x in rs)), "eo_mm": round(sum(x["EoMM"] for x in rs)),
                    "et_mm": round(sum(x["EsMM"] + x["EPMM"] + x["UnderstoryEPMM"] for x in rs)),
                    "min_fw": round(min(x["FW"] for x in rs), 3),
                    "days_fw_below_0_95": sum(1 for x in rs if x["FW"] < 0.95),
                    "max_depletion_fraction": round(max(1 - max(0.0, min(1.0, (min(x["SoilWaterMM"], x["DULMM"]) - x["LL15MM"]) / taw)) for x in rs), 3)})
    return res


def _scenario(sid: str, desc: str, daily_all: list[dict], today: date, ts: ThresholdSet) -> dict:
    first = today - timedelta(days=HISTORY_DAYS)
    days = [_shape_day(r, today, ts) for r in daily_all if date.fromisoformat(r["date"]) >= first]
    fc = [d for d in days if d["kind"] == "forecast"][:7]
    peak = max(fc, key=lambda d: d["stress_index"]) if fc else None
    low = peak
    return {"id": sid, "description": desc, "daily": days,
            "summary_next_7_days": {
                "peak_stress_category": peak["category"] if peak else None,
                "peak_stress_index": peak["stress_index"] if peak else None,
                "peak_stress_date": peak["date"] if peak else None,
                "min_fw": low["fw"] if low else None,
                "max_depletion_fraction": max((d["depletion_fraction"] for d in fc), default=None),
                "rain_mm_total": round(sum(d["rain_mm"] for d in fc), 1),
                "et_mm_total": round(sum(d["et_mm"] for d in fc), 1)}}


def run_pipeline(founder_apsimx: Path, founder_met: Path, workdir: Path, results_path: Path,
                 ts: ThresholdSet, home: Path = DEFAULT_HOME, today: date | None = None) -> dict:
    today = today or datetime.now(timezone.utc).date()
    workdir.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc)
    sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()

    # 1. weather: as-is splice and a rain-scaled sensitivity splice
    met_as_is = workdir / "MambangMuda.met"
    winfo = build_recent_met(founder_met, met_as_is, SITE["lat"], SITE["lon"], today)
    header, hist = parse_met(founder_met)
    ov = overlap_stats(hist, SITE["lat"], SITE["lon"], date(2013, 1, 1), hist[-1].day)
    factor = ov["annual_rain_mm"]["founder_station"] / ov["annual_rain_mm"]["open_meteo"]
    _, all_rows = parse_met(met_as_is)
    cut = date.fromisoformat(winfo["appended_first"])
    scaled = [r if r.day < cut else MetRow(r.day, r.radn, r.maxt, r.mint, round(r.rain * factor, 2)) for r in all_rows]
    met_scaled = workdir / "MambangMuda_rainscaled.met"
    write_met(met_scaled, header, scaled, [
        f"SENSITIVITY: Open-Meteo rain from {cut} multiplied by {factor:.3f} (station/Open-Meteo annual ratio 2013-2022). Not a calibration."])
    check_consecutive(scaled)
    clock_end = f"{all_rows[-1].day.isoformat()}T00:00:00"

    # 2. model copies + runs. Planting year is an assumption (see model_copy.make_daily_copy).
    scen_defs = [
        ("as_is", "Default. Palms planted 2012-01-01 (about 14.8 years old in Oct 2026, assumed), Open-Meteo weather as delivered.",
         met_as_is, 2012),
        ("older_palms_2008", "Sensitivity: palms planted 2008-01-01 (about 18.8 years old), same weather.", met_as_is, 2008),
        ("younger_palms_2016", "Sensitivity: palms planted 2016-01-01 (about 10.8 years old), same weather.", met_as_is, 2016),
        ("rain_scaled_to_station", f"Sensitivity: Open-Meteo rain from 2023 scaled by {factor:.2f} so its 2013-2022 annual total "
         "matches the founder's station (planted 2012). Shows how much the result depends on the rain source; not a calibration.",
         met_scaled, 2012),
    ]
    scenarios, runtimes, edits = [], {}, []
    for sid, desc, met, year in scen_defs:
        model = workdir / f"MambangMuda_{sid}.apsimx"
        e = make_daily_copy(founder_apsimx, model, met.name, clock_end=clock_end, sowing_year=year)
        if sid == "as_is":
            edits = e
        rt, _ = run_models(model, home)
        runtimes[sid] = round(rt, 1)
        daily = read_daily(model.with_suffix(".db"))
        if daily[-1]["date"] != all_rows[-1].day.isoformat():
            raise ApsimRunError(f"APSIM output ends {daily[-1]['date']} but weather ends {all_rows[-1].day}")
        sc = _scenario(sid, desc, daily, today, ts)
        sc["palm_age_years_at_today"] = round(next(r["PalmAgeYears"] for r in daily if r["date"] == today.isoformat()), 1)
        scenarios.append(sc)
        if sid == "as_is":
            context = monthly_context(daily, today)
            soil = {"profile_depth_mm": 2000, "ll15_sum_mm": round(daily[0]["LL15MM"], 1),
                    "dul_sum_mm": round(daily[0]["DULMM"], 1), "sat_sum_mm": round(daily[0]["SATMM"], 1),
                    "plant_available_water_mm": round(daily[0]["DULMM"] - daily[0]["LL15MM"], 1),
                    "note": "available water = soil water minus LL15 (crop LL is unset in the founder's file); "
                            "available water is capped at DUL (so the maximum is plant_available_water_mm); water above DUL is shown as water_above_dul_mm"}

    # management-lever scenarios for the cost-benefit view (imported here: management.py imports this module)
    from .management import run_management
    mgmt = run_management(founder_apsimx, workdir, {"as_delivered": met_as_is, "rain_scaled": met_scaled}, clock_end, home)

    # sanity block: the UNMODIFIED founder run (2001-2021, original weather), as the founder set it up
    orig = workdir / "MambangMuda_founder_period.apsimx"
    make_daily_copy(founder_apsimx, orig, founder_met.name)
    (workdir / founder_met.name).write_bytes(founder_met.read_bytes())
    run_models(orig, home)
    annual_hist = read_annual(orig.with_suffix(".db"))

    result = {
        "schema_version": "1",
        "source": "apsimx",
        "status": "INDICATIVE, NOT VALIDATED: APSIM-X process-model output, not calibrated for this site.",
        "supported_point": SITE,
        "engine": apsim_info(home),
        "model_file": {"founder_file_sha256": sha(founder_apsimx), "met_sha256": sha(founder_met),
                       "note": "founder's files are not redistributed; edits below were made to a COPY"},
        "run": {"started_utc": started.isoformat(timespec="seconds"), "runtime_seconds": runtimes,
                "today_utc": today.isoformat(), "clock_start": "planting day (see scenarios; default 2012-01-01)", "clock_end": clock_end[:10],
                "history_days_served": HISTORY_DAYS},
        "weather_inputs": {**winfo, "overlap_check": ov, "rain_scale_factor_sensitivity": round(factor, 3)},
        "edits": edits, "edits_note": "edits for the default scenario; other scenarios differ only in planting year / weather file named in their description", "soil": soil, "limitations": LIMITATIONS,
        "thresholds": {"version": ts.version, "cutoff_source": ts.cutoff_source,
                       "classes": [{"name": c.name, "label": c.label, "min": c.min} for c in ts.classes],
                       "applies_to": "stress_index = 1 - fw (APSIM water-stress factor). Same provisional 0.3/0.5/0.7 cut-offs as the simple bucket, "
                                 "but a DIFFERENT quantity (APSIM supply/demand, not soil depletion): do not compare classes across the two sources"},
        "scenarios": scenarios, "monthly_context_as_is": context, "management_scenarios": mgmt,
        "sanity_check_founder_period": {"note": "annual totals from the founder's own setup (planted 2001, original station weather, 2001-2021) re-run with APSIM "
                                   + (apsim_info(home)["version"] or "?") + "; for plausibility checking only", "annual": [
            {"year": a["y"], "palm_age": round(a["age"], 1), "rain_mm": round(a["rain"]), "eo_mm": round(a["eo"]),
             "et_mm": round(a["et"]), "lai": round(a["lai"], 2), "ffb_t_ha": round(a["ffb"], 1)} for a in annual_hist]},
    }
    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result
