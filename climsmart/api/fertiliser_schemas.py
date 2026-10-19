"""Response models for /v1/fertiliser-timing (risk of loss from daily rain after a fertiliser application)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class FTMeta(BaseModel):
    status: str = Field(description="Honesty label: the percentages are founder assumptions, the result is a risk of loss, not a proven loss")
    mode: Literal["forecast", "hindcast"] = Field(description="forecast = Open-Meteo daily rain; hindcast = the founder's recorded daily-rain file")
    rain_source: str = Field(description="Where the daily rain comes from (name, and for the hindcast file its period)")
    rain_source_note: str = Field(description="Attribution / licence note for the rain source; Open-Meteo's free tier is non-commercial only")
    assumptions_version: str = Field(description="Version of config/fertiliser_timing.yaml that supplied the factors")
    assumptions_source: str = Field(description="Who the percentages come from (founder assumption), never measured results")
    window_days: int = Field(description="Days after the application date that are examined (D+1..D+N)")
    generated_at: str = Field(description="When this response was produced (UTC, ISO 8601)")
    weather_fetched_at: str | None = Field(default=None, description="Forecast mode: when the weather was fetched (UTC, ISO 8601)")
    weather_stale: bool = Field(default=False, description="Forecast mode: true when the provider failed and older weather is served")
    weather_age_seconds: int | None = Field(default=None, description="Age of the stale weather, seconds (null when not stale)")
    weather_source: str | None = Field(default=None, description="live | stale_cache | snapshot (see /v1/outlook meta)")
    weather_note: str | None = Field(default=None, description="Plain sentence for the UI when the weather is not live; null when live")
    weather_timezone: str | None = Field(default=None, description="IANA time zone of the site, to show weather_fetched_at in local time")


class FTInput(BaseModel):
    name: str = Field(description="Query parameter name", examples=["palms"])
    value: float = Field(description="Value used")
    unit: str = Field(description="Unit", examples=["palms"])
    is_example_default: bool = Field(description="True when you did not send this input: the value is the founder's worked-example number, replace it with your own")
    note: str = Field(description="What the input means")


class FTBlock(BaseModel):
    area_ha: float = Field(description="Block area, hectares")
    palms: int = Field(description="Palms in the block")
    kg_per_palm: float = Field(description="Fertiliser per palm, kg")
    price_per_kg_rp: int = Field(description="Fertiliser price, Rp per kg (whole rupiah)")
    value_rp: int = Field(description="Fertiliser value per application = palms x kg per palm x price per kg, whole Rp")
    value_per_ha_rp: int = Field(description="value_rp / area_ha, whole Rp")


class FTAssumption(BaseModel):
    id: str = Field(description="Risk class id", examples=["very_high"])
    label_en: str = Field(description="Class name, English")
    label_id: str = Field(description="Class name, Bahasa Indonesia")
    rule: str = Field(description="When the class applies, in words")
    bau_pct: float = Field(description="Assumed risk factor without ClimSmart-OP (business as usual), percent of the fertiliser value")
    residual_pct: float = Field(description="Assumed residual risk factor when the daily forecast is used, percent of the fertiliser value")
    bau_range_pct: list[float] | None = Field(default=None, description="The founder's stated range for bau_pct, when he gave a range instead of one number")
    residual_range_pct: list[float] | None = Field(default=None, description="The founder's stated range for residual_pct, when he gave a range")
    source: str = Field(description="'founder assumption (ClimSmart-OP proposal)'; 'caller override' when you changed it")
    overridden: bool = Field(description="True when the caller replaced the founder's default")
    basis: str = Field(description="Why the class exists (literature reasoning in the founder's chapter)")


class FTDay(BaseModel):
    date: str = Field(description="Calendar date (local, YYYY-MM-DD)")
    rain_mm: float = Field(description="Daily rain, mm")
    kind: Literal["past", "forecast", "recorded"] = Field(description="past/forecast = Open-Meteo (forecast mode); recorded = the founder's daily-rain file (hindcast mode)")


class FTAssessment(BaseModel):
    application_date: str = Field(description="Application date D (the last day of the application)")
    window: list[FTDay] = Field(description="Daily rain for D+1..D+N used to pick the class")
    window_rain_total_mm: float = Field(description="Total rain over the window, mm")
    class_id: str = Field(description="Risk class id")
    class_label_en: str = Field(description="Risk class, English")
    class_label_id: str = Field(description="Risk class, Bahasa Indonesia")
    bau_pct: float = Field(description="Assumed BAU factor for this class, percent (founder assumption)")
    residual_pct: float = Field(description="Assumed residual factor for this class, percent (founder assumption)")
    bau_risk_rp: int = Field(description="RISK OF LOSS in value terms without advice = value x bau_pct, whole Rp (not a proven loss)")
    residual_risk_rp: int = Field(description="Assumed residual risk when the daily forecast is used = value x residual_pct, whole Rp")
    avoided_rp: int = Field(description="bau_risk_rp - residual_risk_rp: risk of loss that could be avoided, whole Rp")
    bau_pct_if_applied_without_advice: float | None = Field(default=None, description="Only on the suggested day: the BAU factor of that day's own class, for comparison")


class FTHeadline(BaseModel):
    value_at_risk_planned_rp: int = Field(description="Risk of loss if applied on the planned date, Rp (assumption-based)")
    value_at_risk_suggested_rp: int = Field(description="Assumed residual risk when the application follows the daily forecast (the suggested day), Rp")
    avoided_rp: int = Field(description="Difference of the two, Rp")
    avoided_per_ha_rp: int = Field(description="avoided_rp / area_ha, whole Rp per hectare")
    suggested_day_class_id: str = Field(description="Risk class of the suggested day's own rain window, so you can see whether the suggested day is itself clear")
    suggested_day_is_clear: bool = Field(description="True when the suggested day's own class is 'safe' or 'not_classified'; False means every day in the search range still has a listed risk, and avoided_rp then rests on the founder's residual-factor assumption more than on a clear day")
    suggested_day_own_risk_rp: int = Field(description="Risk of loss on the suggested day taken from the same table (value x that day's BAU factor), Rp")
    avoided_vs_suggested_day_rp: int = Field(description="Conservative alternative to avoided_rp: planned-day risk minus the suggested day's own table risk (never below 0), Rp")
    moved: bool = Field(description="True when the suggested day differs from the planned date")
    text_en: str = Field(description="One sentence for the screen, English, 'risk of loss' wording")
    text_id: str = Field(description="One sentence for the screen, Bahasa Indonesia")


class FTScale(BaseModel):
    scale_ha: float = Field(description="Estate area you scale to (what-if)")
    avoided_per_ha_rp: int = Field(description="avoided_per_ha_rp from the block")
    avoided_rp: int = Field(description="avoided_per_ha_rp x scale_ha, whole Rp")
    note: str = Field(description="Reminder that scaling repeats one application of one block; frequency, fertiliser type, topography etc. are not modelled")


class FTScanDay(BaseModel):
    date: str = Field(description="A possible application date")
    class_id: str = Field(description="Risk class if fertilised on that date")
    bau_pct: float = Field(description="Assumed BAU factor of that class, percent")


class FertiliserTimingResponse(BaseModel):
    meta: FTMeta = Field(description="Labels, source, mode")
    inputs: list[FTInput] = Field(description="Every input used; example defaults are flagged")
    block: FTBlock = Field(description="The block and its fertiliser value")
    assumptions: list[FTAssumption] = Field(description="The editable risk factors used (founder assumptions)")
    planned: FTAssessment = Field(description="Assessment for the planned application date")
    suggested: FTAssessment = Field(description="Assessment for the suggested day (may equal the planned date)")
    headline: FTHeadline = Field(description="The one number for the screen")
    scale: FTScale = Field(description="What-if scaling to a larger area")
    days: list[FTDay] = Field(description="Daily rain series for the chart (planned window, suggested window and surrounding days)")
    scan: list[FTScanDay] = Field(description="Risk class per possible application date with a complete window (marks the safest day)")
    limitations: list[str] = Field(description="Honest list of what this does not do")


class HindcastAvailability(BaseModel):
    available: bool = Field(description="True when this server has the founder's daily-rain file, so mode=hindcast works. False on a deployment without it (mode=hindcast would answer 503)")
    start: str | None = Field(default=None, description="First date in the file (YYYY-MM-DD), when available")
    end: str | None = Field(default=None, description="Last date in the file (YYYY-MM-DD), when available")
    days: int | None = Field(default=None, description="Number of days in the file, when available")


class FertiliserAvailability(BaseModel):
    forecast: Literal[True] = Field(description="Forecast mode needs only the weather provider (can still fail at request time)")
    hindcast: HindcastAvailability = Field(description="Whether the last-season (recorded rain) mode can be offered")
