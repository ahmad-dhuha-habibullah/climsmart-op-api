from .apsim import ApsimEngine
from .base import DayResult, EngineInfo, EngineParams, WaterEngine
from .bucket import BucketEngine

ENGINES = {"fao56-single-bucket": BucketEngine}  # 'apsim-x' needs a result file and a fixed point, so it is built explicitly (ApsimEngine(...)), not looked up by name

__all__ = ["ApsimEngine", "BucketEngine", "DayResult", "EngineInfo", "EngineParams",
           "WaterEngine", "ENGINES"]
