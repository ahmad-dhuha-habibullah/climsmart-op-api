"""Load the precomputed APSIM result file written by scripts/run_apsim.py."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ..config import ROOT

DEFAULT_RESULTS = ROOT / "data" / "apsim_results" / "mambang_muda_latest.json"
REQUIRED = ("schema_version", "source", "supported_point", "engine", "run", "scenarios", "limitations")
STALE_AFTER_HOURS = 48


class ApsimResultsUnavailable(RuntimeError):
    """No usable APSIM result file on this server."""


class ApsimResults:
    def __init__(self, data: dict, path: Path):
        self.data, self.path = data, path

    @classmethod
    def load(cls, path: Path | None = None) -> "ApsimResults":
        path = Path(path) if path else DEFAULT_RESULTS
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise ApsimResultsUnavailable(f"no readable APSIM result file at {path.name}") from e
        missing = [k for k in REQUIRED if k not in data]
        if missing or data.get("source") != "apsimx" or not data.get("scenarios"):
            raise ApsimResultsUnavailable(f"APSIM result file is incomplete (missing {missing})")
        return cls(data, path)

    def age_hours(self, now: datetime | None = None) -> float:
        started = datetime.fromisoformat(self.data["run"]["started_utc"])
        return round(((now or datetime.now(timezone.utc)) - started).total_seconds() / 3600, 1)

    def scenario(self, sid: str) -> dict:
        for s in self.data["scenarios"]:
            if s["id"] == sid:
                return s
        raise KeyError(sid)

    @property
    def scenario_ids(self) -> list[str]:
        return [s["id"] for s in self.data["scenarios"]]
