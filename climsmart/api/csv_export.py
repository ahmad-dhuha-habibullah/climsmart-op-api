"""CSV rendering of the outlook tables. Every file starts with '#' comment lines (source, status,
parameters, limitations) and every data row carries `source` and `source_version` columns, so a row that is
copied out of the file still says where it came from. Comment lines can be skipped with comment="#"."""
from __future__ import annotations

import csv
import io

from . import schemas as s

OUTLOOK_COLUMNS = ["source", "source_version", "date", "kind", "category", "label", "deficit_mm",
                   "deficit_fraction", "stress_ratio", "rain_mm"]
APSIM_COLUMNS = ["source", "source_version", "scenario", "date", "kind", "category", "label", "rain_mm", "eo_mm",
                 "et_mm", "es_mm", "ep_mm", "potential_ep_mm", "lai", "fw", "stress_index", "understory_fw",
                 "soil_water_mm", "available_water_mm", "water_above_dul_mm", "available_fraction",
                 "depletion_fraction", "runoff_mm", "drainage_mm"]


def _c(text: object) -> str:
    return "# " + " ".join(str(text).split())  # one line per comment, no stray newlines


def _render(comments: list[str], columns: list[str], rows: list[list]) -> str:
    buf = io.StringIO()
    for c in comments:
        buf.write(_c(c) + "\n")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(columns)
    w.writerows(rows)
    return buf.getvalue()


def outlook_csv(o: s.OutlookResponse) -> str:
    m = o.meta
    eng = m.model
    ver = f"{eng.name} {eng.version}" if eng else "unknown"
    src = f"simple-water-balance"
    p = o.parameters
    comments = [
        "ClimSmart-OP daily outlook, simple water balance (NOT an APSIM crop-model result)",
        f"source: {src} ({ver})",
        f"status: {m.status}",
        "INDICATIVE, NOT VALIDATED: model-derived from gridded weather data; not calibrated to field data; not a yield forecast",
        f"location: lat {o.location.latitude}, lon {o.location.longitude}, timezone {o.location.timezone}",
        f"parameters: capacity {p.root_zone_capacity_mm} mm, Kc {p.kc}, depletion fraction p {p.depletion_fraction_p}, "
        f"spin-up {p.spinup_days} days, overridden by caller: {', '.join(p.overridden) or 'none'}",
        f"class cut-offs ({', '.join(str(c.min) for c in o.thresholds.classes[1:])} of capacity): {o.thresholds.cutoff_source}",
        f"category is the class of deficit_fraction; thresholds version {o.thresholds.version} ({o.thresholds.status})",
        f"weather: {'; '.join(x.attribution + ' - ' + x.license_note for x in m.sources)}",
        f"weather_stale: {bool(m.weather_stale)}",
        f"generated_at: {m.generated_at}",
        "kind: past = recent analysis, forecast = predicted",
    ]
    rows = [[src, ver, d.date.isoformat(), d.kind, d.category, d.label, d.deficit_mm, d.deficit_fraction,
             d.stress_ratio, d.rain_mm] for d in o.daily]
    return _render(comments, OUTLOOK_COLUMNS, rows)


def apsim_csv(data: dict, scenario: dict, age_hours: float) -> str:
    e, run = data["engine"], data["run"]
    ver = str(e.get("version"))
    sp = data["supported_point"]
    wi = data["weather_inputs"]
    ov = wi.get("overlap_check", {})
    th = data["thresholds"]
    comments = [
        "ClimSmart-OP daily series, APSIM-X crop-model run (NOT the simple water balance)",
        f"source: apsimx {ver} ({e.get('docker_image_tag')}, {e.get('run_method')})",
        f"status: {data.get('status')}",
        "INDICATIVE, NOT VALIDATED: APSIM oil-palm model not calibrated for Sumatra; not a yield forecast",
        f"point: {sp['name']} (lat {sp['lat']}, lon {sp['lon']}); APSIM data exist ONLY for this point",
        f"precomputed offline: run started {run['started_utc']} UTC, age {age_hours} h, today_utc {run['today_utc']}",
        f"scenario: {scenario['id']} - {scenario['description']}",
        f"assumed palm age at run date: {scenario['palm_age_years_at_today']} years (an assumption, not the estate's)",
        f"weather: founder station history {wi.get('founder_history_first')}..{wi.get('founder_history_last')} "
        f"(Merbau Selatan, ~8-9 km away) + Open-Meteo {wi.get('appended_first')}..{wi.get('last_day')}",
        f"weather mismatch measured {ov.get('period')}: annual rain station {ov.get('annual_rain_mm', {}).get('founder_station')} mm "
        f"vs Open-Meteo {ov.get('annual_rain_mm', {}).get('open_meteo')} mm",
        f"category is the class of stress_index = 1 - fw using cut-offs {', '.join(str(c['min']) for c in th['classes'][1:])}: {th['cutoff_source']}",
        "fw = APSIM palm water-stress factor (1 = none); depletion_fraction is soil depletion and is NOT what category is based on",
        "units: mm, mm/day, lai m2/m2, fractions 0-1; kind: past = reanalysis weather, forecast = forecast weather",
        *[f"limitation: {x}" for x in data["limitations"]],
    ]
    rows = [["apsimx", ver, scenario["id"], d["date"], d["kind"], d["category"], d["label"], d["rain_mm"], d["eo_mm"],
             d["et_mm"], d["es_mm"], d["ep_mm"], d["potential_ep_mm"], d["lai"], d["fw"], d["stress_index"],
             d["understory_fw"], d["soil_water_mm"], d["available_water_mm"], d["water_above_dul_mm"],
             d["available_fraction"], d["depletion_fraction"], d["runoff_mm"], d["drainage_mm"]]
            for d in scenario["daily"]]
    return _render(comments, APSIM_COLUMNS, rows)


COSTBENEFIT_COLUMNS = ["source", "source_version", "weather_variant", "scenario", "label", "d_ffb_t_ha_per_year",
                       "d_n_applied_kg_ha", "d_irrigation_mm", "d_n_leached_no3_kg_ha", "d_mean_fw", "currency",
                       "d_revenue", "d_extra_harvest_haul_cost", "d_fertiliser_cost", "d_water_cost",
                       "d_irrigation_fixed_cost", "net_difference", "net_unfavourable", "net_favourable",
                       "break_even_ffb_price", "break_even_yield_factor"]


def costbenefit_csv(resp: dict) -> str:
    """resp = the CostBenefitResponse as a dict. One row per non-base scenario."""
    ver = resp["engine"]["version"]
    comments = [
        "ClimSmart-OP cost-benefit view: APSIM-X management scenarios x USER-SUPPLIED prices (Mambang Muda only)",
        f"physical numbers: source apsimx {ver}, run {resp['run']['started_utc']} UTC; economics: user inputs, not market data",
        "INDICATIVE, NOT VALIDATED: APSIM-X model not calibrated for this site; assumed palm planting year "
        f"{resp['evaluation']['assumed_planting_year']}; hindcast years {resp['evaluation']['full_years']}; weather variant {resp['weather_variant']}",
        "values are per hectare per year, scenario minus base (mean of the hindcast years); net_difference > 0 = better than base for these inputs",
        "NO savings, profit or ROI claim is made or implied",
    ]
    for i in resp["inputs"]:
        comments.append(f"input {i['name']} = {i['value']} ({i['unit']}); {'EXAMPLE DEFAULT, replace with your cost' if i['is_example_default'] else 'supplied by caller'}")
    comments.append(f"sensitivity +/-{resp['sensitivity_pct']}%: net_unfavourable = FFB price down and costs up; net_favourable = reverse")
    comments += [f"limitation: {x}" for x in resp["limitations"]]
    rows = []
    for sc in resp["scenarios"]:
        if sc["is_base"]:
            continue
        d, m, be = sc["delta_vs_base_per_ha_year"], sc["money_vs_base_per_ha_year"], sc["break_even"]
        sens = {x["case"]: x["net_difference"] for x in sc["sensitivity"]}
        rows.append(["apsimx", ver, resp["weather_variant"], sc["id"], sc["label"], d["d_ffb_t_ha"], d["d_n_applied_kg_ha"],
                     d["d_irrigation_mm"], d["d_n_leached_no3_kg_ha"], d["d_mean_fw"], resp["currency"], m["d_revenue"],
                     m["d_extra_harvest_haul_cost"], m["d_fertiliser_cost"], m["d_water_cost"], m["d_irrigation_fixed_cost"],
                     m["net_difference"], sens["unfavourable (FFB low, costs high)"], sens["favourable (FFB high, costs low)"],
                     "" if be["ffb_price_per_tonne"] is None else be["ffb_price_per_tonne"],
                     "" if be["yield_response_factor"] is None else be["yield_response_factor"]])
    return _render(comments, COSTBENEFIT_COLUMNS, rows)
