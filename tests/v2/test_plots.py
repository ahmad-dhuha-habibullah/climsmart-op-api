from __future__ import annotations

import copy
import json

import pytest

from tests.conftest import SQUARE

API = "/api/v2"


def _plantation(v2, name="Kebun Test"):
    r = v2.post(f"{API}/plantations", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()


def test_plantation_crud_and_pagination(v2):
    for i in range(3):
        _plantation(v2, f"K{i}")
    r = v2.get(f"{API}/plantations", params={"limit": 2})
    j = r.json()
    assert j["total"] == 3 and len(j["items"]) == 2 and j["limit"] == 2 and j["offset"] == 0
    pid = j["items"][0]["id"]
    assert v2.patch(f"{API}/plantations/{pid}", json={"description": "x"}).json()["description"] == "x"
    assert v2.delete(f"{API}/plantations/{pid}").status_code == 204
    r = v2.get(f"{API}/plantations/{pid}")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_create_plot_computes_area_centroid_bbox(v2):
    pln = _plantation(v2)
    r = v2.post(f"{API}/plantations/{pln['id']}/plots", json={"name": "A1", "geometry": SQUARE})
    assert r.status_code == 201, r.text
    p = r.json()
    # 0.01 deg x 0.01 deg near the equator ~ 1.11 km x 1.11 km ~ 122.9 ha
    assert 120 < p["area_ha"] < 125
    assert p["centroid"] == {"lat": pytest.approx(2.625, abs=1e-4), "lon": pytest.approx(99.695, abs=1e-4)}
    assert p["bbox"] == [99.69, 2.62, 99.70, 2.63]
    feat = v2.get(f"{API}/plots/{p['id']}/geojson").json()
    assert feat["type"] == "Feature" and feat["geometry"]["type"] == "Polygon"
    lst = v2.get(f"{API}/plots", params={"plantation_id": pln["id"], "bbox": "99.6,2.6,99.8,2.7"}).json()
    assert lst["total"] == 1
    assert v2.get(f"{API}/plots", params={"bbox": "0,0,1,1"}).json()["total"] == 0


@pytest.mark.parametrize("mutate,needle", [
    (lambda g: g["coordinates"][0].pop(), "not closed"),
    (lambda g: g.update(coordinates=[[[99.69, 2.62], [99.7, 2.62], [99.69, 2.62]]]), "at least 4"),
    (lambda g: g.update(coordinates=[[[500, 2.62], [99.7, 2.62], [99.7, 2.63], [500, 2.62]]]), "WGS84"),
    (lambda g: g.update(coordinates=[[[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]]]), "valid"),  # bow-tie
    (lambda g: g.update(type="LineString"), "Polygon or MultiPolygon"),
    (lambda g: g.update(coordinates=[[[400000, 300000], [400100, 300000], [400100, 300100], [400000, 300000]]]),
     "WGS84"),  # projected metres instead of degrees
])
def test_invalid_geometries_rejected_with_envelope(v2, mutate, needle):
    pln = _plantation(v2)
    g = copy.deepcopy(SQUARE)
    mutate(g)
    r = v2.post(f"{API}/plantations/{pln['id']}/plots", json={"name": "bad", "geometry": g})
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "invalid_geometry"
    assert needle in json.dumps(err)


def test_feature_and_multipolygon_accepted(v2):
    pln = _plantation(v2)
    mp = {"type": "MultiPolygon", "coordinates": [SQUARE["coordinates"],
                                                  [[[99.71, 2.62], [99.72, 2.62], [99.72, 2.63], [99.71, 2.62]]]]}
    r = v2.post(f"{API}/plantations/{pln['id']}/plots",
                json={"name": "M", "geometry": {"type": "Feature", "geometry": mp, "properties": {}}})
    assert r.status_code == 201 and r.json()["geometry"]["type"] == "MultiPolygon"


def test_geojson_upload_and_export(v2):
    pln = _plantation(v2)
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"name": "B1", "planting_year": 2012}, "geometry": SQUARE},
        {"type": "Feature", "properties": {"name": "B2"},
         "geometry": {"type": "Polygon", "coordinates": [[[99.71, 2.62], [99.72, 2.62], [99.72, 2.63],
                                                          [99.71, 2.62]]]}}]}
    r = v2.post(f"{API}/plantations/{pln['id']}/plots/upload",
                files={"file": ("blocks.geojson", json.dumps(fc), "application/geo+json")})
    assert r.status_code == 201, r.text
    assert r.json()["count"] == 2
    exp = v2.get(f"{API}/plantations/{pln['id']}/plots.geojson")
    assert exp.headers["content-type"].startswith("application/geo+json")
    assert [f["properties"]["name"] for f in exp.json()["features"]] == ["B1", "B2"]
    # all-or-nothing: one bad feature saves nothing
    bad = copy.deepcopy(fc)
    bad["features"][0]["properties"]["name"] = "C1"
    bad["features"][1]["properties"]["name"] = "C2"
    bad["features"][1]["geometry"]["coordinates"][0].pop()
    r = v2.post(f"{API}/plantations/{pln['id']}/plots/import", json={"geojson": bad})
    assert r.status_code == 422 and r.json()["error"]["details"][0]["feature"] == 1
    assert v2.get(f"{API}/plots", params={"plantation_id": pln["id"]}).json()["total"] == 2


def test_duplicate_plot_name_conflict(v2):
    pln = _plantation(v2)
    body = {"name": "A1", "geometry": SQUARE}
    assert v2.post(f"{API}/plantations/{pln['id']}/plots", json=body).status_code == 201
    r = v2.post(f"{API}/plantations/{pln['id']}/plots", json=body)
    assert r.status_code == 409 and r.json()["error"]["code"] == "duplicate_plot_name"


def test_validation_error_envelope(v2):
    r = v2.post(f"{API}/plantations", json={"name": ""})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"
    assert r.json()["error"]["details"][0]["loc"][-1] == "name"


def test_v2_openapi_and_health(v2):
    spec = v2.get(f"{API}/openapi.json").json()
    assert "/plots/{plot_id}/met-files" in spec["paths"]
    h = v2.get(f"{API}/system/health").json()
    assert h["database"] == "sqlite" and h["apsim"]["available"] is False
