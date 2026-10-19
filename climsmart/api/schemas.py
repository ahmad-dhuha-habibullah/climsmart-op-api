"""Response models. Every response carries a `meta` block. Field descriptions appear in /docs."""
from __future__ import annotations

from datetime import date as Date
from typing import Literal

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    """Error body for every non-2xx answer. Validation errors (422) use FastAPI's standard list."""
    detail: str = Field(description="Short human-readable reason. Never contains secrets or internals.",
                        examples=["Weather provider is rate-limiting us; try again shortly"])


class HealthResponse(BaseModel):
    status: Literal["ok"] = Field(description="Always 'ok' when the service is up")
    version: str = Field(description="API version", examples=["0.1.0"])
    auth: str = Field(description="'open (demo mode)' or 'api-key'", examples=["open (demo mode)"])


class SourceInfo(BaseModel):
    name: str = Field(description="Data source name", examples=["Open-Meteo"])
    url: str = Field(description="Source website", examples=["https://open-meteo.com"])
    attribution: str = Field(description="Attribution text you must show to your users",
                             examples=["Weather data by Open-Meteo.com (CC BY 4.0)"])
    license_note: str = Field(description="Licence caveat for this source")


class ModelInfo(BaseModel):
    name: str = Field(description="Engine id", examples=["fao56-single-bucket"])
    version: str = Field(description="Engine version", examples=["0.1.0"])
    description: str = Field(description="One-line description of the model")


class ParameterSource(BaseModel):
    name: str = Field(description="Parameter name", examples=["kc"])
    value: float | str = Field(description="Value used in this response", examples=[1.0])
    status: str = Field(description="Evidence status, e.g. 'provisional' or "
                                    "'no citable source, provisional'")
    source: str = Field(description="Where the value comes from (citation or 'none')")


class Meta(BaseModel):
    """Provenance block present in every response: read it before using any number."""
    api_version: str = Field(examples=["0.1.0"])
    status: str = Field(description="Always states that outputs are indicative and not validated.",
                        examples=["INDICATIVE: model-derived, not agronomically validated, "
                                  "not calibrated to field data."])
    sources: list[SourceInfo]
    model: ModelInfo | None = Field(default=None, description="Water-balance engine used (null for /v1/forecast)")
    assumptions: list[str] = Field(description="Modelling assumptions and notices (includes a "
                                               "STALE WEATHER line when stale data is served)")
    disclaimers: list[str] = Field(description="Plain-language limits of use")
    parameter_sources: list[ParameterSource] = Field(
        default_factory=list, description="Where each default parameter / cut-off comes from")
    weather_fetched_at: str | None = Field(default=None, description="When the weather was fetched (UTC, ISO 8601)")
    weather_stale: bool = Field(default=False, description="True = the provider failed and a cached answer is "
                                                          "served. Also sent as header X-ClimSmart-Data.")
    weather_age_seconds: int | None = Field(default=None, description="Age of the stale data in seconds "
                                                                      "(null when not stale)")
    weather_source: str | None = Field(default=None, description="live = fetched now or from a fresh cache; stale_cache = the provider "
                                                                "failed and an older cached answer is served; snapshot = the provider failed "
                                                                "and the committed last-good file is served")
    weather_note: str | None = Field(default=None, description="Plain sentence for the UI when the weather is not live, "
                                                              "e.g. 'Showing forecast from 06:12 on 18 Oct'. Null when live.")
    weather_timezone: str | None = Field(default=None, description="IANA time zone of the site, for showing weather_fetched_at in local time")
    generated_at: str = Field(description="When this response was produced (UTC, ISO 8601)")


class Location(BaseModel):
    latitude: float = Field(description="Grid-cell latitude actually used by the weather source (degrees)")
    longitude: float = Field(description="Grid-cell longitude actually used (degrees)")
    elevation_m: float | None = Field(default=None, description="Source elevation, metres")
    timezone: str = Field(description="IANA time zone; daily values are for local calendar days",
                          examples=["Asia/Jakarta"])


class ForecastDay(BaseModel):
    date: Date = Field(description="Local calendar date")
    kind: Literal["past", "forecast"] = Field(description="'past' = recent model analysis, 'forecast' = predicted")
    rain_mm: float = Field(description="Daily precipitation, mm")
    et0_mm: float = Field(description="FAO-56 reference evapotranspiration, mm/day")
    temp_max_c: float | None = Field(default=None, description="Daily maximum temperature, deg C")
    temp_min_c: float | None = Field(default=None, description="Daily minimum temperature, deg C")
    rain_probability_pct: float | None = Field(default=None, description="Chance of rain, %, forecast days only")


class ForecastResponse(BaseModel):
    """Normalised daily weather for a point (recent past, then forecast)."""
    meta: Meta
    location: Location
    daily: list[ForecastDay]


class ParamsUsed(BaseModel):
    root_zone_capacity_mm: float = Field(description="Plant-available water in the root zone (TAW), mm")
    kc: float = Field(description="Crop coefficient used")
    depletion_fraction_p: float = Field(description="Fraction of TAW usable before stress starts")
    initial_depletion_mm: float = Field(description="Soil-water deficit at the first spin-up day, mm")
    runoff_fraction: float = Field(description="Share of rain treated as runoff, 0-0.9")
    spinup_days: int = Field(description="Past days used to warm the model up")
    overridden: list[str] = Field(default_factory=list,
                                  description="Parameters set by the caller instead of the config default")


class WaterDay(BaseModel):
    date: Date = Field(description="Local calendar date")
    kind: Literal["past", "forecast"] = Field(description="'past' = recent model analysis, 'forecast' = predicted")
    rain_mm: float = Field(description="Precipitation, mm")
    et0_mm: float = Field(description="Reference evapotranspiration, mm")
    etc_potential_mm: float = Field(description="Crop demand without stress = kc x ET0, mm")
    etc_actual_mm: float = Field(description="Crop water use after stress reduction, mm")
    drainage_mm: float = Field(description="Water lost below the root zone, mm")
    deficit_mm: float = Field(description="End-of-day soil-water deficit, 0 = full store, mm")
    deficit_fraction: float = Field(description="deficit / root-zone capacity, 0-1")
    ks: float = Field(description="FAO-56 stress coefficient: 1 = none, 0 = full stress")
    stress_ratio: float = Field(description="1 - ks")


class WaterBalanceResponse(BaseModel):
    """Daily bucket model output. Indicative, not validated."""
    meta: Meta
    location: Location
    parameters: ParamsUsed
    daily: list[WaterDay]


class OutlookDay(BaseModel):
    date: Date = Field(description="Local calendar date")
    kind: Literal["past", "forecast"] = Field(description="'past' = recent model analysis, 'forecast' = predicted")
    category: Literal["low", "moderate", "high", "critical"] | str = Field(
        description="Class id from the thresholds file (default: low, moderate, high, critical)")
    label: str = Field(description="Display label of the class", examples=["Moderate"])
    deficit_mm: float = Field(description="End-of-day soil-water deficit, mm (0 = full store)")
    deficit_fraction: float = Field(description="deficit / capacity: the number the class is based on")
    stress_ratio: float = Field(description="1 - ks; 0 = no modelled stress")
    rain_mm: float = Field(description="Precipitation that day, mm")


class OutlookSummary(BaseModel):
    forecast_days: int = Field(description="Number of forecast days in `daily`")
    peak_category: str = Field(description="Worst class reached in the forecast days")
    peak_date: Date | None = Field(description="Day the peak is reached (null if no forecast days)")
    first_high_or_worse_date: Date | None = Field(description="First forecast day at 'high' or worse, "
                                                              "null if never")
    days_per_category: dict[str, int] = Field(description="Count of forecast days in each class")


class ThresholdOut(BaseModel):
    name: str = Field(description="Class id used in `category`")
    label: str = Field(description="Display label")
    min: float = Field(description="Lower bound (inclusive) of deficit_fraction for this class")
    meaning: str = Field(description="Plain-language meaning of the class")
    status: str = Field(description="Evidence status of the class definition")
    cutoff_source: str = Field(default="no citable source, provisional", description="Source of the cut-off value")


class ThresholdsOut(BaseModel):
    version: str = Field(description="Thresholds file version")
    status: str = Field(description="Evidence status of the whole threshold set")
    cutoff_source: str = Field(default="no citable source, provisional",
                               description="The class cut-offs have no citable source")
    metric: str = Field(description="What the cut-offs are applied to", examples=["deficit_fraction_of_taw"])
    classes: list[ThresholdOut] = Field(description="Classes in ascending order")


class OutlookResponse(BaseModel):
    """Traffic-light outlook. The class cut-offs are provisional and have no citable source."""
    meta: Meta
    location: Location
    parameters: ParamsUsed
    thresholds: ThresholdsOut
    summary: OutlookSummary
    daily: list[OutlookDay]
