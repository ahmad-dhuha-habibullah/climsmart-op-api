"""Load the saved APSIM series file written by scripts/extract_apsim_series.py."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ..config import ROOT

DEFAULT_SERIES = ROOT / "data" / "apsim_results" / "mambang_muda_series.json"
REQUIRED = ("schema_version", "source", "status", "supported_point", "engine", "run", "units", "scenarios")


class SeriesUnavailable(RuntimeError):
    pass


def load_series(path: Path | None = None) -> dict:
    path = Path(path) if path else DEFAULT_SERIES
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SeriesUnavailable(f"no readable APSIM series file at {path.name}") from e
    missing = [k for k in REQUIRED if k not in data]
    if missing or data.get("source") != "apsimx" or not data.get("scenarios"):
        raise SeriesUnavailable(f"APSIM series file is incomplete (missing {missing})")
    return data


def age_hours(data: dict, now: datetime | None = None) -> float:
    started = datetime.fromisoformat(data["run"]["started_utc"])
    return round(((now or datetime.now(timezone.utc)) - started).total_seconds() / 3600, 1)
