"""FAO-56 style single-bucket daily soil-water balance (transparent, ~30 lines of maths).

Following FAO Irrigation & Drainage Paper 56 (Allen et al., 1998), root-zone depletion Dr:

    TAW = root_zone_capacity_mm                    (total plant-available water)
    RAW = p * TAW                                  (readily available water)
    Ks  = 1                           if Dr_prev <= RAW
        = (TAW - Dr_prev)/(TAW - RAW) otherwise, clamped to [0, 1]      (eq. 84)
    ETc_adj = Ks * Kc * ET0
    Peff    = rain * (1 - runoff_fraction)
    Dr      = clamp(Dr_prev - Peff + ETc_adj, 0, TAW)                   (eq. 85, no irrigation)
    Drainage = max(0, Peff - ETc_adj - Dr_prev)    (excess above field capacity)

Simplifications (all documented in the API response `assumptions`): one layer, fixed root
depth/capacity, no capillary rise, no canopy interception, no runoff unless configured,
Kc constant over the period, no irrigation.
"""
from __future__ import annotations

from ..providers.base import DailyWeather
from .base import DayResult, EngineInfo, EngineParams, WaterEngine


class BucketEngine(WaterEngine):
    info = EngineInfo(
        name="fao56-single-bucket",
        version="0.1.0",
        description="FAO-56 style single-layer daily soil-water balance with a Ks stress factor.",
        assumptions=[
            "Single soil layer with fixed plant-available water capacity (default 115 mm per 1 m "
            "root zone; literature range about 90-150 mm; see config/agronomy.yaml).",
            "Mature oil palm crop coefficient Kc = 1.0 (FAO-56 palm / Monzon 2022; adjustable).",
            "Stress starts when deficit exceeds p x capacity, p = 0.65 (FAO-56 palm; adjustable).",
            "Rain is fully effective unless runoff_fraction is set; excess drains below root zone.",
            "No irrigation, capillary rise, groundwater, interception or slope effects.",
            "Soil starts at a stated initial deficit (default 0 = full) at the start of the spin-up window.",
            "Weather inputs are gridded model data for the point, not on-site measurements.",
        ],
    )

    def run(self, weather: list[DailyWeather], params: EngineParams) -> list[DayResult]:
        taw = params.root_zone_capacity_mm
        raw = params.depletion_fraction_p * taw
        dr = min(max(params.initial_depletion_mm, 0.0), taw)
        out: list[DayResult] = []
        for w in weather:
            if dr <= raw:
                ks = 1.0
            else:
                ks = max(0.0, min(1.0, (taw - dr) / (taw - raw))) if taw > raw else 0.0
            etc_pot = params.kc * w.et0_mm
            etc_act = ks * etc_pot
            peff = w.rain_mm * (1.0 - params.runoff_fraction)
            drainage = max(0.0, peff - etc_act - dr)
            dr = min(max(dr - peff + etc_act, 0.0), taw)
            out.append(DayResult(
                date=w.date, is_forecast=w.is_forecast, rain_mm=w.rain_mm, et0_mm=w.et0_mm,
                etc_potential_mm=etc_pot, etc_actual_mm=etc_act, drainage_mm=drainage,
                deficit_mm=dr, deficit_fraction=dr / taw, ks=ks, stress_ratio=1.0 - ks,
            ))
        return out
