"""Contract tests for the existing /v1 API. They pin the public behaviour so the v2 work cannot break it."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from climsmart.config import Settings
from climsmart.providers.base import ProviderError
from climsmart.providers.openmeteo import FREE_URL, OpenMeteoProvider

FIX = Path(__file__).parent / "fixtures"
SITE = {"lat": 2.6235, "lon": 99.6922}


def test_openapi_v1_is_unchanged(v1_client):
    """The combined app (v1 + mounted v2) must publish exactly the same /openapi.json as before v2."""
    baseline = json.loads((FIX / "openapi_v1_baseline.json").read_text())
    assert v1_client.get("/openapi.json").json() == baseline


def test_health(v1_client):
    r = v1_client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@pytest.mark.parametrize("path", ["/docs", "/redoc"])
def test_docs_pages(v1_client, path):
    assert v1_client.get(path).status_code == 200


def test_forecast(v1_client):
    r = v1_client.get("/v1/forecast", params={**SITE, "past_days": 2, "forecast_days": 3})
    assert r.status_code == 200
    j = r.json()
    assert set(j) >= {"meta", "location", "daily"}
    assert len(j["daily"]) == 5
    assert {d["kind"] for d in j["daily"]} == {"past", "forecast"}
    assert j["meta"]["sources"][0]["name"] == "Open-Meteo"


def test_water_balance(v1_client):
    r = v1_client.get("/v1/water-balance", params={**SITE, "days": 7})
    assert r.status_code == 200
    j = r.json()
    assert j["parameters"]["root_zone_capacity_mm"] > 0
    day = j["daily"][0]
    assert {"deficit_mm", "ks", "stress_ratio", "etc_actual_mm"} <= set(day)


def test_water_balance_rejects_initial_depletion_above_capacity(v1_client):
    r = v1_client.get("/v1/water-balance", params={**SITE, "root_zone_capacity_mm": 50, "initial_depletion_mm": 60})
    assert r.status_code == 422
    assert "detail" in r.json()


def test_outlook_and_csv(v1_client):
    r = v1_client.get("/v1/outlook", params=SITE)
    assert r.status_code == 200
    j = r.json()
    assert j["summary"]["peak_category"] in {"low", "moderate", "high", "critical"}
    assert [c["name"] for c in j["thresholds"]["classes"]] == ["low", "moderate", "high", "critical"]
    c = v1_client.get("/v1/outlook.csv", params=SITE)
    assert c.status_code == 200
    assert c.headers["content-type"].startswith("text/csv")
    assert c.text.startswith("# ClimSmart-OP daily outlook")
    header = [ln for ln in c.text.splitlines() if not ln.startswith("#")][0]
    assert header.startswith("source,source_version,date,kind,category")


def test_apsim_outlook(v1_client):
    r = v1_client.get("/v1/apsim-outlook", params={"compare": "false"})
    assert r.status_code == 200
    j = r.json()
    assert j["apsim"]["source"] == "apsimx"
    assert j["meta"]["precomputed"] is True
    csv = v1_client.get("/v1/apsim-outlook.csv")
    assert csv.status_code == 200 and "apsimx" in csv.text


def test_apsim_outlook_with_comparison_uses_provider(v1_client, fake_provider):
    r = v1_client.get("/v1/apsim-outlook")
    assert r.status_code == 200
    assert r.json()["comparison"]["source"] == "simple-water-balance"
    assert fake_provider.calls >= 1


def test_apsim_outlook_other_point_404(v1_client):
    r = v1_client.get("/v1/apsim-outlook", params={"lat": 0, "lon": 0})
    assert r.status_code == 404
    assert "detail" in r.json()


def test_apsim_cost_benefit_and_csv(v1_client):
    r = v1_client.get("/v1/apsim-cost-benefit", params={"ffb_price_per_tonne": 120})
    assert r.status_code == 200
    j = r.json()
    assert any(s["is_base"] for s in j["scenarios"])
    assert v1_client.get("/v1/apsim-cost-benefit.csv").status_code == 200


def test_apsim_series(v1_client):
    r = v1_client.get("/v1/apsim-series", params={"group": "planting_year"})
    assert r.status_code == 200
    assert all(s["group"] == "planting_year" for s in r.json()["scenarios"])


def test_fertiliser_timing_forecast_and_availability(v1_client):
    r = v1_client.get("/v1/fertiliser-timing")
    assert r.status_code == 200
    assert r.json()["meta"]["mode"] == "forecast"
    a = v1_client.get("/v1/fertiliser-timing/availability")
    assert a.status_code == 200 and a.json()["forecast"] is True


def test_validation_error_shape_is_fastapi_default(v1_client):
    r = v1_client.get("/v1/forecast", params={"lat": 999, "lon": 0})
    assert r.status_code == 422
    assert isinstance(r.json()["detail"], list)  # v1 keeps FastAPI's error shape (v2 uses an envelope)


def _client(provider, **settings):
    from app.main import create_combined_app
    return TestClient(create_combined_app(provider=provider, settings=Settings(**settings)))


def test_api_key_required_when_configured(fake_provider):
    c = _client(fake_provider, api_keys=["k1"], rate_limit_per_min=0)
    assert c.get("/v1/outlook", params=SITE).status_code == 401
    assert c.get("/v1/outlook", params=SITE, headers={"X-API-Key": "k1"}).status_code == 200
    assert c.get("/health").status_code == 200


def test_rate_limit(fake_provider):
    c = _client(fake_provider, rate_limit_per_min=2)
    codes = [c.get("/v1/forecast", params=SITE).status_code for _ in range(3)]
    assert codes[:2] == [200, 200] and codes[2] == 429


def test_provider_failure_maps_to_502(tmp_path):
    from tests.conftest import FakeProvider
    c = _client(FakeProvider(ProviderError("down", kind="upstream")), rate_limit_per_min=0)
    r = c.get("/v1/forecast", params=SITE)
    assert r.status_code == 502


def test_provider_rate_limited_maps_to_503(tmp_path):
    from tests.conftest import FakeProvider
    c = _client(FakeProvider(ProviderError("busy", kind="rate_limited", retry_after=7)), rate_limit_per_min=0)
    r = c.get("/v1/forecast", params=SITE)
    assert r.status_code == 503 and r.headers["Retry-After"] == "7"


@respx.mock
async def test_openmeteo_provider_parses_mocked_response():
    payload = {"latitude": 2.63, "longitude": 99.67, "elevation": 19, "timezone": "Asia/Jakarta",
               "daily": {"time": ["2026-10-17", "2026-10-18", "2026-10-19"],
                         "precipitation_sum": [1.0, 2.0, None], "et0_fao_evapotranspiration": [4.0, 4.1, None],
                         "temperature_2m_max": [31, 32, 33], "temperature_2m_min": [23, 23, 24],
                         "precipitation_probability_max": [None, 40, 50]}}
    respx.get(FREE_URL).mock(return_value=httpx.Response(200, json=payload))
    s = await OpenMeteoProvider(retries=0).get_daily(2.63, 99.67, 1, 2)
    assert [d.rain_mm for d in s.days] == [1.0, 2.0]  # trailing null forecast day dropped, never invented
    assert s.extra["trailing_days_trimmed"] == 1


@respx.mock
async def test_openmeteo_provider_gap_is_an_error():
    payload = {"daily": {"time": ["2026-10-17", "2026-10-18", "2026-10-19"],
                         "precipitation_sum": [1.0, None, 3.0], "et0_fao_evapotranspiration": [4.0, None, 4.0]}}
    respx.get(FREE_URL).mock(return_value=httpx.Response(200, json=payload))
    with pytest.raises(ProviderError):
        await OpenMeteoProvider(retries=0).get_daily(2.63, 99.67, 1, 2)
