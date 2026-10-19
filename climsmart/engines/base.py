"""Engine interface. A water-stress engine turns daily weather into daily soil-water state.

The v1 engine is the transparent FAO-56 style bucket (engines/bucket.py). A process-based
APSIM-X engine can implement the same interface later (see engines/apsim.py, a stub).
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field

from ..agronomy import DEFAULT_KC, DEFAULT_P, DEFAULT_TAW_MM
from ..providers.base import DailyWeather


@dataclass(frozen=True)
class EngineParams:
    root_zone_capacity_mm: float = DEFAULT_TAW_MM  # TAW, mm (config/agronomy.yaml)
    kc: float = DEFAULT_KC  # crop coefficient, mature oil palm (config/agronomy.yaml)
    depletion_fraction_p: float = DEFAULT_P  # fraction of TAW usable before stress (config/agronomy.yaml)
    initial_depletion_mm: float = 0.0  # soil-water deficit at the first day (0 = full store)
    runoff_fraction: float = 0.0  # share of rain lost to runoff (0 = ignored)


@dataclass(frozen=True)
class DayResult:
    date: object  # datetime.date
    is_forecast: bool
    rain_mm: float
    et0_mm: float
    etc_potential_mm: float  # kc * et0 (no stress)
    etc_actual_mm: float  # ks * kc * et0
    drainage_mm: float  # water lost below root zone
    deficit_mm: float  # end-of-day soil-water deficit (depletion), 0..TAW
    deficit_fraction: float  # deficit / TAW
    ks: float  # 1 = no stress, 0 = full stress (FAO-56 water-stress coefficient)
    stress_ratio: float  # 1 - ks


@dataclass(frozen=True)
class EngineInfo:
    name: str
    version: str
    description: str
    assumptions: list[str] = field(default_factory=list)


class WaterEngine(abc.ABC):
    info: EngineInfo

    @abc.abstractmethod
    def run(self, weather: list[DailyWeather], params: EngineParams) -> list[DayResult]: ...
