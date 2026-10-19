"""Seed a clearly labelled DEMO plantation/plot with a real MET file and two real APSIM runs.

APSIM runs locally (APSIM_COMMAND) on the exact APSIMX/MET artifacts the server generated, then the results are
registered with POST /apsim/configs/{id}/import-run (write token). Usage:
    APSIM_COMMAND=... V2_WRITE_TOKEN=... python scripts/seed_demo.py --base https://climsmart-op-api.vercel.app
"""
import argparse
import gzip
import os
import sys
import tempfile
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.adapters.apsim import runner  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--base", default="http://127.0.0.1:8077")
a = ap.parse_args()
H = {"X-API-Key": os.environ.get("V2_WRITE_TOKEN", "")}
c = httpx.Client(base_url=a.base + "/api/v2", timeout=120, headers=H)


def chk(r, *ok):
    if r.status_code not in ok:
        raise SystemExit(f"{r.request.method} {r.request.url.path} -> {r.status_code} {r.text[:300]}")
    return r.json()


def wait(job_id):
    for _ in range(120):
        j = c.get(f"/jobs/{job_id}").json()
        if j["status"] in ("succeeded", "failed", "cancelled"):
            return j
        time.sleep(2)
    raise SystemExit("job timeout")


pl = chk(c.post("/plantations", json={
    "name": "DEMO — Kebun Mambang Muda (example)", "code": "DEMO-MM", "organization": "Demo data (not a real estate record)",
    "description": "Demonstration plantation. The plot outline is illustrative; weather is real Open-Meteo model data; "
                   "APSIM outputs are real model runs, indicative and not validated."}), 201)
ring = [[99.690, 2.620], [99.700, 2.620], [99.700, 2.630], [99.690, 2.630], [99.690, 2.620]]
plot = chk(c.post(f"/plantations/{pl['id']}/plots", json={"name": "DEMO Blok A1", "planting_year": 2023,
                                                          "geometry": {"type": "Polygon", "coordinates": [ring]},
                                                          "attributes": {"demo": True}}), 201)
print("plot", plot["id"], f"{plot['area_ha']:.1f} ha")
acc = chk(c.post(f"/plots/{plot['id']}/met-files", json={"start_date": "2023-01-01", "end_date": "2024-12-31",
                                                          "name": "DEMO 2023-2024 model-only"}), 202)
j = wait(acc["job"]["id"])
met = chk(c.get(f"/met-files/{j['resource_id']}"), 200)
print("met", met["id"], met["status"])
assert met["status"] == "valid", met["validation"]
tpl = chk(c.get("/apsim/templates"), 200)["items"][0]
base_params = {"clock_start": "2023-01-01", "clock_end": "2024-12-31", "sowing_date": "2023-01-01"}
met_bytes = c.get(met["artifact"]["download_url"].replace("/api/v2", "")).content
for name, pop in (("DEMO baseline 135 palms/ha", 135), ("DEMO dense 160 palms/ha", 160)):
    cfg = chk(c.post(f"/plots/{plot['id']}/apsim-configs", json={
        "template_id": tpl["id"], "met_file_id": met["id"], "name": name, "params": {**base_params, "population": pop}}), 201)
    apsimx = c.get(cfg["apsimx_artifact"]["download_url"].replace("/api/v2", "")).content
    wd = Path(tempfile.mkdtemp())
    res = runner.run(apsimx.decode(), met_bytes, wd, apsim_command=os.environ["APSIM_COMMAND"], docker_image=None,
                     timeout=600)
    out = chk(c.post(f"/apsim/configs/{cfg['id']}/import-run", data={
        "label": name, "apsimx_sha256": cfg["apsimx_artifact"]["sha256"], "met_sha256": met["artifact"]["sha256"],
        "apsim_version": res.runtime.get("apsim_version") or "unknown", "seconds": str(res.seconds),
        "runner": "ClimSmart dev box (APSIM NG docker image 8030)"},
        files={"db": ("simulation.db.gz", gzip.compress(res.db_path.read_bytes())), "log": ("apsim.log", res.log.encode())}),
        201)
    print("run", out["run_id"], out["tables"])
print("done plot_id=" + plot["id"])
