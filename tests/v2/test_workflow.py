"""MET generation, APSIM config validation, jobs, runs (runtime missing -> honest failure; real run if available)."""
from __future__ import annotations

import hashlib
import os
import shutil

import pytest
import respx

from tests.conftest import SQUARE, synthetic_openmeteo

API = "/api/v2"
PERIOD = {"start_date": "2023-01-01", "end_date": "2024-12-31"}
PARAMS = {"clock_start": "2023-01-01", "clock_end": "2024-12-31", "sowing_date": "2023-01-01"}


def _plot(v2):
    pln = v2.post(f"{API}/plantations", json={"name": "K"}).json()
    return v2.post(f"{API}/plantations/{pln['id']}/plots", json={"name": "A1", "geometry": SQUARE}).json()


def _met(v2, plot_id, **kw):
    r = v2.post(f"{API}/plots/{plot_id}/met-files", json={**PERIOD, **kw})
    assert r.status_code == 202, r.text
    assert r.headers["location"].startswith("/api/v2/jobs/")
    job = v2.get(f"{API}/jobs/{r.json()['job']['id']}").json()  # inline mode: already ran in the background task
    return r.json()["met_file_id"], job


def _template_id(v2):
    t = v2.get(f"{API}/apsim/templates").json()["items"]
    assert len(t) == 1
    return t[0]["id"]


def test_plot_weather_has_provenance(v2, mock_weather):
    p = _plot(v2)
    r = v2.get(f"{API}/plots/{p['id']}/weather", params={"start_date": "2024-01-01", "end_date": "2024-01-10"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert len(j["days"]) == 10 and j["days"][0]["source"] == "open-meteo-archive"
    assert j["provenance"]["point"] == {"lat": p["centroid"]["lat"], "lon": p["centroid"]["lon"]}
    assert j["units"]["radn"] == "MJ/m2/day"


def test_met_generation_valid_with_checksum_and_artifact(v2, mock_weather):
    p = _plot(v2)
    mid, job = _met(v2, p["id"])
    assert job["status"] == "succeeded", job
    m = v2.get(f"{API}/met-files/{mid}").json()
    assert m["status"] == "valid" and m["strategy"] == "model_only"
    v = m["validation"]
    assert v["expected_days"] == 731 and v["complete_days"] == 731 and v["coverage_pct"] == 100
    assert m["tav"] is not None and m["amp"] is not None
    art = m["artifact"]
    assert art["plot_id"] == p["id"] and art["kind"] == "met"
    body = v2.get(art["download_url"]).content
    assert hashlib.sha256(body).hexdigest() == art["sha256"] and len(body) == art["size_bytes"]
    text = body.decode()
    assert text.startswith("[weather.met.weather]") and "tav =" in text and "year day radn maxt mint rain" in text
    assert text.count("\n") > 731
    assert art["provenance"]["strategy"] == "model_only"
    logs = v2.get(f"{API}/jobs/{job['id']}/logs").text
    assert "MET artifact" in logs


def test_met_with_gap_is_invalid_and_not_filled(v2):
    from app.adapters.weather import openmeteo
    from climsmart.apsim.weather import ARCHIVE_URL
    openmeteo._cache.clear()
    p = _plot(v2)
    with respx.mock() as r:
        r.get(ARCHIVE_URL).mock(side_effect=synthetic_openmeteo({"2023-03-05", "2023-03-06"}))
        mid, job = _met(v2, p["id"])
    assert job["status"] == "failed" and job["error"]["code"] == "met_validation_failed"
    m = v2.get(f"{API}/met-files/{mid}").json()
    assert m["status"] == "invalid" and m["artifact_id"] is None
    err = [e for e in m["validation"]["errors"] if e["code"] == "missing_values"][0]
    assert err["variable"] == "radn" and err["dates"] == ["2023-03-05", "2023-03-06"]


def test_sensor_assimilation_is_reported_unavailable(v2):
    p = _plot(v2)
    r = v2.post(f"{API}/plots/{p['id']}/met-files", json={**PERIOD, "strategy": "sensor_assimilation"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "strategy_not_available"


def test_config_validation_and_linkage(v2, mock_weather):
    p = _plot(v2)
    mid, _ = _met(v2, p["id"])
    tid = _template_id(v2)
    url = f"{API}/plots/{p['id']}/apsim-configs"
    bad = v2.post(url, json={"template_id": tid, "met_file_id": mid, "params": {**PARAMS, "foo": 1}})
    assert bad.status_code == 422 and bad.json()["error"]["details"][0]["param"] == "foo"
    bad = v2.post(url, json={"template_id": tid, "met_file_id": mid,
                             "params": {**PARAMS, "clock_end": "2025-06-30"}})
    assert "inside the MET" in bad.text
    bad = v2.post(url, json={"template_id": tid, "met_file_id": mid, "params": {**PARAMS, "sowing_date": "2022-01-01"}})
    assert "never sown" in bad.text
    bad = v2.post(url, json={"template_id": tid, "met_file_id": mid, "params": {**PARAMS, "population": 5}})
    assert bad.status_code == 422
    r = v2.post(url, json={"template_id": tid, "met_file_id": mid, "name": "base", "params": PARAMS})
    assert r.status_code == 201, r.text
    cfg = r.json()
    assert cfg["version"] == 1 and cfg["status"] == "generated"
    met_art = v2.get(f"{API}/met-files/{mid}").json()["artifact"]
    prov = cfg["apsimx_artifact"]["provenance"]
    assert prov["met"]["sha256"] == met_art["sha256"] and prov["template"]["sha256"]
    apsimx = v2.get(cfg["apsimx_artifact"]["download_url"]).text
    assert '"FileName": "weather.met"' in apsimx and "2023-01-01T00:00:00" in apsimx and "ClimSmartDaily" in apsimx
    r2 = v2.post(url, json={"template_id": tid, "met_file_id": mid, "name": "base",
                            "params": {**PARAMS, "population": 143}})
    assert r2.json()["version"] == 2


def test_config_rejects_invalid_met(v2):
    from app.adapters.weather import openmeteo
    from climsmart.apsim.weather import ARCHIVE_URL
    openmeteo._cache.clear()
    p = _plot(v2)
    with respx.mock() as r:
        r.get(ARCHIVE_URL).mock(side_effect=synthetic_openmeteo({"2023-03-05"}))
        mid, _ = _met(v2, p["id"])
    r = v2.post(f"{API}/plots/{p['id']}/apsim-configs",
                json={"template_id": _template_id(v2), "met_file_id": mid, "params": PARAMS})
    assert r.status_code == 409 and r.json()["error"]["code"] == "met_not_valid"


def _config(v2, mock_weather, **params):
    p = _plot(v2)
    mid, _ = _met(v2, p["id"])
    cfg = v2.post(f"{API}/plots/{p['id']}/apsim-configs",
                  json={"template_id": _template_id(v2), "met_file_id": mid, "params": {**PARAMS, **params}}).json()
    return p, cfg


def test_run_fails_honestly_without_runtime_then_retry(v2, mock_weather):
    p, cfg = _config(v2, mock_weather)
    r = v2.post(f"{API}/apsim/runs", json={"config_id": cfg["id"]})
    assert r.status_code == 202
    job = v2.get(f"{API}/jobs/{r.json()['job']['id']}").json()
    assert job["status"] == "failed" and job["error"]["code"] == "apsim_runtime_unavailable"
    run = v2.get(f"{API}/apsim/runs/{r.json()['run_id']}").json()
    assert run["status"] == "failed" and run["outputs"] == [] and run["db_artifact_id"] is None
    assert v2.get(f"{API}/apsim/runs/{run['id']}/outputs/DailyOutput").status_code == 404
    rt = v2.post(f"{API}/jobs/{job['id']}/retry")
    assert rt.status_code == 202 and rt.json()["retry_of"] == job["id"] and rt.json()["attempt"] == 2
    assert v2.post(f"{API}/jobs/{job['id']}/cancel").status_code == 409  # already terminal


def test_worker_mode_claim_and_cancel(v2, mock_weather):
    from app.services import jobs
    v2.ctx.settings.job_mode = "worker"
    p, cfg = _config(v2, mock_weather)  # MET job stays queued in worker mode
    jl = v2.get(f"{API}/jobs", params={"status": "queued"}).json()
    assert jl["total"] == 1
    jid = jl["items"][0]["id"]
    assert jobs.claim(v2.ctx, jid) == jid
    assert jobs.claim(v2.ctx, jid) is None  # second claimer loses the compare-and-set
    jobs.execute(v2.ctx, jid)
    assert v2.get(f"{API}/jobs/{jid}").json()["status"] == "succeeded"
    met = v2.get(f"{API}/plots/{p['id']}/met-files").json()["items"][0]
    cfg = v2.post(f"{API}/plots/{p['id']}/apsim-configs",
                  json={"template_id": _template_id(v2), "met_file_id": met["id"], "params": PARAMS}).json()
    r = v2.post(f"{API}/apsim/runs", json={"config_id": cfg["id"]}).json()
    c = v2.post(f"{API}/jobs/{r['job']['id']}/cancel").json()
    assert c["status"] == "cancelled"
    assert v2.get(f"{API}/apsim/runs/{r['run_id']}").json()["status"] == "cancelled"


def test_plot_summary_lists_everything(v2, mock_weather):
    p, cfg = _config(v2, mock_weather)
    v2.post(f"{API}/apsim/runs", json={"config_id": cfg["id"]})
    s = v2.get(f"{API}/plots/{p['id']}/summary").json()
    assert len(s["met_files"]) == 1 and len(s["configs"]) == 1 and len(s["runs"]) == 1
    assert {a["kind"] for a in s["artifacts"]} == {"met", "apsimx"}
    assert {j["type"] for j in s["history"]} == {"met.generate", "apsim.run"}


APSIM = os.environ.get("APSIM_COMMAND")


@pytest.mark.skipif(not APSIM, reason="APSIM_COMMAND not set (real APSIM runtime not available)")
def test_real_apsim_run_outputs_and_compare(v2, mock_weather):
    """Runs the real APSIM NG binary on SYNTHETIC test weather (mocked Open-Meteo) and checks outputs."""
    v2.ctx.settings.apsim_command = APSIM
    p, cfg = _config(v2, mock_weather)
    cfg2 = v2.post(f"{API}/plots/{p['id']}/apsim-configs",
                   json={"template_id": _template_id(v2), "met_file_id": cfg["met_file_id"],
                         "params": {**PARAMS, "population": 160}}).json()
    runs = []
    for c in (cfg, cfg2):
        r = v2.post(f"{API}/apsim/runs", json={"config_id": c["id"]}).json()
        job = v2.get(f"{API}/jobs/{r['job']['id']}").json()
        assert job["status"] == "succeeded", v2.get(f"{API}/jobs/{r['job']['id']}/logs").text
        runs.append(r["run_id"])
    run = v2.get(f"{API}/apsim/runs/{runs[0]}").json()
    tables = {o["table_name"]: o for o in run["outputs"]}
    assert tables["ClimSmartDaily"]["row_count"] == 731
    assert run["provenance"]["apsim"]["apsim_version"].startswith("APSIM")
    q = v2.get(f"{API}/apsim/runs/{runs[0]}/outputs/ClimSmartDaily",
               params={"vars": "OilPalm.LAI,Weather.Rain", "start_date": "2024-01-01", "end_date": "2024-01-31"}).json()
    assert q["columns"] == ["Clock.Today", "OilPalm.LAI", "Weather.Rain"] and q["total"] == 31
    assert q["provenance"]["met"]["sha256"]
    cmp = v2.get(f"{API}/apsim/runs/compare", params={"ids": ",".join(runs), "vars": "OilPalm.LAI"}).json()
    assert cmp["input_differences"]["params"] == {"population": [135.0, 160.0]}
    assert cmp["input_differences"]["same_met_artifact"] is True
    assert len(cmp["dates"]) == 731
    assert shutil.which  # noqa
