"""Transparent cost-benefit arithmetic on top of precomputed APSIM-X management scenarios.

NOT a financial model: no discounting, no capital cost, no labour, no price forecasting. It answers one narrow
question: "if APSIM-X's yield response were right, and these prices were right, what is the per-hectare yearly
difference between scenario X and the base?". All prices are inputs from the caller. Formulas are returned in the
response so a reader can redo every number by hand.

Per scenario s versus base b, per hectare per year, using the MEAN of the full evaluation years (2023-2025 hindcast):
    d_ffb      = ffb_s - ffb_b                                        [t/ha/yr, APSIM-X]
    d_revenue  = d_ffb * ffb_price                                    [per tonne FFB]
    d_harvest  = d_ffb * harvest_haul_cost                            [per tonne FFB; 0 unless the caller sets it]
    d_fert     = (N_s - N_b) * n_price                                [kg N/ha/yr from APSIM-X x price per kg N]
    d_water    = (irr_s - irr_b) * 10 * water_price                   [mm x 10 m3/ha/mm x price per m3]
    d_fixed    = fixed_s - fixed_b   where fixed = irrigation_fixed_cost if irrigation mm > 0 else 0
    net        = d_revenue - d_harvest - d_fert - d_water - d_fixed   [> 0: scenario better than base for these inputs]
"""
from __future__ import annotations

M3_PER_MM_HA = 10.0
YEARS_KEYS = ("ffb_t_ha", "n_applied_kg_ha", "irrigation_mm", "n_leached_no3_kg_ha", "drainage_mm")

FORMULAS = [
    "d_ffb_t_ha = FFB_scenario - FFB_base   (APSIM-X, mean of the full hindcast years)",
    "d_revenue = d_ffb_t_ha * ffb_price_per_tonne",
    "d_extra_harvest_haul_cost = d_ffb_t_ha * harvest_haul_cost_per_tonne   (0 unless you set it)",
    "d_fertiliser_cost = (N_applied_scenario - N_applied_base) kg N/ha * n_price_per_kg",
    "d_water_cost = (irrigation_mm_scenario - irrigation_mm_base) * 10 m3 per mm per ha * water_price_per_m3",
    "d_irrigation_fixed_cost = fixed cost if the scenario irrigates (mm > 0) minus the same for the base",
    "net_difference = d_revenue - d_extra_harvest_haul_cost - d_fertiliser_cost - d_water_cost - d_irrigation_fixed_cost",
    "break_even_ffb_price = harvest_haul_cost + (d_fert + d_water + d_fixed) / d_ffb   (price at which net = 0; undefined when d_ffb = 0)",
    "break_even_yield_factor f: net(f) = f * d_ffb * (ffb_price - harvest_haul_cost) - (d_fert + d_water + d_fixed) = 0  ->  "
    "f = (d_fert + d_water + d_fixed) / (d_ffb * (ffb_price - harvest_haul_cost)); f = 1 means APSIM-X's response as simulated",
    "sensitivity 'unfavourable': FFB price x (1 - p), all cost prices x (1 + p); 'favourable': the reverse; p = sensitivity_pct/100",
    "yield what-if: d_ffb multiplied by (1 - w), 1, (1 + w) with w = yield_whatif_pct/100; an illustration of a wrong response, NOT an error estimate",
]

LIMITATIONS = [
    "APSIM-X yield response is UNVALIDATED for this site: the founder's model has no Sumatra calibration, "
    "no comparison with measured FFB, and only N (no P/K) is modelled. Treat deltas as model behaviour, not as measured response.",
    "Palm age is ASSUMED (planted 2012-01-01 in the model copy; the founder's 2001 planting breaks the model beyond about 25 years).",
    "Weather 2023-2025 is a hindcast from Open-Meteo (about +35% more rain than the founder's station in the overlap years); "
    "the rain_scaled variant shows how much the answer depends on that. It is not a forecast of future seasons.",
    "The yield response is not instantaneous in the model: with N 1.5x the first year (2023) differs from base by about 0 t/ha and the "
    "gain appears in 2024-2025, so a 3-year mean mixes a lag period with the response (cause, probably bunch development time, not verified). "
    "The per-year table shows this; a longer window was not run.",
    "One point only (Mambang Muda), one soil, one management schedule hard-coded in the founder's manager (rates by palm age, 14-day harvest).",
    "Differences of about 1 t FFB/ha over three years or less are within what harvest timing and small model changes alone produce "
    "(for example splitting the same N into 6 doses changes 3-year FFB by about 1%); do not read them as real effects.",
    "Prices are your inputs. Defaults are placeholders ('example, replace with your costs'), not market data. "
    "Not counted: capital and installation cost, labour, N application cost, discounting, price risk, other nutrients, "
    "environmental value of reduced leaching.",
    "No savings, profit or return-on-investment claim is made or implied. 'net_difference' is a per-hectare yearly "
    "difference between two model scenarios, nothing more.",
]


def _mean_years(sc: dict) -> dict:
    ys = sc["full_years"]
    n = len(ys)
    out = {k: sum(y[k] for y in ys) / n for k in YEARS_KEYS}
    out["mean_fw"] = sum(y["mean_fw"] for y in ys) / n
    out["days_fw_below_0_95"] = sum(y["days_fw_below_0_95"] for y in ys) / n
    return out


def _net(d_ffb: float, d_fert_units: float, d_water_units: float, fixed_s: float, fixed_b: float, p: dict) -> dict:
    d_rev = d_ffb * p["ffb_price_per_tonne"]
    d_harv = d_ffb * p["harvest_haul_cost_per_tonne"]
    d_fert = d_fert_units * p["n_price_per_kg"]
    d_water = d_water_units * M3_PER_MM_HA * p["water_price_per_m3"]
    d_fixed = fixed_s - fixed_b
    return {"d_revenue": d_rev, "d_extra_harvest_haul_cost": d_harv, "d_fertiliser_cost": d_fert, "d_water_cost": d_water,
            "d_irrigation_fixed_cost": d_fixed, "net_difference": d_rev - d_harv - d_fert - d_water - d_fixed}


def _scaled(p: dict, ffb_f: float, cost_f: float) -> dict:
    q = dict(p)
    q["ffb_price_per_tonne"] = p["ffb_price_per_tonne"] * ffb_f
    for k in ("n_price_per_kg", "water_price_per_m3", "irrigation_fixed_cost_per_ha_year", "harvest_haul_cost_per_tonne"):
        q[k] = p[k] * cost_f
    return q


def _r(x: float | None, nd: int = 2) -> float | None:
    return None if x is None else round(x, nd)


def evaluate(variant_block: dict, prices: dict, sensitivity_pct: float, yield_whatif_pct: float,
             other_variants: dict[str, dict] | None = None) -> list[dict]:
    """variant_block: results['management_scenarios']['variants'][name]. prices: dict of the five numeric inputs.
    Returns one dict per NON-base scenario, plus the base first (with absolute numbers only)."""
    scs = variant_block["scenarios"]
    base = next(s for s in scs if s["is_base"])
    mb = _mean_years(base)
    p = sensitivity_pct / 100.0
    w = yield_whatif_pct / 100.0
    rows = []
    for s in scs:
        m = _mean_years(s)
        row = {"id": s["id"], "label": s["label"], "description": s["description"], "is_base": s["is_base"], "params": s["params"],
               "absolute_mean_per_year": {
                   "ffb_t_ha": _r(m["ffb_t_ha"], 5), "n_applied_kg_ha": _r(m["n_applied_kg_ha"], 1),
                   "irrigation_mm": _r(m["irrigation_mm"], 1), "n_leached_no3_kg_ha": _r(m["n_leached_no3_kg_ha"], 1),
                   "drainage_mm": _r(m["drainage_mm"], 0), "mean_fw": _r(m["mean_fw"], 4),
                   "days_fw_below_0_95": _r(m["days_fw_below_0_95"], 1)}}
        if s["is_base"]:
            rows.append(row)
            continue
        d_ffb = m["ffb_t_ha"] - mb["ffb_t_ha"]
        d_n = m["n_applied_kg_ha"] - mb["n_applied_kg_ha"]
        d_irr = m["irrigation_mm"] - mb["irrigation_mm"]
        fixed = lambda mm: prices["irrigation_fixed_cost_per_ha_year"] if mm > 0 else 0.0
        fs, fb = fixed(m["irrigation_mm"]), fixed(mb["irrigation_mm"])
        money = _net(d_ffb, d_n, d_irr, fs, fb, prices)
        costs = money["d_fertiliser_cost"] + money["d_water_cost"] + money["d_irrigation_fixed_cost"]
        be_price, be_factor = None, None
        if abs(costs) < 1e-9:
            be_note = "no cost difference: the sign of d_ffb alone decides, no break-even exists"
        elif abs(d_ffb) < 1e-9:
            be_note = "d_ffb is 0 while costs differ: no yield response to break even on"
        else:
            cand_price = prices["harvest_haul_cost_per_tonne"] + costs / d_ffb
            denom = d_ffb * (prices["ffb_price_per_tonne"] - prices["harvest_haul_cost_per_tonne"])
            cand_factor = None if abs(denom) < 1e-9 else costs / denom
            if cand_price <= 0:
                be_note = ("no positive FFB price makes net zero: better than base at every price (more yield and lower cost)"
                           if d_ffb > 0 else
                           "no positive FFB price makes net zero: worse than base at every price (less yield and higher cost)")
            else:
                be_price = cand_price
                be_note = ("scenario better than base when the FFB price is ABOVE the break-even price" if d_ffb > 0
                           else "scenario better than base when the FFB price is BELOW the break-even price (it loses yield but saves cost)")
            if cand_factor is not None and cand_factor > 0:
                be_factor = cand_factor
                be_note += ("; and better than base if the real yield gain is at least " if d_ffb > 0
                            else "; and better than base if the real yield loss is smaller than ") + \
                    f"{cand_factor:.3g} x the simulated change (at the ffb price given)"
            else:
                be_note += "; no positive yield-response factor gives net zero"
        sens = []
        for name, ffb_f, cost_f in (("base prices", 1.0, 1.0), ("FFB price low", 1 - p, 1.0), ("FFB price high", 1 + p, 1.0),
                                    ("cost prices high", 1.0, 1 + p), ("cost prices low", 1.0, 1 - p),
                                    ("unfavourable (FFB low, costs high)", 1 - p, 1 + p),
                                    ("favourable (FFB high, costs low)", 1 + p, 1 - p)):
            q = _scaled(prices, ffb_f, cost_f)
            sens.append({"case": name, "net_difference": _r(_net(d_ffb, d_n, d_irr, fixed_q(q, m), fixed_q(q, mb), q)["net_difference"])})
        whatif = []
        for f in (1 - w, 1.0, 1 + w):
            whatif.append({"yield_response_factor": _r(f, 3), "d_ffb_t_ha": _r(d_ffb * f, 3),
                           "net_difference": _r(_net(d_ffb * f, d_n, d_irr, fs, fb, prices)["net_difference"])})
        per_year = []
        for ys, yb in zip(s["full_years"], base["full_years"]):
            mny = _net(ys["ffb_t_ha"] - yb["ffb_t_ha"], ys["n_applied_kg_ha"] - yb["n_applied_kg_ha"],
                       ys["irrigation_mm"] - yb["irrigation_mm"], fixed(ys["irrigation_mm"]), fixed(yb["irrigation_mm"]), prices)
            per_year.append({"year": ys["year"], "d_ffb_t_ha": _r(ys["ffb_t_ha"] - yb["ffb_t_ha"], 5),
                             "d_n_applied_kg_ha": _r(ys["n_applied_kg_ha"] - yb["n_applied_kg_ha"], 3),
                             "d_irrigation_mm": _r(ys["irrigation_mm"] - yb["irrigation_mm"], 3),
                             "net_difference": _r(mny["net_difference"])})
        by_variant = {}
        for vname, vb in (other_variants or {}).items():
            os_ = next((x for x in vb["scenarios"] if x["id"] == s["id"]), None)
            ob = next((x for x in vb["scenarios"] if x["is_base"]), None)
            if os_ is None or ob is None:
                continue
            mo, mob = _mean_years(os_), _mean_years(ob)
            by_variant[vname] = {
                "d_ffb_t_ha": _r(mo["ffb_t_ha"] - mob["ffb_t_ha"], 5),
                "d_n_applied_kg_ha": _r(mo["n_applied_kg_ha"] - mob["n_applied_kg_ha"], 3),
                "d_irrigation_mm": _r(mo["irrigation_mm"] - mob["irrigation_mm"], 3),
                "net_difference": _r(_net(mo["ffb_t_ha"] - mob["ffb_t_ha"], mo["n_applied_kg_ha"] - mob["n_applied_kg_ha"],
                                          mo["irrigation_mm"] - mob["irrigation_mm"], fixed(mo["irrigation_mm"]),
                                          fixed(mob["irrigation_mm"]), prices)["net_difference"])}
        row.update({
            "delta_vs_base_per_ha_year": {
                "d_ffb_t_ha": _r(d_ffb, 5), "d_n_applied_kg_ha": _r(d_n, 3), "d_irrigation_mm": _r(d_irr, 3),
                "d_irrigation_m3_ha": _r(d_irr * M3_PER_MM_HA, 0),
                "d_n_leached_no3_kg_ha": _r(m["n_leached_no3_kg_ha"] - mb["n_leached_no3_kg_ha"], 1),
                "d_drainage_mm": _r(m["drainage_mm"] - mb["drainage_mm"], 0),
                "d_mean_fw": _r(m["mean_fw"] - mb["mean_fw"], 4),
                "d_days_fw_below_0_95": _r(m["days_fw_below_0_95"] - mb["days_fw_below_0_95"], 1)},
            "money_vs_base_per_ha_year": {k: _r(v) for k, v in money.items()},
            "sensitivity": sens, "yield_whatif": whatif,
            "break_even": {"ffb_price_per_tonne": _r(be_price), "yield_response_factor": _r(be_factor, 3), "reading": be_note},
            "per_year_hindcast": per_year,
            "net_difference_by_weather_variant": by_variant,
        })
        rows.append(row)
    return rows


def fixed_q(q: dict, m: dict) -> float:
    return q["irrigation_fixed_cost_per_ha_year"] if m["irrigation_mm"] > 0 else 0.0


# ---------------------------------------------------------------------------------------------
# example placeholders: used ONLY when the caller does not send the input, and flagged in the response
# ---------------------------------------------------------------------------------------------
EXAMPLE_DEFAULTS = {
    "ffb_price_per_tonne": (100.0, "currency per tonne FFB", "price you receive for fresh fruit bunches"),
    "n_price_per_kg": (1.0, "currency per kg N", "cost of fertiliser per kg of nitrogen (product price per kg / N fraction; e.g. urea is 46% N)"),
    "water_price_per_m3": (0.1, "currency per m3", "cost of irrigation water per m3 (1 mm over 1 ha = 10 m3)"),
    "irrigation_fixed_cost_per_ha_year": (0.0, "currency per ha per year", "irrigation operating cost that applies only when the scenario irrigates at all (0 = not counted)"),
    "harvest_haul_cost_per_tonne": (0.0, "currency per tonne FFB", "extra harvest/transport cost per extra tonne of FFB (0 = not counted)"),
}


def build_response(data: dict, age_hours: float, supplied: dict, weather: str, sensitivity_pct: float,
                   yield_whatif_pct: float, currency: str, generated_at: str, stale_after_hours: float) -> dict:
    """data = the precomputed APSIM result file (dict). supplied = {input name: value or None}."""
    mg = data["management_scenarios"]
    prices, inputs = {}, []
    for name, (dflt, unit, note) in EXAMPLE_DEFAULTS.items():
        v = supplied.get(name)
        prices[name] = float(dflt if v is None else v)
        inputs.append({"name": name, "value": prices[name], "unit": unit.replace("currency", currency),
                       "is_example_default": v is None, "note": note})
    variants = mg["variants"]
    others = {k: v for k, v in variants.items() if k != weather}
    rows = evaluate(variants[weather], prices, sensitivity_pct, yield_whatif_pct, others)
    partial = {s["id"]: s["partial_year"] for s in variants[weather]["scenarios"] if s.get("partial_year")}
    sp = data["supported_point"]
    return {
        "meta": {"physical_numbers_source": "apsimx", "economics_source": "user-inputs",
                 "status": data.get("status", "INDICATIVE, NOT VALIDATED"), "generated_at": generated_at, "precomputed": True,
                 "supported_point": sp,
                 "note": f"APSIM scenario numbers exist only for {sp['name']} and are precomputed; prices are yours; indicative, not validated; no savings or ROI claim."},
        "engine": data["engine"],
        "run": {**{k: data["run"][k] for k in ("started_utc", "runtime_seconds", "today_utc", "clock_end", "clock_start", "history_days_served")},
                "age_hours": age_hours, "result_stale": age_hours > stale_after_hours},
        "currency": currency, "weather_variant": weather, "weather_variants": mg["weather_variants"],
        "inputs": inputs, "sensitivity_pct": sensitivity_pct, "yield_whatif_pct": yield_whatif_pct,
        "evaluation": mg["evaluation"], "levers": mg["levers"], "units": mg["units"], "formulas": FORMULAS,
        "scenarios": rows, "partial_year": partial, "limitations": LIMITATIONS,
    }
