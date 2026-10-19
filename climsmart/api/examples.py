"""Example responses shown in /docs. Values are ILLUSTRATIVE (not a real forecast); a test
validates each example against its response model so the docs cannot drift from the schema."""
from __future__ import annotations

_STATUS = "INDICATIVE: model-derived, not agronomically validated, not calibrated to field data."
_META = {
    "api_version": "0.1.0", "status": _STATUS,
    "sources": [{"name": "Open-Meteo", "url": "https://open-meteo.com",
                 "attribution": "Weather data by Open-Meteo.com (CC BY 4.0)",
                 "license_note": "Free tier is non-commercial use only; commercial use requires a paid "
                                 "Open-Meteo plan or another provider."}],
    "model": {"name": "fao56-single-bucket", "version": "0.1.0",
              "description": "FAO-56 style single-layer daily soil-water balance with a Ks stress factor."},
    "assumptions": ["Single soil layer with fixed plant-available water capacity (default 115 mm per 1 m root zone)."],
    "disclaimers": ["Indicative decision-support only. Outputs are model-derived and have NOT been validated "
                    "against field observations.",
                    "Not a yield forecast. No yield, cost-saving or profit claim is made or implied."],
    "parameter_sources": [
        {"name": "kc", "value": 1.0, "status": "literature value for mature stands only; provisional",
         "source": "FAO-56 Table 12 palm trees; Monzon et al. 2022."},
        {"name": "outlook_class_cutoffs", "value": "moderate >= 0.3, high >= 0.5, critical >= 0.7",
         "status": "no citable source, provisional", "source": "No citable source; display scale only."}],
    "weather_fetched_at": "2026-10-17T13:00:00+00:00", "weather_stale": False, "weather_age_seconds": None,
    "generated_at": "2026-10-17T13:00:01+00:00",
}
_LOC = {"latitude": 2.636, "longitude": 99.671, "elevation_m": 19.0, "timezone": "Asia/Jakarta"}
_PARAMS = {"root_zone_capacity_mm": 115.0, "kc": 1.0, "depletion_fraction_p": 0.65,
           "initial_depletion_mm": 0.0, "runoff_fraction": 0.0, "spinup_days": 60, "overridden": []}

FORECAST = {"meta": {**_META, "model": None, "parameter_sources": []}, "location": _LOC, "daily": [
    {"date": "2026-10-16", "kind": "past", "rain_mm": 4.2, "et0_mm": 3.9, "temp_max_c": 30.1,
     "temp_min_c": 23.4, "rain_probability_pct": None},
    {"date": "2026-10-17", "kind": "forecast", "rain_mm": 12.5, "et0_mm": 3.6, "temp_max_c": 29.0,
     "temp_min_c": 23.1, "rain_probability_pct": 80.0}]}

WATER_BALANCE = {"meta": {**_META, "parameter_sources": _META["parameter_sources"][:1]}, "location": _LOC,
                 "parameters": _PARAMS, "daily": [
    {"date": "2026-10-17", "kind": "forecast", "rain_mm": 12.5, "et0_mm": 3.6, "etc_potential_mm": 3.6,
     "etc_actual_mm": 3.6, "drainage_mm": 0.0, "deficit_mm": 31.2, "deficit_fraction": 0.2713,
     "ks": 1.0, "stress_ratio": 0.0}]}

_CLASSES = [
    {"name": "low", "label": "Low", "min": 0.0, "meaning": "Soil water store is mostly full.",
     "status": "provisional, not agronomically validated", "cutoff_source": "no citable source, provisional"},
    {"name": "moderate", "label": "Moderate", "min": 0.3, "meaning": "Store is drawing down.",
     "status": "provisional, not agronomically validated", "cutoff_source": "no citable source, provisional"},
    {"name": "high", "label": "High", "min": 0.5, "meaning": "Approaching the model's stress-onset level.",
     "status": "provisional, not agronomically validated", "cutoff_source": "no citable source, provisional"},
    {"name": "critical", "label": "Critical", "min": 0.7, "meaning": "Beyond the model's stress-onset level.",
     "status": "provisional, not agronomically validated", "cutoff_source": "no citable source, provisional"}]

OUTLOOK = {"meta": _META, "location": _LOC, "parameters": _PARAMS,
           "thresholds": {"version": "0.2.0-provisional", "status": "provisional, not agronomically validated",
                          "cutoff_source": "no citable source, provisional", "metric": "deficit_fraction_of_taw",
                          "classes": _CLASSES},
           "summary": {"forecast_days": 1, "peak_category": "low", "peak_date": "2026-10-17",
                       "first_high_or_worse_date": None,
                       "days_per_category": {"low": 1, "moderate": 0, "high": 0, "critical": 0}},
           "daily": [{"date": "2026-10-17", "kind": "forecast", "category": "low", "label": "Low",
                      "deficit_mm": 31.2, "deficit_fraction": 0.2713, "stress_ratio": 0.0, "rain_mm": 12.5}]}


def _load_apsim_example() -> dict:
    """Short real /v1/apsim-outlook response (docs/examples/apsim-outlook.short.json), if generated."""
    import json
    from pathlib import Path
    f = Path(__file__).resolve().parent.parent.parent / "docs" / "examples" / "apsim-outlook.short.json"
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


APSIM_OUTLOOK = _load_apsim_example()


def _load_csv_example(name: str, fallback: str) -> str:
    """Head of a real CSV export (docs/examples/<name>): comment lines plus the first 3 data rows."""
    from pathlib import Path
    f = Path(__file__).resolve().parent.parent.parent / "docs" / "examples" / name
    try:
        lines = f.read_text(encoding="utf-8").splitlines()
    except OSError:
        return fallback
    head = [x for x in lines if x.startswith("#")][:14]
    body = [x for x in lines if not x.startswith("#")][:4]
    return "\n".join(head + ["# ..."] + body) + "\n"


OUTLOOK_CSV = _load_csv_example("outlook.csv", "# source: simple-water-balance\nsource,source_version,date,kind,category\n")
APSIM_CSV = _load_csv_example("apsim-outlook.csv", "# source: apsimx\nsource,source_version,scenario,date,kind,category\n")


def _load_cb_example() -> dict:
    """Short real /v1/apsim-cost-benefit response (docs/examples/apsim-cost-benefit.short.json), if generated."""
    import json
    from pathlib import Path
    f = Path(__file__).resolve().parent.parent.parent / "docs" / "examples" / "apsim-cost-benefit.short.json"
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


COST_BENEFIT = _load_cb_example()
COST_BENEFIT_CSV = _load_csv_example("apsim-cost-benefit.csv", "# source: apsimx\nsource,source_version,weather_variant,scenario\n")


def _load_ft_example() -> dict:
    """Real /v1/fertiliser-timing hindcast response (docs/examples/fertiliser-timing.short.json), if generated."""
    import json
    from pathlib import Path
    f = Path(__file__).resolve().parent.parent.parent / "docs" / "examples" / "fertiliser-timing.short.json"
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


FERTILISER_TIMING = _load_ft_example()


def _load_series_example() -> dict:
    """Short real /v1/apsim-series response (docs/examples/apsim-series.short.json), if generated."""
    import json
    from pathlib import Path
    f = Path(__file__).resolve().parent.parent.parent / "docs" / "examples" / "apsim-series.short.json"
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


APSIM_SERIES = _load_series_example()
