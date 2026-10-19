"""Distil the APSIM-X output `.db` files (SQLite) into one compact, labelled series file for plotting.

Offline only (scripts/extract_apsim_series.py). The API serves the saved JSON; it never opens a `.db` or runs APSIM.
Every number is read from the `.db` with fixed SELECTs on an allowlist of report tables (opened read-only);
nothing is computed except rounding and the unit label. Column meanings and units are APSIM's (`_Units` table)
except where `UNIT_NOTES` says the `.db` does not state them.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
STATUS = "INDICATIVE, NOT VALIDATED: APSIM-X process-model output, not calibrated for this site."

# series name -> (table, column, unit, decimals)
ANNUAL = {
    "ffb_t_ha": ("AnnualOutput", "Calculations.Script.AnnualYield", "t/ha/yr", 3),
    "lai": ("AnnualOutput", "OilPalm.LAI", "m2/m2", 3),
    "palm_age_y": ("AnnualOutput", "OilPalm.Age", "y", 2),
    "rain_mm": ("AnnualOutput", "AnnualRain", "mm/yr", 1),
    "n_applied_kg_ha": ("AnnualOutput", "AnnualFertiliserNAdded", "kg/ha/yr", 1),
    "n_leached_kg_ha": ("AnnualOutput", "AnnualNLeaching", "kg/ha/yr", 2),
    "irrigation_mm": ("AnnualOutput", "AnnualIrrigation", "mm/yr", 1),
    "palm_n_uptake_kg_ha": ("AnnualOutput", "AnnualPalmNUptake", "kg/ha/yr", 1),
}
MONTHLY = {
    "lai": ("MonthlyOutput", "OilPalm.LAI", "m2/m2", 3),
    "soil_water_mm": ("MonthlyOutput", "TotalSW", "mm", 1),
    "soil_nitrate_kg_ha": ("MonthlyOutput", "TotalNO3", "kg/ha", 1),
    "stem_mass_g_m2": ("MonthlyOutput", "OilPalm.StemMass", "g/m2", 0),
}
UNIT_NOTES = {
    "ffb_t_ha": "Fresh fruit bunch yield, t per ha per calendar year: the founder's manager sums the model's per-harvest HarvestFFB into Calculations.Script.AnnualYield.",
    "soil_water_mm": "Total soil water over the 2 m profile on the 1st of the month. It includes water below wilting point (sum of LL15 = 321.4 mm); field capacity (DUL) 546.9 mm, saturation 908.3 mm.",
    "soil_nitrate_kg_ha": "Total nitrate-N in the profile on the 1st of the month; the .db's _Units table does not state the unit, kg/ha is APSIM's usual unit for TotalNO3 (HYPOTHESIS).",
    "n_leached_kg_ha": "Nitrate-N leached below the profile, per year; unit not stated in _Units, kg/ha per the model source (HYPOTHESIS).",
    "stem_mass_g_m2": "Palm stem dry mass, a proxy for biomass (APSIM _Units: g/m^2). Other pools are not reported by the founder's file.",
}
# (id, group, db file, label)
PLANTING = [("as_is", "planting_year", "MambangMuda_as_is.db"),
            ("older_palms_2008", "planting_year", "MambangMuda_older_palms_2008.db"),
            ("younger_palms_2016", "planting_year", "MambangMuda_younger_palms_2016.db")]
MGMT_GROUP = {"as_delivered": "management_as_delivered", "rain_scaled": "management_rain_scaled"}


def _ro(path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    c.execute("PRAGMA query_only=ON")
    return c


def _q(c: sqlite3.Connection, table: str, cols: list[str], order: str) -> list[tuple]:
    assert table in ("AnnualOutput", "MonthlyOutput", "DailyOutput")  # allowlist; column names come from the constants above
    sel = ", ".join(f'"{x}"' for x in cols)
    return c.execute(f'select {sel} from {table} order by "{order}"').fetchall()


def read_scenario(db: Path) -> dict:
    c = _ro(db)
    try:
        a_cols = ["Clock.Today.Year"] + [v[1] for v in ANNUAL.values()]
        rows = _q(c, "AnnualOutput", a_cols, "Clock.Today.Year")
        annual = {"year": [int(r[0]) for r in rows]}
        for i, (k, (_, _, _, nd)) in enumerate(ANNUAL.items(), start=1):
            annual[k] = [None if r[i] is None else round(float(r[i]), nd) for r in rows]
        m_cols = ["Clock.Today"] + [v[1] for v in MONTHLY.values()]
        rows = _q(c, "MonthlyOutput", m_cols, "Clock.Today")
        monthly = {"month": [str(r[0])[:7] for r in rows]}
        for i, (k, (_, _, _, nd)) in enumerate(MONTHLY.items(), start=1):
            monthly[k] = [None if r[i] is None else round(float(r[i]), nd) for r in rows]
        last = c.execute('select "Clock.Today","YTDYieldFFBtHa" from DailyOutput order by "Clock.Today" desc limit 1').fetchone()
        ytd = None
        if last:
            day = str(last[0])[:10]
            ytd = {"year": int(day[:4]), "through": day, "ffb_t_ha_to_date": round(float(last[1]), 3), "partial": True}
            if ytd["year"] in annual["year"]:  # the year was completed
                ytd = None
        return {"annual": annual, "monthly": monthly, "partial_year": ytd}
    finally:
        c.close()


def build_series(work_dir: Path, results: dict) -> dict:
    """work_dir holds the .db files written by the run that produced `results` (mambang_muda_latest.json content)."""
    scenarios = []
    desc = {s["id"]: s for s in results["scenarios"]}
    for sid, group, fname in PLANTING:
        d = read_scenario(work_dir / fname)
        s = desc[sid]
        scenarios.append({"id": sid, "group": group, "label": {"as_is": "As-is (planted 2012, assumed)", "older_palms_2008": "Older palms (planted 2008)",
                                                              "younger_palms_2016": "Younger palms (planted 2016)"}[sid],
                          "description": s["description"], "weather_variant": "as_delivered", "is_base": sid == "as_is",
                          "params": None, "db_file": fname, **d})
    for wv, group in MGMT_GROUP.items():
        for ms in results["management_scenarios"]["variants"][wv]["scenarios"]:
            fname = f"mgmt_{wv}_{ms['id']}.db"
            d = read_scenario(work_dir / fname)
            scenarios.append({"id": f"{wv}__{ms['id']}", "group": group, "label": ms["label"], "description": ms["description"],
                              "weather_variant": wv, "is_base": bool(ms.get("is_base")), "params": ms["params"], "db_file": fname, **d})
    run = results["run"]
    return {
        "schema_version": SCHEMA_VERSION,
        "source": "apsimx",
        "status": STATUS,
        "supported_point": results["supported_point"],
        "engine": {k: results["engine"].get(k) for k in ("name", "version", "build_date", "docker_image_tag", "docker_image_digest")},
        "run": {"started_utc": run["started_utc"], "today_utc": run["today_utc"], "clock_end": run["clock_end"],
                "extracted_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "from_rerun": False,
                "note": "Series are read from the .db files of the run recorded in mambang_muda_latest.json (same run, not a new one)."},
        "weather": {"founder_history": f"{results['weather_inputs']['founder_history_first']}..{results['weather_inputs']['founder_history_last']} (nearby station, source undocumented)",
                    "appended_from": results["weather_inputs"]["appended_first"],
                    "open_meteo": f"archive to {results['weather_inputs']['open_meteo']['archive_last']}, then forecast to {results['weather_inputs']['last_day']}",
                    "note": "Years 2023-2025 are an Open-Meteo HINDCAST; the last partial year includes forecast days. Rain in rain_scaled scenarios is multiplied by the station/Open-Meteo ratio from 2023."},
        "assumptions": {"assumed_planting_year": 2012, "planting_note": "ASSUMED: palms planted 2012-01-01 (about 14.8 y old in Oct 2026); real block ages are unknown.",
                        "management_from_year": 2023, "management_note": "Management scenarios differ from the base only from 2023; earlier years are identical."},
        "units": {"annual": {k: v[2] for k, v in ANNUAL.items()}, "monthly": {k: v[2] for k, v in MONTHLY.items()}},
        "unit_notes": UNIT_NOTES,
        "series_sources": {"annual": "AnnualOutput report (EndOfYear)", "monthly": "MonthlyOutput report (StartOfMonth: value on the 1st)",
                           "partial_year": "last row of DailyOutput.YTDYieldFFBtHa"},
        "soil": results.get("soil"),
        "limitations": ["INDICATIVE, NOT VALIDATED: the oil-palm model was not calibrated or checked against observations for Sumatra; yield here is a MODELLED value for comparing scenarios, not a forecast of harvests.",
                        "Only the Mambang Muda point is supported.",
                        "Scenario differences are model sensitivities (N rate x0.5 / x1.5, 3 vs 6 doses, irrigation rules), not recommendations.",
                        "Annual values exist for full years only (2012-2025); the 2026 partial year is given as yield to date and must not be annualised."],
        "scenarios": scenarios,
    }


def write_series(work_dir: Path, results_path: Path, out: Path) -> dict:
    results = json.loads(results_path.read_text(encoding="utf-8"))
    data = build_series(work_dir, results)
    out.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    return data
