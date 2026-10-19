"""Load and apply outlook thresholds from a YAML/JSON file."""
from __future__ import annotations

import json
from pathlib import Path

import yaml
from pydantic import BaseModel, field_validator


class ThresholdClass(BaseModel):
    name: str
    min: float  # inclusive lower bound on the metric
    label: str
    meaning: str
    status: str = "provisional, not agronomically validated"
    cutoff_source: str = "no citable source, provisional"


class ThresholdSet(BaseModel):
    version: str
    status: str = "provisional, not agronomically validated"
    metric: str = "deficit_fraction_of_taw"
    cutoff_source: str = "no citable source, provisional"
    classes: list[ThresholdClass]

    @field_validator("classes")
    @classmethod
    def _check(cls, v):
        if len(v) < 2:
            raise ValueError("need at least two classes")
        mins = [c.min for c in v]
        if mins != sorted(mins) or len(set(mins)) != len(mins):
            raise ValueError("class minimums must be strictly ascending")
        if mins[0] != 0:
            raise ValueError("first class must start at 0")
        return v

    def classify(self, value: float) -> ThresholdClass:
        chosen = self.classes[0]
        for c in self.classes:
            if value >= c.min:
                chosen = c
        return chosen

    @property
    def order(self) -> list[str]:
        return [c.name for c in self.classes]


def load_thresholds(path: Path | str) -> ThresholdSet:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    data = json.loads(text) if p.suffix == ".json" else yaml.safe_load(text)
    return ThresholdSet(**data)
