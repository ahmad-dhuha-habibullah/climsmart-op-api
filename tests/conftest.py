"""Shared fixtures. No test touches the network: weather comes from FakeProvider or respx mocks."""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta, timezone
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


# ----------------------------------------------------------------------------------- v2 fixtures
def synthetic_openmeteo(gap_dates: set[str] | None = None):
    """respx side effect: SYNTHETIC Open-Meteo daily payload for the requested start/end (tests only)."""
    import math

    import httpx

    def _respond(request):
        q = dict(request.url.params)
        start, end = date.fromisoformat(q["start_date"]), date.fromisoformat(q["end_date"])
        times, radn, tmax, tmin, rain, et0 = [], [], [], [], [], []
        d = start
        while d <= end:
            i = d.toordinal()
            times.append(d.isoformat())
            gap = gap_dates and d.isoformat() in gap_dates
            radn.append(None if gap else round(17 + 3 * math.sin(i / 9), 2))
            tmax.append(round(31 + math.sin(i / 30), 2))
            tmin.append(round(23 + 0.5 * math.cos(i / 30), 2))
            rain.append(float(i % 11))
            et0.append(4.0)
            d += timedelta(days=1)
        return httpx.Response(200, json={"latitude": float(q["latitude"]), "longitude": float(q["longitude"]),
                                         "elevation": 20.0, "daily": {
            "time": times, "shortwave_radiation_sum": radn, "temperature_2m_max": tmax, "temperature_2m_min": tmin,
            "precipitation_sum": rain, "et0_fao_evapotranspiration": et0}})
    return _respond


@pytest.fixture
def mock_weather():
    import respx

    from app.adapters.weather import openmeteo
    from climsmart.apsim.weather import ARCHIVE_URL, FORECAST_URL
    openmeteo._cache.clear()
    with respx.mock(assert_all_called=False) as r:
        r.get(ARCHIVE_URL).mock(side_effect=synthetic_openmeteo())
        r.get(FORECAST_URL).mock(side_effect=synthetic_openmeteo())
        yield r


@pytest.fixture
def v2_settings(tmp_path):
    from app.core.config import V2Settings
    s = V2Settings.from_env()
    s.database_url = f"sqlite:///{tmp_path / 'v2.db'}"
    s.artifact_dir = tmp_path / "artifacts"
    s.job_mode = "inline"
    s.apsim_command = None
    s.apsim_docker_image = None
    s.apsim_runner, s.write_token, s.ephemeral, s.artifact_store = "local", None, False, "local"
    return s


@pytest.fixture
def v2(v2_settings, fake_provider):
    from fastapi.testclient import TestClient

    from app.api.v2 import build_context
    from app.main import create_combined_app
    from climsmart.config import Settings
    ctx = build_context(v2_settings)
    app = create_combined_app(provider=fake_provider, settings=Settings(rate_limit_per_min=0), v2_ctx=ctx)
    with TestClient(app) as c:
        c.ctx = ctx
        yield c


SQUARE = {"type": "Polygon", "coordinates": [[[99.69, 2.62], [99.70, 2.62], [99.70, 2.63], [99.69, 2.63],
                                              [99.69, 2.62]]]}
