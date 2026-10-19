"""APSIMX generation from a registered template with a WHITELIST of editable parameters.

Every whitelisted parameter maps to a node/property that was verified to exist in the template file
(oilpalm_south_sumatra_1.0.0.apsimx). Anything else is rejected; users never upload APSIMX or manager code.
"""
from __future__ import annotations

import copy
import json
from datetime import date

from app.core.errors import invalid

MET_FILENAME = "weather.met"
EXTRA_REPORT = "ClimSmartDaily"
# Added report (verified to run on APSIM NG 2026.4.8030): weather + key palm/soil state for overlays.
EXTRA_VARIABLES = ["[Clock].Today", "[Weather].Rain", "[Weather].MaxT", "[Weather].MinT", "[Weather].Radn",
                   "[OilPalm].LAI", "[OilPalm].FW", "[OilPalm].EP", "[Soil].SoilWater.Es",
                   "sum([Soil].Water.MM) as SoilWaterMM", "[OilPalm].HarvestFFB"]

PARAMETERS = {
    "clock_start": {"type": "date", "label": "Simulation start", "node": "Clock.Start", "required": True},
    "clock_end": {"type": "date", "label": "Simulation end", "node": "Clock.End", "required": True},
    "sowing_date": {"type": "date", "label": "Planting (sowing) date", "node": "Palm Management.sowing_date",
                    "required": True, "help": "Palms are sown on this exact day; it must lie inside the clock period."},
    "population": {"type": "number", "label": "Palm density", "unit": "palms/ha", "min": 50, "max": 250,
                   "default": 135, "node": "Palm Management.population"},
    "understory_cover_max": {"type": "number", "label": "Max understory cover", "unit": "fraction", "min": 0,
                             "max": 1, "default": 0.4, "node": "OilPalm.UnderstoryCoverMax"},
    "initial_soil_water_fraction": {"type": "number", "label": "Initial soil water (fraction of PAW)",
                                    "unit": "0=LL15, 1=DUL", "min": 0, "max": 1, "default": 1.0,
                                    "node": "Soil.Water.InitialValues"},
}


def _find(node: dict, pred):
    if pred(node):
        return node
    for c in node.get("Children", []) or []:
        r = _find(c, pred)
        if r is not None:
            return r
    return None


def _by_type(model, prefix):
    return _find(model, lambda n: n.get("$type", "").startswith(prefix))


def _by_name(model, name):
    return _find(model, lambda n: n.get("Name") == name)


def inspect_template(text: str) -> dict:
    """Read the defaults of the whitelisted parameters from the template (proves the nodes exist)."""
    m = json.loads(text)
    clock, mgr, palm, phys, water = (_by_type(m, "Models.Clock"), _by_name(m, "Palm Management"),
                                     _by_type(m, "Models.PMF.OilPalm.OilPalm"), _by_type(m, "Models.Soils.Physical"),
                                     _by_type(m, "Models.Soils.Water"))
    if not all([clock, mgr, palm, phys, water, _by_type(m, "Models.Climate.Weather"), _by_type(m, "Models.Core.Zone")]):
        raise ValueError("template lacks one of Clock/Weather/Zone/Palm Management/OilPalm/Physical/Water")
    mp = {p["Key"]: p["Value"] for p in mgr.get("Parameters", [])}
    for k in ("sowing_date", "population"):
        if k not in mp:
            raise ValueError(f"Palm Management has no '{k}' parameter")
    params = copy.deepcopy(PARAMETERS)
    params["clock_start"]["template_value"] = clock["Start"][:10]
    params["clock_end"]["template_value"] = clock["End"][:10]
    sd = mp["sowing_date"].split(" ")[0]  # dd/mm/yyyy
    dd, mm, yy = sd.split("/")
    params["sowing_date"]["template_value"] = f"{yy}-{mm}-{dd}"
    params["population"]["template_value"] = float(mp["population"])
    params["understory_cover_max"]["template_value"] = palm.get("UnderstoryCoverMax")
    soil = _by_type(m, "Models.Soils.Soil")
    return {"apsimx_version": m.get("Version"), "parameters": params,
            "soil": {"name": soil.get("SoilType"), "site": soil.get("Site"), "country": soil.get("Country"),
                     "layers_mm": phys["Thickness"]}}


def validate_params(raw: dict, met_start: date, met_end: date) -> tuple[dict, list[dict]]:
    errors: list[dict] = []
    unknown = sorted(set(raw) - set(PARAMETERS))
    for k in unknown:
        errors.append({"param": k, "msg": "not an editable parameter of this template"})
    out: dict = {}
    for k, spec in PARAMETERS.items():
        v = raw.get(k, spec.get("default"))
        if v is None:
            if spec.get("required"):
                errors.append({"param": k, "msg": "required"})
            continue
        if spec["type"] == "date":
            try:
                out[k] = date.fromisoformat(str(v)).isoformat()
            except ValueError:
                errors.append({"param": k, "msg": "must be YYYY-MM-DD"})
        else:
            try:
                x = float(v)
            except (TypeError, ValueError):
                errors.append({"param": k, "msg": "must be a number"})
                continue
            if not (spec["min"] <= x <= spec["max"]):
                errors.append({"param": k, "msg": f"must be between {spec['min']} and {spec['max']}"})
            out[k] = x
    if not errors:
        cs, ce, sd = (date.fromisoformat(out[k]) for k in ("clock_start", "clock_end", "sowing_date"))
        if ce <= cs:
            errors.append({"param": "clock_end", "msg": "must be after clock_start"})
        if cs < met_start or ce > met_end:
            errors.append({"param": "clock_start/clock_end", "msg": f"clock period {cs}..{ce} must lie inside the MET "
                                                                    f"file period {met_start}..{met_end}"})
        if not (cs <= sd <= ce):
            errors.append({"param": "sowing_date", "msg": "must lie inside the clock period, otherwise palms are never "
                                                          "sown (the manager sows only when Today == sowing_date)"})
    if errors:
        raise invalid("invalid_config", "APSIM configuration failed validation", errors)
    return out, []


def generate(template_text: str, params: dict) -> tuple[str, list[str]]:
    m = json.loads(template_text)
    edits: list[str] = []
    w = _by_type(m, "Models.Climate.Weather")
    edits.append(f"Weather.FileName: '{w.get('FileName')}' -> '{MET_FILENAME}' (the linked MET artifact)")
    w["FileName"] = MET_FILENAME
    clock = _by_type(m, "Models.Clock")
    for key, p in (("Start", "clock_start"), ("End", "clock_end")):
        new = f"{params[p]}T00:00:00"
        edits.append(f"Clock.{key}: '{clock.get(key)}' -> '{new}'")
        clock[key] = new
    mgr = _by_name(m, "Palm Management")
    sd = date.fromisoformat(params["sowing_date"])
    for p in mgr["Parameters"]:
        if p["Key"] == "sowing_date":
            new = f"{sd.day:02d}/{sd.month:02d}/{sd.year} 00:00:00"
            edits.append(f"Palm Management.sowing_date: '{p['Value']}' -> '{new}'")
            p["Value"] = new
        elif p["Key"] == "population":
            new = f"{params['population']:g}"
            if p["Value"] != new:
                edits.append(f"Palm Management.population: '{p['Value']}' -> '{new}'")
            p["Value"] = new
    palm = _by_type(m, "Models.PMF.OilPalm.OilPalm")
    if palm.get("UnderstoryCoverMax") != params["understory_cover_max"]:
        edits.append(f"OilPalm.UnderstoryCoverMax: {palm.get('UnderstoryCoverMax')} -> {params['understory_cover_max']}")
    palm["UnderstoryCoverMax"] = params["understory_cover_max"]
    phys, water = _by_type(m, "Models.Soils.Physical"), _by_type(m, "Models.Soils.Water")
    f = params["initial_soil_water_fraction"]
    new_iv = [round(ll + f * (dul - ll), 4) for ll, dul in zip(phys["LL15"], phys["DUL"], strict=True)]
    if new_iv != water["InitialValues"]:
        edits.append(f"Soil.Water.InitialValues: LL15 + {f:g} x (DUL - LL15) per layer -> {new_iv}")
    water["InitialValues"] = new_iv
    zone = _by_type(m, "Models.Core.Zone")
    if _by_name(zone, EXTRA_REPORT) is None:
        zone.setdefault("Children", []).append({
            "$type": "Models.Report, Models", "VariableNames": EXTRA_VARIABLES, "EventNames": ["[Clock].EndOfDay"],
            "GroupByVariableName": None, "Name": EXTRA_REPORT, "ResourceName": None, "Children": [],
            "Enabled": True, "ReadOnly": False})
        edits.append(f"Added Models.Report '{EXTRA_REPORT}' (EndOfDay): " + ", ".join(EXTRA_VARIABLES))
    return json.dumps(m, indent=2), edits
