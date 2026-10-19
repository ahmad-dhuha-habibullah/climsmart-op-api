"""APSIM-X engine: serves PRECOMPUTED APSIM Next Generation runs through the `WaterEngine` interface.

APSIM is never run inside a request. `scripts/run_apsim.py` builds a recent weather file, runs the
founder's oil-palm model offline (about 5 s per scenario) and writes a compact JSON result file
(`data/apsim_results/mambang_muda_latest.json`). This engine reads that file.

Limits (also in every API response): only the Mambang Muda point has APSIM results; the `weather`
and `params` arguments are NOT used to compute anything (APSIM already ran with its own weather and
soil), so `run()` ignores them and says so in `info.assumptions`.

Mapping to `DayResult` (APSIM numbers, not bucket numbers):
- rain_mm / et0_mm        <- APSIM Weather.Rain / SoilWater.Eo
- etc_potential_mm        <- palm potential EP + soil evaporation Es
- etc_actual_mm           <- Es + palm EP + understorey EP (APSIM "ET")
- deficit_mm / fraction   <- plant-available-water capacity (DUL-LL15, 225 mm) minus available water
- ks / stress_ratio       <- APSIM palm water-stress factor fw / (1 - fw)
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from ..apsim.results import ApsimResults
from ..providers.base import DailyWeather
from .base import DayResult, EngineInfo, EngineParams, WaterEngine


class ApsimEngine(WaterEngine):
    def __init__(self, results: ApsimResults | None = None, results_path: Path | None = None,
                 scenario: str = "as_is"):
        self._results = results or ApsimResults.load(results_path)  # raises ApsimResultsUnavailable
        self._scenario = scenario
        e = self._results.data["engine"]
        self.info = EngineInfo(
            name="apsim-x", version=str(e.get("version")),
            description="APSIM Next Generation oil-palm model run offline for the Mambang Muda point; "
                        "results precomputed, indicative, not validated.",
            assumptions=["Only the Mambang Muda point has APSIM results.",
                         "weather and params arguments are ignored: APSIM ran with its own weather and soil.",
                         *self._results.data["limitations"]])

    @property
    def supported_point(self) -> dict:
        return self._results.data["supported_point"]

    def run(self, weather: list[DailyWeather], params: EngineParams) -> list[DayResult]:
        sc = self._results.scenario(self._scenario)
        taw = self._results.data["soil"]["plant_available_water_mm"]
        out = []
        for d in sc["daily"]:
            out.append(DayResult(
                date=date.fromisoformat(d["date"]), is_forecast=d["kind"] == "forecast",
                rain_mm=d["rain_mm"], et0_mm=d["eo_mm"],
                etc_potential_mm=d["potential_ep_mm"] + d["es_mm"], etc_actual_mm=d["et_mm"],
                drainage_mm=d["drainage_mm"], deficit_mm=round(taw - d["available_water_mm"], 1),
                deficit_fraction=d["depletion_fraction"], ks=d["fw"], stress_ratio=d["stress_index"]))
        return out
