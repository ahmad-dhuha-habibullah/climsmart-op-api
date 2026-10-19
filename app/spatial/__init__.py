"""GeoJSON geometry validation and metrics (shapely + pyproj geodesic area on WGS84)."""
from __future__ import annotations

from typing import Any

from pyproj import Geod
from shapely.geometry import shape
from shapely.validation import explain_validity

from app.core.errors import invalid

GEOD = Geod(ellps="WGS84")
MAX_AREA_HA = 500_000.0  # a single plot larger than this is almost certainly a mistake (wrong CRS / whole region)


def _check_ring(ring: Any, where: str, errors: list) -> None:
    if not isinstance(ring, list) or len(ring) < 4:
        errors.append({"path": where, "msg": "a linear ring needs at least 4 positions"})
        return
    for i, pos in enumerate(ring):
        if not (isinstance(pos, list | tuple) and len(pos) >= 2 and all(isinstance(v, int | float) for v in pos[:2])):
            errors.append({"path": f"{where}[{i}]", "msg": "position must be [lon, lat]"})
            return
        lon, lat = pos[0], pos[1]
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            errors.append({"path": f"{where}[{i}]", "msg": f"[{lon}, {lat}] is outside WGS84 lon/lat bounds "
                                                          "(coordinates must be EPSG:4326 degrees, lon first)"})
            return
    if list(ring[0][:2]) != list(ring[-1][:2]):
        errors.append({"path": where, "msg": "ring is not closed (first and last positions differ)"})


def extract_geometry(obj: Any) -> dict:
    """Accept a GeoJSON Geometry, a Feature, or a FeatureCollection with exactly one feature."""
    if not isinstance(obj, dict) or "type" not in obj:
        raise invalid("invalid_geometry", "GeoJSON object with a 'type' is required")
    t = obj["type"]
    if t == "FeatureCollection":
        feats = obj.get("features") or []
        if len(feats) != 1:
            raise invalid("invalid_geometry", f"FeatureCollection must contain exactly one feature here (got {len(feats)});"
                                              " use the import endpoint for several plots")
        return extract_geometry(feats[0])
    if t == "Feature":
        return extract_geometry(obj.get("geometry"))
    return obj


def validate_geometry(obj: Any) -> tuple[dict, dict]:
    """Return (normalised geometry, metrics) or raise a 422 ApiError with per-ring details."""
    g = extract_geometry(obj)
    t = g.get("type")
    if t not in ("Polygon", "MultiPolygon"):
        raise invalid("invalid_geometry", f"geometry type must be Polygon or MultiPolygon (got {t})")
    coords = g.get("coordinates")
    errors: list = []
    polys = [coords] if t == "Polygon" else coords
    if not isinstance(polys, list) or not polys:
        raise invalid("invalid_geometry", "coordinates are missing or empty")
    for pi, poly in enumerate(polys):
        if not isinstance(poly, list) or not poly:
            errors.append({"path": f"polygon[{pi}]", "msg": "polygon needs at least an exterior ring"})
            continue
        for ri, ring in enumerate(poly):
            _check_ring(ring, f"polygon[{pi}].ring[{ri}]", errors)
    if errors:
        raise invalid("invalid_geometry", "geometry failed validation", errors)
    geom = shape({"type": t, "coordinates": coords})
    if not geom.is_valid:
        raise invalid("invalid_geometry", "geometry is not valid (OGC simple features)",
                      [{"path": "geometry", "msg": explain_validity(geom)}])
    area_m2, _ = GEOD.geometry_area_perimeter(geom)
    area_ha = abs(area_m2) / 10_000
    if area_ha <= 0:
        raise invalid("invalid_geometry", "geometry has zero area")
    if area_ha > MAX_AREA_HA:
        raise invalid("invalid_geometry", f"area {area_ha:,.0f} ha exceeds the {MAX_AREA_HA:,.0f} ha limit for one plot")
    c = geom.representative_point() if not geom.centroid.within(geom) else geom.centroid
    minx, miny, maxx, maxy = geom.bounds
    return ({"type": t, "coordinates": coords},
            {"area_ha": round(area_ha, 4), "centroid_lat": round(c.y, 6), "centroid_lon": round(c.x, 6),
             "bbox": [minx, miny, maxx, maxy]})
