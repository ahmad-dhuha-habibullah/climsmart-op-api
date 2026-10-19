"""Settings read from environment variables (see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_THRESHOLDS = ROOT / "config" / "thresholds.yaml"


def _split(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


@dataclass
class Settings:
    api_keys: list[str] = field(default_factory=list)  # empty = open demo mode
    rate_limit_per_min: int = 60  # 0 disables
    cache_ttl_seconds: int = 3600
    cors_origins: list[str] = field(default_factory=lambda: ["*"])
    thresholds_file: Path = DEFAULT_THRESHOLDS
    open_meteo_api_key: str | None = None
    agronomy_file: Path | None = None  # None = config/agronomy.yaml
    # Reverse proxies whose X-Forwarded-For header may be believed (IPs/CIDRs). Empty = trust none.
    trusted_proxies: list[str] = field(default_factory=list)
    provider_retries: int = 2  # extra attempts after the first on 429/5xx/timeout
    provider_backoff_seconds: float = 0.5
    stale_max_seconds: int = 48 * 3600  # serve cached weather up to this old if the provider is down (labelled stale)
    provider_budget_seconds: float = 9.0  # total time for all upstream attempts of one request (fail fast, well under Vercel's limit)
    forecast_snapshot_dir: Path | None = None  # None = data/forecast_snapshot/ (committed public-weather last-good files)
    log_level: str = "INFO"
    apsim_results_file: Path | None = None  # None = data/apsim_results/mambang_muda_latest.json
    apsim_series_file: Path | None = None  # None = data/apsim_results/mambang_muda_series.json
    hindcast_rain_file: Path | None = None  # None = data/hindcast/founder_daily_rain_2024_2025.csv (local, not committed)
    fertiliser_timing_file: Path | None = None  # None = config/fertiliser_timing.yaml

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ
        tf = env.get("CLIMSMART_THRESHOLDS_FILE", "").strip()
        return cls(
            api_keys=_split(env.get("CLIMSMART_API_KEYS", "")),
            rate_limit_per_min=int(env.get("CLIMSMART_RATE_LIMIT_PER_MIN", "60") or 60),
            cache_ttl_seconds=int(env.get("CLIMSMART_CACHE_TTL_SECONDS", "3600") or 3600),
            cors_origins=_split(env.get("CLIMSMART_CORS_ORIGINS", "*")) or ["*"],
            thresholds_file=Path(tf) if tf else DEFAULT_THRESHOLDS,
            open_meteo_api_key=env.get("OPEN_METEO_API_KEY", "").strip() or None,
            trusted_proxies=_split(env.get("CLIMSMART_TRUSTED_PROXIES", "")),
            provider_retries=max(0, int(env.get("CLIMSMART_PROVIDER_RETRIES", "2") or 2)),
            provider_backoff_seconds=float(env.get("CLIMSMART_PROVIDER_BACKOFF_SECONDS", "0.5") or 0.5),
            stale_max_seconds=int(env.get("CLIMSMART_STALE_MAX_SECONDS", "172800") or 172800),
            provider_budget_seconds=float(env.get("CLIMSMART_PROVIDER_BUDGET_SECONDS", "9") or 9),
            forecast_snapshot_dir=Path(sd) if (sd := env.get("CLIMSMART_FORECAST_SNAPSHOT_DIR", "").strip()) else None,
            log_level=(env.get("CLIMSMART_LOG_LEVEL", "INFO") or "INFO").upper(),
            apsim_results_file=Path(ar) if (ar := env.get("CLIMSMART_APSIM_RESULTS_FILE", "").strip()) else None,
            apsim_series_file=Path(sf) if (sf := env.get("CLIMSMART_APSIM_SERIES_FILE", "").strip()) else None,
            hindcast_rain_file=Path(hf) if (hf := env.get("CLIMSMART_HINDCAST_RAIN_FILE", "").strip()) else None,
            fertiliser_timing_file=Path(ff) if (ff := env.get("CLIMSMART_FERTILISER_TIMING_FILE", "").strip()) else None,
            agronomy_file=Path(af) if (af := env.get("CLIMSMART_AGRONOMY_FILE", "").strip()) else None,
        )
