"""Agronomic defaults (TAW, Kc, p) loaded from config/agronomy.yaml, with their source notes."""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel

from .config import ROOT

DEFAULT_AGRONOMY = ROOT / "config" / "agronomy.yaml"
CUTOFF_STATUS = "no citable source, provisional"


class ParamSpec(BaseModel):
    value: float
    plausible_range: list[float]
    unit: str
    status: str
    source: str


class Agronomy(BaseModel):
    version: str
    status: str
    parameters: dict[str, ParamSpec]

    def value(self, name: str) -> float:
        return self.parameters[name].value


def load_agronomy(path: Path | str = DEFAULT_AGRONOMY) -> Agronomy:
    a = Agronomy(**yaml.safe_load(Path(path).read_text(encoding="utf-8")))
    for need in ("root_zone_capacity_mm", "kc", "depletion_fraction_p"):
        if need not in a.parameters:
            raise ValueError(f"agronomy file missing parameter {need}")
    return a


_D = load_agronomy()
DEFAULT_TAW_MM = _D.value("root_zone_capacity_mm")
DEFAULT_KC = _D.value("kc")
DEFAULT_P = _D.value("depletion_fraction_p")
