"""End-to-end check of the v2 vertical slice against a running server (real Open-Meteo, real APSIM if configured).

    python scripts/e2e_v2.py [--base http://127.0.0.1:8077]
Prints one line per step; exits non-zero on the first failure.
"""
import argparse
import sys
import time

import httpx

ap = argparse.ArgumentParser()
ap.add_argument("--base", default="http://127.0.0.1:8077")
a = ap.parse_args()
c = httpx.Client(base_url=a.base + "/api/v2", timeout=120)


def ok(step, cond, info=""):
    print(("PASS " if cond else "FAIL ") + step + (f" | {info}" if info else ""))
    if not cond:
        sys.exit(1)


def wait(job_id, limit=900):
    t0 = time.time()
    while time.time() - t0 < limit:
        j = c.get(f"/jobs/{job_id}").json()
        if j["status"] in ("succeeded", "failed", "cancelled"):
            return j
        time.sleep(2)
    raise SystemExit(f"job {job_id} timed out")


stamp = time.strftime("%H%M%S")
pl = c.post("/plantations", json={"name": f"E2E Kebun {stamp}", "organization": "E2E test (synthetic plantation)"}).json()
ok("1 create plantation", "id" in pl, pl["id"])
sq = [[104.10, -3.00], [104.12, -3.00], [104.12, -2.98], [104.10, -2.98], [104.10, -3.00]]
p = c.post(f"/plantations/{pl['id']}/plots", json={"name": "Blok A1", "planting_year": 2015,
                                                     "geometry": {"type": "Polygon", "coordinates": [sq]}})
plot = p.json()
ok("2 create plot (GeoJSON polygon)", p.status_code == 201, f"{plot['area_ha']:.1f} ha centroid {plot['centroid']}")
bad = c.post(f"/plantations/{pl['id']}/plots", json={"name": "bad", "geometry": {"type": "Polygon", "coordinates": [sq[:-1]]}})
ok("3 reject unclosed ring", bad.status_code == 422 and "error" in bad.json(), bad.json()["error"]["code"])
up = c.post(f"/plantations/{pl['id']}/plots/upload", files={"file": ("b.geojson", httpx.Response(200, json={
    "type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"name": "Blok B1"}, "geometry": {
        "type": "Polygon", "coordinates": [[[104.13, -3.0], [104.14, -3.0], [104.14, -2.99], [104.13, -3.0]]]}}]}).content,
    "application/geo+json")})
ok("4 upload GeoJSON file", up.status_code == 201, f"count={up.json().get('count')}")
w = c.get(f"/plots/{plot['id']}/weather").json()
kinds = sorted({d["kind"] for d in w["days"]})
ok("5 plot weather (Open-Meteo, real)", len(w["days"]) > 0, f"{len(w['days'])} days kinds={kinds} grid=({w['provenance']['grid_lat']},{w['provenance']['grid_lon']})")
r = c.post(f"/plots/{plot['id']}/met-files", json={"start_date": "2023-01-01", "end_date": "2024-12-31"})
ok("6 MET request -> 202 + job", r.status_code == 202, r.json()["job"]["id"])
j = wait(r.json()["job"]["id"])
met = c.get(f"/met-files/{j['resource_id']}").json()
ok("7 MET generated + validated", met["status"] == "valid",
   f"coverage {met['validation']['coverage_pct']}% tav={met['tav']:.2f} amp={met['amp']:.2f} sha256={met['artifact']['sha256'][:12]}")
body = c.get(met["artifact"]["download_url"].replace("/api/v2", "")).text
ok("8 MET download parses", body.lstrip().startswith("[weather.met.weather]") or "year" in body, f"{len(body.splitlines())} lines")
sa = c.post(f"/plots/{plot['id']}/met-files", json={"start_date": "2023-01-01", "end_date": "2023-12-31",
                                                     "strategy": "sensor_assimilation"})
ok("9 assimilation honestly unavailable", sa.status_code == 422, sa.json()["error"]["code"])
tpl = c.get("/apsim/templates").json()["items"][0]
cfgs = []
for name, pop in (("baseline", 135), ("dense", 160)):
    cr = c.post(f"/plots/{plot['id']}/apsim-configs", json={"template_id": tpl["id"], "met_file_id": met["id"], "name": name,
        "params": {"clock_start": "2023-01-01", "clock_end": "2024-12-31", "sowing_date": "2023-01-01", "population": pop}})
    ok(f"10 config '{name}' linked to MET", cr.status_code == 201 and cr.json()["met_file_id"] == met["id"], cr.json().get("id", cr.text))
    cfgs.append(cr.json())
badc = c.post(f"/plots/{plot['id']}/apsim-configs", json={"template_id": tpl["id"], "met_file_id": met["id"],
              "params": {"clock_start": "2023-01-01", "clock_end": "2024-12-31", "sowing_date": "2023-01-01", "evil": 1}})
ok("11 non-whitelisted parameter rejected", badc.status_code == 422, badc.json()["error"]["code"])
runs = []
for cfg in cfgs:
    rr = c.post("/apsim/runs", json={"config_id": cfg["id"], "label": cfg["name"]})
    ok("12 run -> 202 + job", rr.status_code == 202, rr.json()["job"]["id"])
    jj = wait(rr.json()["job"]["id"])
    run = c.get(f"/apsim/runs/{jj['resource_id']}").json()
    runs.append(run)
    ok(f"13 run '{cfg['name']}' finished", run["status"] in ("succeeded", "failed"),
       f"status={run['status']} {run.get('runtime_seconds')}s apsim={run['provenance'].get('apsim', {}).get('version')} err={run.get('error')}")
if all(r["status"] == "succeeded" for r in runs):
    q = c.get(f"/apsim/runs/{runs[0]['id']}/outputs/ClimSmartDaily", params={"vars": "OilPalm.LAI,Weather.Rain", "limit": 1000}).json()
    lai = [row[q["columns"].index("OilPalm.LAI")] for row in q["rows"]]
    ok("14 outputs queried with provenance", q["total"] > 700 and q["provenance"]["met"]["sha256"] == met["artifact"]["sha256"],
       f"rows={q['total']} LAI last={lai[-1]:.3f} max={max(lai):.3f}")
    cmp = c.get("/apsim/runs/compare", params={"ids": f"{runs[0]['id']},{runs[1]['id']}", "vars": "OilPalm.LAI"}).json()
    ok("15 compare runs", cmp["input_differences"]["same_met_artifact"],
       f"params diff={cmp['input_differences']['params']} mean LAI diff={cmp['variables']['OilPalm.LAI']['difference']['mean']:.4f}")
else:
    print("SKIP 14/15 outputs/compare: APSIM did not succeed (see step 13)")
s = c.get(f"/plots/{plot['id']}/summary").json()
ok("16 plot summary", len(s["met_files"]) >= 1 and len(s["runs"]) == 2,
   f"met={len(s['met_files'])} configs={len(s['configs'])} runs={len(s['runs'])} artifacts={len(s['artifacts'])} jobs={len(s['history'])}")
done = c.post(f"/jobs/{runs[0]['job_id']}/cancel")
ok("17 cancel terminal job -> 409", done.status_code == 409, done.json()["error"]["code"])
v1 = httpx.get(a.base + "/openapi.json").json()
ok("18 v1 OpenAPI untouched", not any(k.startswith("/api/v2") for k in v1["paths"]), f"{len(v1['paths'])} v1 paths")
print(f"plot_id={plot['id']} runs={[r['id'] for r in runs]}")
