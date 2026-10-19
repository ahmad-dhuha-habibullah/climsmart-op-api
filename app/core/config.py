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
    ephemeral: bool = False  # True when storage is a throwaway /tmp sqlite (Vercel without DATABASE_URL)
    apsim_runner: str = "local"  # "local" (APSIM_COMMAND/docker in the worker) or "actions" (GitHub Actions)
    github_dispatch_token: str | None = None
    github_repo: str = "ahmad-dhuha-habibullah/climsmart-op-api"
    github_workflow: str = "apsim-run.yml"
    github_ref: str = "main"
    apsim_callback_secret: str | None = None
    public_base_url: str | None = None
    artifact_store: str = "local"  # "local" or "supabase"
    supabase_url: str | None = None
    supabase_service_key: str | None = None
    supabase_bucket: str = "climsmart-artifacts"
    write_token: str | None = None  # V2_WRITE_TOKEN: when set, v2 writes need it; reads stay public

    @property
    def storage_configured(self) -> bool:
        """A database URL is always resolved (on Vercel without DATABASE_URL: an ephemeral /tmp sqlite)."""
        return bool(self.database_url)

    @classmethod
    def from_env(cls) -> V2Settings:
        env = os.environ
        on_vercel = bool(env.get("VERCEL"))
        db = env.get("DATABASE_URL", "").strip() or None
        ephemeral = False
        if db is None and not on_vercel:
            db = f"sqlite:///{ROOT / 'data' / 'v2' / 'climsmart.db'}"
        elif db is None:  # Vercel: /tmp is per-instance and wiped on cold start -> data is NOT durable
            db, ephemeral = "sqlite:////tmp/climsmart-v2.db", True
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
            ephemeral=ephemeral,
            artifact_store=(env.get("ARTIFACT_STORE", "").strip() or "local").lower(),
            supabase_url=env.get("SUPABASE_URL", "").strip() or None,
            supabase_service_key=env.get("SUPABASE_SERVICE_ROLE_KEY", "").strip() or None,
            supabase_bucket=env.get("SUPABASE_BUCKET", "").strip() or "climsmart-artifacts",
            write_token=env.get("V2_WRITE_TOKEN", "").strip() or None,
            apsim_runner=(env.get("APSIM_RUNNER", "").strip() or "local").lower(),
            github_dispatch_token=env.get("GITHUB_DISPATCH_TOKEN", "").strip() or None,
            github_repo=env.get("GITHUB_REPO", "").strip() or "ahmad-dhuha-habibullah/climsmart-op-api",
            github_workflow=env.get("GITHUB_WORKFLOW_FILE", "").strip() or "apsim-run.yml",
            github_ref=env.get("GITHUB_REF_NAME_DISPATCH", "").strip() or "main",
            apsim_callback_secret=env.get("APSIM_CALLBACK_SECRET", "").strip() or None,
            public_base_url=(env.get("PUBLIC_BASE_URL", "").strip()
                             or (f"https://{env['VERCEL_PROJECT_PRODUCTION_URL']}"
                                 if env.get("VERCEL_PROJECT_PRODUCTION_URL") else None)),
        )
