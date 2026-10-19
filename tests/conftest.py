"""Shared fixtures. No test touches the network: weather comes from FakeProvider or respx mocks."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from climsmart.providers.base import DailyWeather, ProviderError, WeatherProvider, WeatherSeries  # noqa: E402


class FakeProvider(WeatherProvider):
    """Deterministic SYNTHETIC weather for tests (clearly not real data)."""

    name = "fake"

    def __init__(self, error: ProviderError | None = None):
        self.error = error
        self.calls = 0

    async def get_daily(self, lat, lon, past_days, forecast_days) -> WeatherSeries:
        self.calls += 1
        if self.error:
            raise self.error
        today = datetime.now(timezone.utc).date()
        days = []
        for i in range(-past_days, forecast_days):
            d = today + timedelta(days=i)
            days.append(DailyWeather(date=d, is_forecast=i >= 0, rain_mm=float((i * 7) % 13),
                                     et0_mm=4.0, temp_max_c=31.0, temp_min_c=23.0,
                                     rain_probability_pct=50.0 if i >= 0 else None))
        return WeatherSeries(latitude=lat, longitude=lon, elevation_m=20.0, timezone="Asia/Jakarta", days=days,
                             source_name="Open-Meteo", source_url="https://open-meteo.com",
                             source_attribution="Weather data by Open-Meteo.com (CC BY 4.0)",
                             source_license_note="synthetic test data",
                             fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))


@pytest.fixture
def fake_provider():
    return FakeProvider()


@pytest.fixture
def v1_settings():
    from climsmart.config import Settings
    return Settings(rate_limit_per_min=0)


@pytest.fixture
def v1_client(fake_provider, v1_settings, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import create_combined_app
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 't.db'}")
    monkeypatch.setenv("ARTIFACT_DIR", str(tmp_path / "artifacts"))
    app = create_combined_app(provider=fake_provider, settings=v1_settings)
    with TestClient(app) as c:
        yield c


def pytest_configure(config):
    os.environ.setdefault("CLIMSMART_LOG_LEVEL", "WARNING")
