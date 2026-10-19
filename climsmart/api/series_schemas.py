"""Response models for /v1/apsim-series (saved APSIM-X output series for plotting)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SeriesMeta(BaseModel):
    source: Literal["apsimx"] = Field(description="Always 'apsimx': numbers come from saved APSIM-X output (.db) files, not from a live run and not from the simple water balance")
    status: str = Field(description="Honesty label: INDICATIVE, NOT VALIDATED")
    engine: str = Field(description="APSIM-X version that produced the numbers", examples=["APSIM Next Generation 2026.4.8030.0"])
    run_started_utc: str = Field(description="When the APSIM runs started (UTC, ISO 8601)")
    age_hours: float = Field(description="Hours since the run started; this is saved data, it does not update by itself")
    stale: bool = Field(description="True when the saved run is older than 48 hours (the partial-year figure then lags)")
    point: str = Field(description="The single supported location")
    lat: float = Field(description="Latitude of the supported point")
    lon: float = Field(description="Longitude of the supported point")
    weather: dict[str, str] = Field(description="How the weather was spliced: founder history, appended period, Open-Meteo hindcast/forecast, and a note")
    assumptions: dict[str, str | int] = Field(description="Assumed planting year (2012) and management start year; both are assumptions")
    limitations: list[str] = Field(description="What this data is not (not validated, not a harvest forecast, one point)")


class UnitInfo(BaseModel):
    annual: dict[str, str] = Field(description="Unit of each annual series")
    monthly: dict[str, str] = Field(description="Unit of each monthly series")
    notes: dict[str, str] = Field(description="Where a unit or definition needs explaining (e.g. what soil_water_mm includes), HYPOTHESIS marked")


class AnnualSeries(BaseModel):
    year: list[int] = Field(description="Calendar years (full years only), ascending")
    ffb_t_ha: list[float | None] = Field(description="MODELLED fresh fruit bunch yield, t/ha per year (not a forecast, not validated)")
    lai: list[float | None] = Field(description="Leaf area index at year end, m2/m2")
    palm_age_y: list[float | None] = Field(description="Palm age at year end, years (assumes planting 2012 for as_is)")
    rain_mm: list[float | None] = Field(description="Annual rain used by the model, mm")
    n_applied_kg_ha: list[float | None] = Field(description="Nitrogen fertiliser applied, kg/ha per year")
    n_leached_kg_ha: list[float | None] = Field(description="Nitrate-N leached below the profile, kg/ha per year (unit HYPOTHESIS: kg/ha)")
    irrigation_mm: list[float | None] = Field(description="Irrigation applied by the scenario rule, mm per year")
    palm_n_uptake_kg_ha: list[float | None] = Field(description="Palm nitrogen uptake, kg/ha per year")


class MonthlySeries(BaseModel):
    month: list[str] = Field(description="Month (YYYY-MM); each value is read on the 1st of that month")
    lai: list[float | None] = Field(description="Leaf area index, m2/m2")
    soil_water_mm: list[float | None] = Field(description="Total soil water in the 2 m profile, mm (includes water below wilting point; see unit notes)")
    soil_nitrate_kg_ha: list[float | None] = Field(description="Total soil nitrate-N, kg/ha (unit HYPOTHESIS)")
    stem_mass_g_m2: list[float | None] = Field(description="Palm stem dry mass, g/m2 (biomass proxy)")


class PartialYear(BaseModel):
    year: int = Field(description="The incomplete year")
    through: str = Field(description="Last simulated day (includes forecast days)")
    ffb_t_ha_to_date: float = Field(description="Modelled FFB yield from 1 January to that day, t/ha. Not a full year: do not annualise")
    partial: Literal[True] = Field(description="Always true")


class SeriesScenario(BaseModel):
    id: str = Field(description="Scenario id", examples=["as_delivered__n_1_5x"])
    group: Literal["planting_year", "management_as_delivered", "management_rain_scaled"] = Field(description="planting_year = palm-age sensitivity; management_* = N/irrigation scenarios with Open-Meteo rain as delivered or scaled to the station")
    label: str = Field(description="Short name for legends")
    description: str = Field(description="What differs from the base scenario")
    weather_variant: Literal["as_delivered", "rain_scaled"] = Field(description="Which weather the run used")
    is_base: bool = Field(description="True for the BAU/base scenario of its group")
    params: dict | None = Field(default=None, description="Management parameters (N multiplier, applications per year, irrigation rule, start year); null for planting-year scenarios")
    db_file: str = Field(description="Name of the APSIM .db the numbers were read from (the file itself is not served)")
    annual: AnnualSeries | None = Field(default=None, description="Annual series; omitted with annual=false")
    monthly: MonthlySeries | None = Field(default=None, description="Monthly series; omitted with monthly=false")
    partial_year: PartialYear | None = Field(default=None, description="Yield to date for the last, incomplete year")


class ApsimSeriesResponse(BaseModel):
    meta: SeriesMeta = Field(description="Source labels and honesty flags")
    units: UnitInfo = Field(description="Units and definitions")
    soil: dict | None = Field(default=None, description="Soil water reference values of the profile (LL15, DUL, SAT sums, mm) to draw reference lines")
    scenarios: list[SeriesScenario] = Field(description="The scenarios selected by the query")
