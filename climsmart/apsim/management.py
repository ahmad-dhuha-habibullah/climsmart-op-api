"""Management-lever scenario runs (fertiliser N, N split, rule-based irrigation) with APSIM-X.

Every number produced here is APSIM-X output (source='apsimx' + engine version) for ONE point (Mambang Muda),
an ASSUMED 2012 planting year, and the founder's unvalidated model. Nothing here is economics: money is computed
later, on request, by climsmart.costbenefit from user-supplied prices.

Scenario management applies from MANAGEMENT_FROM_YEAR (1 Jan 2023) on; before that every scenario is the founder's
own management, so all scenarios share the same palm/soil starting state at the start of the evaluation window.
"""
from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from .model_copy import add_irrigation_rule, apply_n_levers, make_daily_copy
from .pipeline import DEFAULT_HOME, read_daily, run_models

MANAGEMENT_FROM_YEAR = 2023
FULL_YEARS = (2023, 2024, 2025)
SOWING_YEAR = 2012          # ASSUMPTION, same as the default outlook scenario
FW_STRESS_DAYS_BELOW = 0.95
FW_NOTE = "fw = APSIM palm water-stress factor (EP/PEP), 1 = no stress"


@dataclass(frozen=True)
class MgmtScenario:
    id: str
    label: str
    description: str
    n_multiplier: float = 1.0
    n_applications: int = 3
    irrigation: dict | None = None      # {trigger_fw, amount_mm, min_days}
    is_base: bool = False


SCENARIOS: list[MgmtScenario] = [
    MgmtScenario("base", "Base (founder's management)",
                 "Founder's N schedule unchanged (about 158.8 kg N/ha/yr at maturity, 3 doses 15 Jan/May/Sep), no irrigation.",
                 is_base=True),
    MgmtScenario("n_0_5x", "N fertiliser 0.5x", "Half the founder's N rate from 2023, same dates, no irrigation.", n_multiplier=0.5),
    MgmtScenario("n_1_5x", "N fertiliser 1.5x", "1.5 times the founder's N rate from 2023, same dates, no irrigation.", n_multiplier=1.5),
    MgmtScenario("n_split_6x", "Same N, 6 doses/yr",
                 "Founder's annual N rate split into 6 equal doses (15 Jan/Mar/May/Jul/Sep/Nov) instead of 3, no irrigation. "
                 "The only leaching-related lever the model offers besides the N rate.", n_applications=6),
    MgmtScenario("irrigate_fw_lt_0_9", "Water top-up when fw < 0.9",
                 "Founder's N. Rule: if yesterday's fw < 0.9 and 3+ days since the last top-up, apply 10 mm.",
                 irrigation={"trigger_fw": 0.9, "amount_mm": 10.0, "min_days": 3}),
    MgmtScenario("irrigate_fw_lt_0_8", "Water top-up when fw < 0.8",
                 "Founder's N. Rule: if yesterday's fw < 0.8 and 3+ days since the last top-up, apply 10 mm.",
                 irrigation={"trigger_fw": 0.8, "amount_mm": 10.0, "min_days": 3}),
]

LEVERS = {
    "exist_and_varied_here": [
        {"lever": "N fertiliser rate", "how": "multiplier on every N rate in the founder's 'Palm Management' manager code, "
         "from 2023 (the founder's code sets rates by palm age; amounts are hard-coded there)"},
        {"lever": "N fertiliser timing/splitting", "how": "3 doses (15 Jan/May/Sep, founder) vs 6 equal doses (15 Jan/Mar/May/Jul/Sep/Nov)"},
        {"lever": "Irrigation (water supplement)", "how": "the founder's file contains an Irrigation model but nothing applies water; "
         "we ADD a rule-based manager (trigger on fw, fixed mm, minimum days between)"},
    ],
    "exist_but_not_varied": [
        "N form / NO3:NH4 split (hard-coded 50:50)",
        "harvest interval (hard-coded 14 days)",
    ],
    "do_not_exist": [
        "phosphorus and potassium fertiliser (the model simulates N only)",
        "a dedicated leaching-prevention practice (cover crop, slow-release N, drainage control): leaching is only an OUTPUT; "
        "the only levers that influence it are the N rate and N splitting above",
        "irrigation scheduling beyond the simple rule we add (no soil-moisture sensors, no forecast-driven rule)",
    ],
}

UNITS = {
    "ffb_t_ha_yr": "t fresh fruit bunch per ha per calendar year: sum of the model's per-harvest 'HarvestFFB' ([Units(\"t/ha\")], "
                   "from the OilPalm model source) added up by the founder's manager as Calculations.Script.AnnualYield "
                   "(report column 'Calculations.Script.AnnualYield' in AnnualOutput)",
    "n_applied_kg_ha": "kg N per ha (sum of [Fertiliser].NitrogenApplied)",
    "n_leached_no3_kg_ha": "kg NO3-N per ha leached below the 2 m profile (sum of [Soil].SoilWater.LeachNO3: 'amount of N leaching as NO3-N from the deepest soil layer')",
    "irrigation_mm": "mm applied (sum of [Irrigation].IrrigationApplied); 1 mm over 1 ha = 10 m3",
    "drainage_mm": "mm drained below the profile (sum of [Soil].SoilWater.Drainage)",
}


def _annual_extra(db: Path) -> dict[int, dict]:
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    rows = c.execute('select "Clock.Today.Year" y, AnnualDenitrification dn, AnnualN2O n2o, AnnualPalmNUptake up, '
                     '"Calculations.Script.AnnualYield" ffb, "Calculations.Script.AnnualBunches" bunches from AnnualOutput').fetchall()
    c.close()
    return {int(r["y"]): dict(r) for r in rows}


def _year_stats(daily: list[dict], year: int, extra: dict | None, partial: bool) -> dict:
    rows = [r for r in daily if r["date"].startswith(str(year))]
    n = len(rows)
    fw = [r["FW"] for r in rows]
    d = {
        "year": year, "days": n, "partial": partial,
        "first_day": rows[0]["date"], "last_day": rows[-1]["date"],
        "ffb_t_ha": round(rows[-1]["YTDYieldFFBtHa"], 3),
        "n_applied_kg_ha": round(sum(r["FertNKgHa"] for r in rows), 2),
        "n_leached_no3_kg_ha": round(sum(r["LeachNO3KgHa"] for r in rows), 2),
        "irrigation_mm": round(sum(r["IrrigationMM"] for r in rows), 1),
        "drainage_mm": round(sum(r["DrainageMM"] for r in rows), 0),
        "rain_mm": round(sum(r["RainMM"] for r in rows), 0),
        "mean_fw": round(sum(fw) / n, 4), "min_fw": round(min(fw), 3),
        "days_fw_below_0_95": sum(1 for x in fw if x < FW_STRESS_DAYS_BELOW),
    }
    if extra and not partial:
        d["denitrification_kg_n_ha"] = round(extra["dn"], 2)
        d["n2o_kg_n_ha"] = round(extra["n2o"], 2)
        if abs(extra["ffb"] - d["ffb_t_ha"]) > 1e-3:
            raise ValueError(f"FFB mismatch daily vs annual report {year}: {extra['ffb']} vs {d['ffb_t_ha']}")
    return d


def _sum_years(years: list[dict]) -> dict:
    keys = ["ffb_t_ha", "n_applied_kg_ha", "n_leached_no3_kg_ha", "irrigation_mm", "drainage_mm"]
    out = {k: round(sum(y[k] for y in years), 3) for k in keys}
    out["mean_fw"] = round(sum(y["mean_fw"] * y["days"] for y in years) / sum(y["days"] for y in years), 4)
    out["days_fw_below_0_95"] = sum(y["days_fw_below_0_95"] for y in years)
    return out


def _run_one(args) -> tuple[str, str, dict, list[str], float]:
    variant, sc, founder, workdir, met_name, clock_end, home = args

    def mutate(model: dict) -> list[str]:
        e = apply_n_levers(model, sc.n_multiplier, MANAGEMENT_FROM_YEAR, sc.n_applications)
        if sc.irrigation:
            e += add_irrigation_rule(model, MANAGEMENT_FROM_YEAR, sc.irrigation["trigger_fw"],
                                     sc.irrigation["amount_mm"], sc.irrigation["min_days"])
        return e

    model = workdir / f"mgmt_{variant}_{sc.id}.apsimx"
    edits = make_daily_copy(founder, model, met_name, clock_end=clock_end, sowing_year=SOWING_YEAR, mutate=mutate)
    rt, _ = run_models(model, home)
    db = model.with_suffix(".db")
    daily = read_daily(db)
    extra = _annual_extra(db)
    years = [_year_stats(daily, y, extra.get(y), False) for y in FULL_YEARS]
    last_year = int(daily[-1]["date"][:4])
    ytd = _year_stats(daily, last_year, None, True) if last_year > FULL_YEARS[-1] else None
    res = {
        "id": sc.id, "label": sc.label, "description": sc.description, "is_base": sc.is_base,
        "params": {"n_multiplier": sc.n_multiplier, "n_applications_per_year": sc.n_applications,
                   "irrigation_rule": sc.irrigation, "management_from_year": MANAGEMENT_FROM_YEAR},
        "full_years": years, "full_years_total": _sum_years(years),
        "partial_year": ytd,
    }
    return variant, sc.id, res, edits, rt


def run_management(founder: Path, workdir: Path, met_files: dict[str, Path], clock_end: str,
                   home: Path = DEFAULT_HOME, workers: int = 4) -> dict:
    """met_files: {'as_delivered': path, 'rain_scaled': path} -> the 'management_scenarios' result block."""
    jobs = [(v, sc, founder, workdir, m.name, clock_end, home) for v, m in met_files.items() for sc in SCENARIOS]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        out = list(ex.map(_run_one, jobs))
    variants: dict[str, dict] = {v: {"scenarios": []} for v in met_files}
    edits_by_id: dict[str, list[str]] = {}
    runtimes: dict[str, float] = {}
    for variant, sid, res, edits, rt in out:
        variants[variant]["scenarios"].append(res)
        edits_by_id[sid] = edits
        runtimes[f"{variant}/{sid}"] = round(rt, 1)
    for v in variants.values():
        v["scenarios"].sort(key=lambda r: [s.id for s in SCENARIOS].index(r["id"]))
    return {
        "evaluation": {
            "management_from": f"{MANAGEMENT_FROM_YEAR}-01-01",
            "full_years": list(FULL_YEARS),
            "partial_year": "year after the last full year, up to the last day of the weather file (includes forecast days); NOT a full year, do not annualise",
            "weather_note": "2023-2025 weather is a HINDCAST (Open-Meteo archive, grid cell near the site); it is not a forecast of future seasons. "
                            "Annual FFB needs a full year, which is why a hindcast window is used.",
            "assumed_planting_year": SOWING_YEAR,
            "planting_year_note": "ASSUMED (founder's 2001 planting breaks the model beyond ~25 y). Palm age in 2023-2025 is about 11-14 y under this assumption.",
        },
        "fw_definition": FW_NOTE,
        "levers": LEVERS, "units": UNITS,
        "edits_by_scenario": edits_by_id,
        "weather_variants": {
            "as_delivered": "Open-Meteo rain as delivered (about +35% vs the founder's station in the 2013-2022 overlap)",
            "rain_scaled": "Open-Meteo rain from 2023 multiplied by the station/Open-Meteo ratio of the overlap years (sensitivity, not a calibration)",
        },
        "runtime_seconds": runtimes,
        "variants": variants,
    }
