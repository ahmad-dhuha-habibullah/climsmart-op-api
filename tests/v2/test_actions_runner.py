"""GitHub Actions runner: dispatch (mocked GitHub API), callback auth, success/failure, ephemeral refusal, write guard."""
import gzip
import io
import sqlite3
import tarfile

import httpx
import respx

from tests.v2.test_workflow import API, _config

SECRET = "test-secret-not-real"
DISPATCH = "https://api.github.com/repos/o/r/actions/workflows/apsim-run.yml/dispatches"


def _actions(v2):
    s = v2.ctx.settings
    s.apsim_runner, s.github_repo, s.github_dispatch_token = "actions", "o/r", "ghp_test"
    s.apsim_callback_secret, s.public_base_url = SECRET, "https://example.test"


def _submit(v2, mock_weather, status=204):
    _actions(v2)
    p, cfg = _config(v2, mock_weather)
    with respx.mock(assert_all_called=True) as m:
        route = m.post(DISPATCH).mock(return_value=httpx.Response(status))
        r = v2.post(f"{API}/apsim/runs", json={"config_id": cfg["id"]})
        body = route.calls[0].request.content
    assert r.status_code == 202
    return r.json(), body


def _fake_db() -> bytes:
    con = sqlite3.connect(":memory:")
    con.execute('CREATE TABLE "ClimSmartDaily" ("Clock.Today" TEXT, "OilPalm.LAI" REAL)')
    con.executemany("INSERT INTO ClimSmartDaily VALUES (?, ?)", [("2023-01-01 00:00:00", 0.1), ("2023-01-02 00:00:00", 0.2)])
    con.commit()
    return gzip.compress(con.serialize())


def test_dispatch_sets_running_and_passes_inputs(v2, mock_weather):
    acc, body = _submit(v2, mock_weather)
    assert b'"run_id"' in body and b"https://example.test/api/v2/apsim/runs/" in body
    run = v2.get(f"{API}/apsim/runs/{acc['run_id']}").json()
    job = v2.get(f"{API}/jobs/{acc['job']['id']}").json()
    assert run["status"] == "running" and job["status"] == "running"


def test_dispatch_failure_is_reported(v2, mock_weather):
    acc, _ = _submit(v2, mock_weather, status=401)
    job = v2.get(f"{API}/jobs/{acc['job']['id']}").json()
    assert job["status"] == "failed" and job["error"]["code"] == "actions_dispatch_failed"


def test_bundle_and_callback_require_secret(v2, mock_weather):
    acc, _ = _submit(v2, mock_weather)
    rid = acc["run_id"]
    assert v2.get(f"{API}/apsim/runs/{rid}/bundle").status_code == 401
    assert v2.get(f"{API}/apsim/runs/{rid}/bundle", headers={"X-ClimSmart-Callback-Secret": "wrong"}).status_code == 401
    b = v2.get(f"{API}/apsim/runs/{rid}/bundle", headers={"X-ClimSmart-Callback-Secret": SECRET})
    names = tarfile.open(fileobj=io.BytesIO(b.content)).getnames()
    assert sorted(names) == ["simulation.apsimx", "weather.met"]
    r = v2.post(f"{API}/apsim/runs/{rid}/callback", data={"status": "succeeded"}, headers={"X-ClimSmart-Callback-Secret": "x"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_callback_secret"


def test_callback_success_registers_outputs(v2, mock_weather):
    acc, _ = _submit(v2, mock_weather)
    rid = acc["run_id"]
    r = v2.post(f"{API}/apsim/runs/{rid}/callback", headers={"X-ClimSmart-Callback-Secret": SECRET},
                data={"status": "succeeded", "apsim_version": "APSIM 2026.4.8030.0", "seconds": "3.1"},
                files={"db": ("simulation.db.gz", _fake_db()), "log": ("apsim.log", b"ok")})
    assert r.status_code == 200 and r.json()["status"] == "succeeded"
    run = v2.get(f"{API}/apsim/runs/{rid}").json()
    assert run["status"] == "succeeded" and run["provenance"]["apsim"]["version"] == "APSIM 2026.4.8030.0"
    assert run["outputs"][0]["row_count"] == 2
    assert v2.get(f"{API}/jobs/{acc['job']['id']}").json()["status"] == "succeeded"
    # a second callback for the same run is refused
    again = v2.post(f"{API}/apsim/runs/{rid}/callback", headers={"X-ClimSmart-Callback-Secret": SECRET},
                    data={"status": "succeeded"})
    assert again.status_code == 409


def test_callback_failure_keeps_log_and_fails(v2, mock_weather):
    acc, _ = _submit(v2, mock_weather)
    rid = acc["run_id"]
    r = v2.post(f"{API}/apsim/runs/{rid}/callback", headers={"X-ClimSmart-Callback-Secret": SECRET},
                data={"status": "failed", "message": "boom"}, files={"log": ("apsim.log", b"Fatal error")})
    assert r.json()["status"] == "failed"
    run = v2.get(f"{API}/apsim/runs/{rid}").json()
    assert run["status"] == "failed" and run["log_artifact_id"] and run["error"]["message"] == "boom"
    assert v2.get(f"{API}/jobs/{acc['job']['id']}").json()["status"] == "failed"


def test_actions_refused_on_ephemeral_storage(v2, mock_weather):
    _actions(v2)
    v2.ctx.settings.ephemeral = True
    _, cfg = _config(v2, mock_weather)
    r = v2.post(f"{API}/apsim/runs", json={"config_id": cfg["id"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "persistent_storage_required"


def test_write_guard(v2):
    v2.ctx.settings.write_token = "tok"
    assert v2.post(f"{API}/plantations", json={"name": "x"}).status_code == 401
    assert v2.post(f"{API}/plantations", json={"name": "x"}, headers={"X-API-Key": "tok"}).status_code == 201
    assert v2.get(f"{API}/plantations").status_code == 200
