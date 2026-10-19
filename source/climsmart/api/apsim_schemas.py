"""Response models for /v1/apsim-outlook (APSIM-X results beside the simple water balance)."""
from __future__ import annotations

from datetime import date as Date
from typing import Any, Literal

from pydantic import BaseModel, Field

ApsimSource = Literal["apsimx"]
BalanceSource = Literal["simple-water-balance"]


class ApsimPoint(BaseModel):
    name: str = Field(description="Name of the only place APSIM results exist for", examples=["Mambang Muda, North Sumatra, Indonesia"])
    lat: float = Field(description="Latitude, decimal degrees", examples=[2.6235])
    lon: float = Field(description="Longitude, decimal degrees", examples=[99.6922])


class ApsimEngineInfo(BaseModel):
    name: str = Field(description="Model name", examples=["APSIM Next Generation"])
    version: str = Field(description="Exact APSIM version that produced the numbers", examples=["2026.4.8030.0"])
    build_date: str | None = Field(default=None, description="APSIM build date", examples=["2026-04-24"])
    docker_image_tag: str | None = Field(default=None, description="Docker Hub image the build was taken from", examples=["apsiminitiative/apsimng:8030"])
    docker_image_digest: str | None = Field(default=None, description="Image digest (sha256) of that tag")
    dotnet: str | None = Field(default=None, description=".NET runtime bundled with the image")
    run_method: str = Field(description="How APSIM was started", examples=["dotnet Models.dll (no docker)"])


class ApsimRun(BaseModel):
    started_utc: str = Field(description="When the offline run started (UTC, ISO 8601)", examples=["2026-10-17T13:56:41+00:00"])
    age_hours: float = Field(description="Hours since started_utc, computed when you call the API", examples=[3.2])
    result_stale: bool = Field(description="True when the result is older than 48 h: re-run scripts/run_apsim.py")
    runtime_seconds: dict[str, float] = Field(description="APSIM wall-clock seconds per scenario")
    today_utc: str = Field(description="The run's 'today' (UTC date): days before are 'past', from this day 'forecast'")
    clock_end: str = Field(description="Last simulated day (end of the weather file)")
    clock_start: str = Field(description="How the simulation start is chosen")
    history_days_served: int = Field(description="Past days included in each scenario's daily list")


class ApsimDay(BaseModel):
    date: Date = Field(description="Calendar date (UTC date of the weather file)")
    kind: Literal["past", "forecast"] = Field(description="'past' = reanalysis/analysis weather, 'forecast' = forecast weather")
    rain_mm: float = Field(description="Rain used by APSIM, mm")
    eo_mm: float = Field(description="APSIM potential evaporation Eo, mm/day")
    et_mm: float = Field(description="APSIM evapotranspiration = soil evaporation + palm transpiration + understorey transpiration, mm/day")
    es_mm: float = Field(description="APSIM soil evaporation Es, mm/day")
    ep_mm: float = Field(description="APSIM palm transpiration EP, mm/day")
    potential_ep_mm: float = Field(description="Palm transpiration demand (PotentialEP), mm/day")
    lai: float = Field(description="Palm leaf area index, m2/m2")
    fw: float = Field(description="APSIM palm water-stress factor on photosynthesis: 1 = none, lower = more stress")
    stress_index: float = Field(description="1 - fw (0 = none, 1 = full). The class is taken from this")
    understory_fw: float = Field(description="Water-stress factor of the understorey vegetation (1 = none)")
    soil_water_mm: float = Field(description="Profile soil water (0-2 m), mm")
    available_water_mm: float = Field(description="Soil water above LL15 and up to DUL, mm (maximum = soil.plant_available_water_mm)")
    water_above_dul_mm: float = Field(description="Soil water above DUL (wetter than field capacity), mm")
    available_fraction: float = Field(description="available_water_mm / plant-available capacity, 0-1")
    depletion_fraction: float = Field(description="1 - available_fraction, 0-1 (soil depletion; NOT what the class is based on)")
    runoff_mm: float = Field(description="APSIM runoff, mm")
    drainage_mm: float = Field(description="APSIM drainage below the profile, mm")
    category: str = Field(description="Class of stress_index with the PROVISIONAL 0.3/0.5/0.7 cut-offs (no citable source)", examples=["low"])
    label: str = Field(description="Display label of the class", examples=["Low"])


class ApsimSummary(BaseModel):
    peak_stress_category: str | None = Field(description="Worst class in the next 7 forecast days", examples=["low"])
    peak_stress_index: float | None = Field(description="Largest stress_index in the next 7 days")
    peak_stress_date: str | None = Field(description="Date of that peak")
    min_fw: float | None = Field(description="Lowest fw in the next 7 days")
    max_depletion_fraction: float | None = Field(description="Largest soil depletion fraction in the next 7 days")
    rain_mm_total: float = Field(description="Rain in the next 7 days, mm")
    et_mm_total: float = Field(description="APSIM evapotranspiration in the next 7 days, mm")


class ApsimScenarioSummary(BaseModel):
    id: str = Field(description="Scenario id", examples=["as_is"])
    description: str = Field(description="What this scenario assumes")
    palm_age_years_at_today: float = Field(description="Simulated palm age on the run date, years (an assumption)")
    summary_next_7_days: ApsimSummary = Field(description="Next-7-days summary for this scenario")


class ApsimScenario(ApsimScenarioSummary):
    daily: list[ApsimDay] = Field(description="Daily APSIM values: recent past, then forecast days")


class ApsimMonth(BaseModel):
    month: str = Field(description="Month (YYYY-MM)", examples=["2026-08"])
    rain_mm: float = Field(description="Rain, mm")
    eo_mm: float = Field(description="APSIM Eo, mm")
    et_mm: float = Field(description="APSIM evapotranspiration, mm")
    min_fw: float = Field(description="Lowest fw in the month")
    days_fw_below_0_95: int = Field(description="Days with fw < 0.95")
    max_depletion_fraction: float = Field(description="Largest soil depletion fraction in the month")


class ApsimMeta(BaseModel):
    source: ApsimSource = Field(description="Where every number in the 'apsim' block comes from: an APSIM-X run")
    status: str = Field(description="Honesty label; always 'INDICATIVE ... not validated'")
    generated_at: str = Field(description="When this response was produced (UTC, ISO 8601)")
    precomputed: bool = Field(description="Always true: APSIM runs offline, never per request")
    supported_point: ApsimPoint = Field(description="APSIM data exists ONLY for this point")
    note: str = Field(description="One-sentence reminder of the above")


class ApsimBlock(BaseModel):
    source: ApsimSource = Field(description="Always 'apsimx': these numbers are from an APSIM-X run")
    engine: ApsimEngineInfo = Field(description="Exact APSIM build")
    run: ApsimRun = Field(description="When and how long the offline run took")
    weather_inputs: dict[str, Any] = Field(description="What weather went into APSIM: founder history, Open-Meteo "
                                                       "archive/forecast spans, grid cell, and the measured station-vs-grid overlap check")
    soil: dict[str, Any] = Field(description="Soil water capacity of the founder's 2 m profile (LL15, DUL, SAT, plant-available mm)")
    edits: list[str] = Field(description="Every change made to the founder's .apsimx copy before running")
    limitations: list[str] = Field(description="What limits these numbers. Show them to users")
    thresholds: dict[str, Any] = Field(description="Class cut-offs used and their (lack of) source")
    scenario: ApsimScenario = Field(description="The selected scenario with daily values")
    other_scenarios: list[ApsimScenarioSummary] = Field(description="Sensitivity scenarios (palm age, rain source): summaries only")
    monthly_context: list[ApsimMonth] = Field(description="APSIM month-by-month context for the last 12 months (default scenario)")
    sanity_check_founder_period: dict[str, Any] = Field(description="Annual rain / Eo / ET / LAI / FFB of the founder's own 2001-2021 setup re-run, for plausibility checks")


class BalanceDay(BaseModel):
    date: Date = Field(description="Local calendar date")
    kind: Literal["past", "forecast"] = Field(description="'past' or 'forecast'")
    rain_mm: float = Field(description="Rain from the weather source, mm")
    et0_mm: float = Field(description="FAO-56 reference evapotranspiration, mm")
    deficit_mm: float = Field(description="Bucket soil-water deficit, mm")
    deficit_fraction: float = Field(description="deficit / capacity, 0-1")
    stress_ratio: float = Field(description="1 - ks, 0-1")
    category: str = Field(description="Class of deficit_fraction (provisional cut-offs)", examples=["low"])
    label: str = Field(description="Display label of the class", examples=["Low"])


class BalanceBlock(BaseModel):
    source: BalanceSource = Field(description="Always 'simple-water-balance': NOT an APSIM number")
    engine: str = Field(description="Engine name and version", examples=["fao56-single-bucket 0.1.0"])
    location: str = Field(description="Weather grid cell used, 'lat, lon'")
    parameters: str = Field(description="Capacity / Kc / p / spin-up used (provisional defaults)")
    peak_category: str = Field(description="Worst class in the next 7 forecast days")
    daily: list[BalanceDay] = Field(description="Daily bucket values")
    note: str = Field(description="Why this differs from the APSIM block")
    weather_stale: bool = Field(description="True if cached (stale) weather was used")


class ApsimOutlookResponse(BaseModel):
    """APSIM-X outlook for the one supported point, with the simple water balance for comparison."""
    meta: ApsimMeta
    apsim: ApsimBlock = Field(description="APSIM-X numbers (source = 'apsimx')")
    comparison: BalanceBlock | None = Field(default=None, description="Simple FAO-56 bucket for the same point and dates (source = "
                                            "'simple-water-balance'); null if the weather provider was unavailable or compare=false")
    comparison_unavailable_reason: str | None = Field(default=None, description="Why comparison is null (when it is)")
