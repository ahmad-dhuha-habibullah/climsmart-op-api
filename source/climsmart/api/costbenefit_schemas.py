"""Response models for /v1/apsim-cost-benefit."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .apsim_schemas import ApsimEngineInfo, ApsimPoint, ApsimRun


class CBMeta(BaseModel):
    physical_numbers_source: Literal["apsimx"] = Field(description="Yield, N applied, irrigation, leaching and fw all come from APSIM-X runs")
    economics_source: Literal["user-inputs"] = Field(description="Every price comes from the caller (or a labelled placeholder default); nothing is market data")
    status: str = Field(description="Honesty label", examples=["INDICATIVE, NOT VALIDATED: APSIM-X process-model output, not calibrated for this site."])
    generated_at: str = Field(description="When this response was produced (UTC, ISO 8601)")
    precomputed: bool = Field(description="APSIM scenarios are precomputed offline; only the arithmetic runs per request")
    supported_point: ApsimPoint = Field(description="APSIM data exists ONLY for this point")
    note: str = Field(description="One-sentence reminder: APSIM numbers are precomputed for one point, prices are yours, indicative, no ROI claim")


class CBInput(BaseModel):
    name: str = Field(description="Query parameter name", examples=["ffb_price_per_tonne"])
    value: float = Field(description="Value used", examples=[100.0])
    unit: str = Field(description="Unit (currency = the `currency` label of this response)", examples=["currency per tonne FFB"])
    is_example_default: bool = Field(description="True when you did not send this input: the value is an example placeholder, replace it with your own cost")
    note: str = Field(description="What the input means")


class CBSensitivityCase(BaseModel):
    case: str = Field(description="Which prices were changed", examples=["unfavourable (FFB low, costs high)"])
    net_difference: float = Field(description="net_difference per ha per year under that case, currency units")


class CBYieldWhatIf(BaseModel):
    yield_response_factor: float = Field(description="Multiplier on APSIM-X's yield change (1 = as simulated)")
    d_ffb_t_ha: float = Field(description="APSIM-X yield change scaled by the factor, t FFB/ha/yr")
    net_difference: float = Field(description="net_difference per ha per year with the scaled yield change")


class CBBreakEven(BaseModel):
    ffb_price_per_tonne: float | None = Field(description="FFB price at which net_difference = 0, or null if none exists")
    yield_response_factor: float | None = Field(description="Multiple of APSIM-X's simulated yield change at which net_difference = 0 (at the given FFB price), or null")
    reading: str = Field(description="How to read the two numbers (direction in which the scenario is better than base)")


class CBPerYear(BaseModel):
    year: int = Field(description="Hindcast year")
    d_ffb_t_ha: float = Field(description="Scenario minus base FFB that year, t/ha (APSIM-X, 5 decimals)")
    d_n_applied_kg_ha: float | None = Field(default=None, description="Scenario minus base N applied that year, kg N/ha (APSIM-X)")
    d_irrigation_mm: float | None = Field(default=None, description="Scenario minus base irrigation that year, mm (APSIM-X): lets a client compute the water cost per year")
    net_difference: float = Field(description="net_difference that year with the same prices")


class CBScenario(BaseModel):
    id: str = Field(examples=["n_1_5x"], description="Scenario id")
    label: str = Field(description="Short human label")
    description: str = Field(description="Exactly what was simulated")
    is_base: bool = Field(description="True for the base scenario (founder's management)")
    params: dict[str, Any] = Field(description="Management parameters of the APSIM-X run")
    absolute_mean_per_year: dict[str, float | None] = Field(description="Mean over the full hindcast years, per ha per year: ffb_t_ha, n_applied_kg_ha, "
                                                                          "irrigation_mm, n_leached_no3_kg_ha, drainage_mm, mean_fw, days_fw_below_0_95 (all source=apsimx)")
    delta_vs_base_per_ha_year: dict[str, float | None] | None = Field(default=None, description="scenario minus base (null for the base): d_ffb_t_ha, d_n_applied_kg_ha, "
                                                                                              "d_irrigation_mm, d_irrigation_m3_ha, d_n_leached_no3_kg_ha (leaching change, no money value attached), d_drainage_mm, d_mean_fw, d_days_fw_below_0_95")
    money_vs_base_per_ha_year: dict[str, float | None] | None = Field(default=None, description="d_revenue, d_extra_harvest_haul_cost, d_fertiliser_cost, d_water_cost, "
                                                                                              "d_irrigation_fixed_cost, net_difference (currency per ha per year; >0 = scenario better than base for these inputs)")
    sensitivity: list[CBSensitivityCase] | None = Field(default=None, description="net_difference under price changes of +/- sensitivity_pct")
    yield_whatif: list[CBYieldWhatIf] | None = Field(default=None, description="net_difference if APSIM-X's yield change were smaller/larger (illustration, not an error estimate)")
    break_even: CBBreakEven | None = Field(default=None, description="Break-even FFB price and yield factor")
    per_year_hindcast: list[CBPerYear] | None = Field(default=None, description="Same comparison for each hindcast year: shows year-to-year spread")
    net_difference_by_weather_variant: dict[str, dict[str, float | None]] | None = Field(default=None, description="d_ffb_t_ha, d_n_applied_kg_ha, d_irrigation_mm and net_difference under the OTHER weather variant(s), same prices")


class CostBenefitResponse(BaseModel):
    meta: CBMeta
    engine: ApsimEngineInfo = Field(description="Exact APSIM build that produced the physical numbers")
    run: ApsimRun = Field(description="When the offline APSIM run was made and whether it is stale")
    currency: str = Field(description="Label only, no conversion is done", examples=["currency units"])
    weather_variant: str = Field(description="as_delivered or rain_scaled: which APSIM weather input the numbers come from")
    weather_variants: dict[str, str] = Field(description="What each variant is")
    inputs: list[CBInput] = Field(description="Every price used, flagged when it is an example placeholder")
    sensitivity_pct: float = Field(description="Price sensitivity used, percent")
    yield_whatif_pct: float = Field(description="Yield what-if used, percent")
    evaluation: dict[str, Any] = Field(description="Period the numbers cover (hindcast years, management start, partial year note), assumed planting year")
    levers: dict[str, Any] = Field(description="Which management levers exist in the founder's model, which are varied here and which do not exist")
    units: dict[str, str] = Field(description="Units and the exact APSIM report columns behind each physical number")
    formulas: list[str] = Field(description="Every formula used, so the numbers can be recomputed by hand")
    scenarios: list[CBScenario] = Field(description="Base first, then each alternative with its difference to base")
    partial_year: dict[str, Any] = Field(description="Most recent partial year per scenario (APSIM-X, includes forecast days): context only, not annualised, not used in money")
    limitations: list[str] = Field(description="What this view cannot tell you")
