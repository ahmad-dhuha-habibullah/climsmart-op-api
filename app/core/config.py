"""v2 settings from environment variables (all optional for local dev; see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _bool(v: str | None, default: bool) -> bool:
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class V2Settings:
    database_url: str | None
    artifact_dir: Path
    job_mode: str  # "inline" (run in the API process after the response) or "worker" (separate process)
    apsim_command: str | None  # e.g. "dotnet /opt/apsim/Models.dll" or "/usr/local/bin/Models"
    apsim_docker_image: str | None  # e.g. "apsiminitiative/apsimng:8030" (used only if docker is on PATH)
    apsim_timeout_seconds: int
    template_dir: Path
    auto_migrate: bool
    open_meteo_api_key: str | None
    weather_timeout_seconds: float
    on_vercel: bool

    @property
    def storage_configured(self) -> bool:
        """On Vercel the filesystem is ephemeral: v2 needs an explicit DATABASE_URL there."""
        return bool(self.database_url)

    @classmethod
    def from_env(cls) -> V2Settings:
        env = os.environ
        on_vercel = bool(env.get("VERCEL"))
        db = env.get("DATABASE_URL", "").strip() or None
        if db is None and not on_vercel:
            db = f"sqlite:///{ROOT / 'data' / 'v2' / 'climsmart.db'}"
        default_art = Path("/tmp/climsmart-artifacts") if on_vercel else ROOT / "data" / "v2" / "artifacts"
        return cls(
            database_url=db,
            artifact_dir=Path(env.get("ARTIFACT_DIR", "").strip() or default_art),
            job_mode=(env.get("JOB_MODE", "").strip() or "inline").lower(),
            apsim_command=env.get("APSIM_COMMAND", "").strip() or None,
            apsim_docker_image=env.get("APSIM_DOCKER_IMAGE", "").strip() or None,
            apsim_timeout_seconds=int(env.get("APSIM_TIMEOUT_SECONDS", "900") or 900),
            template_dir=Path(env.get("APSIM_TEMPLATE_DIR", "").strip() or ROOT / "app" / "apsim_templates"),
            auto_migrate=_bool(env.get("DB_AUTO_MIGRATE"), True),
            open_meteo_api_key=env.get("OPEN_METEO_API_KEY", "").strip() or None,
            weather_timeout_seconds=float(env.get("WEATHER_TIMEOUT_SECONDS", "30") or 30),
            on_vercel=on_vercel,
        )
