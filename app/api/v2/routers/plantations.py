from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Response, UploadFile
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.v2.deps import Page, ctx, paged
from app.core.errors import conflict, invalid, not_found
from app.core.util import new_id, utcnow
from app.models import Plantation, Plot
from app.schemas import PlantationIn, PlantationPatch, PlotImport, PlotIn, PlotPatch
from app.services import serialize as ser
from app.services.context import Context
from app.spatial import validate_geometry

router = APIRouter(tags=["plantations & plots"])


def _plantation(s, pid):
    p = s.get(Plantation, pid)
    if p is None:
        raise not_found("plantation", pid)
    return p


def _plot(s, pid):
    p = s.get(Plot, pid)
    if p is None:
        raise not_found("plot", pid)
    return p


@router.get("/plantations", summary="List plantations")
def list_plantations(q: str | None = None, page: Page = Depends(), c: Context = Depends(ctx)):
    with c.db.session() as s:
        stmt = select(Plantation)
        if q:
            stmt = stmt.where(Plantation.name.ilike(f"%{q}%"))
        total = s.execute(select(func.count()).select_from(stmt.subquery())).scalar()
        rows = s.execute(stmt.order_by(Plantation.name).limit(page.limit).offset(page.offset)).scalars().all()
        counts = dict(s.execute(select(Plot.plantation_id, func.count()).group_by(Plot.plantation_id)).all())
        return paged([ser.plantation(p, counts.get(p.id, 0)) for p in rows], total, page)


@router.post("/plantations", status_code=201, summary="Create a plantation (kebun)")
def create_plantation(body: PlantationIn, c: Context = Depends(ctx)):
    try:
        with c.db.session() as s:
            p = Plantation(id=new_id("pln"), **body.model_dump())
            s.add(p)
            s.flush()
            return ser.plantation(p, 0)
    except IntegrityError:
        raise conflict("duplicate_code", f"plantation code '{body.code}' already exists")


@router.get("/plantations/{plantation_id}", summary="Get a plantation")
def get_plantation(plantation_id: str, c: Context = Depends(ctx)):
    with c.db.session() as s:
        p = _plantation(s, plantation_id)
        n = s.execute(select(func.count()).where(Plot.plantation_id == p.id)).scalar()
        return ser.plantation(p, n)


@router.patch("/plantations/{plantation_id}", summary="Update a plantation")
def patch_plantation(plantation_id: str, body: PlantationPatch, c: Context = Depends(ctx)):
    with c.db.session() as s:
        p = _plantation(s, plantation_id)
        for k, v in body.model_dump(exclude_unset=True).items():
            setattr(p, k, v)
        p.updated_at = utcnow()
        return ser.plantation(p)


@router.delete("/plantations/{plantation_id}", status_code=204, summary="Delete a plantation and its plots")
def delete_plantation(plantation_id: str, c: Context = Depends(ctx)):
    with c.db.session() as s:
        s.delete(_plantation(s, plantation_id))
    return Response(status_code=204)


@router.get("/plots", summary="List plots (filter by plantation, name, bbox)")
def list_plots(plantation_id: str | None = None, q: str | None = None,
               bbox: str | None = None, include_geometry: bool = True,
               page: Page = Depends(), c: Context = Depends(ctx)):
    with c.db.session() as s:
        stmt = select(Plot)
        if plantation_id:
            stmt = stmt.where(Plot.plantation_id == plantation_id)
        if q:
            stmt = stmt.where(Plot.name.ilike(f"%{q}%"))
        if bbox:
            try:
                x0, y0, x1, y1 = (float(v) for v in bbox.split(","))
            except ValueError:
                raise invalid("invalid_bbox", "bbox must be minLon,minLat,maxLon,maxLat")
            stmt = stmt.where(Plot.bbox_max_lon >= x0, Plot.bbox_min_lon <= x1, Plot.bbox_max_lat >= y0,
                              Plot.bbox_min_lat <= y1)
        total = s.execute(select(func.count()).select_from(stmt.subquery())).scalar()
        rows = s.execute(stmt.order_by(Plot.name).limit(page.limit).offset(page.offset)).scalars().all()
        return paged([ser.plot(p, include_geometry) for p in rows], total, page)


def _new_plot(plantation_id: str, body: dict) -> Plot:
    geom, m = validate_geometry(body["geometry"])
    x0, y0, x1, y1 = m["bbox"]
    return Plot(id=new_id("plt"), plantation_id=plantation_id, name=body["name"], code=body.get("code"),
                crop=body.get("crop") or "oil_palm", planting_year=body.get("planting_year"), geometry=geom,
                area_ha=m["area_ha"], centroid_lat=m["centroid_lat"], centroid_lon=m["centroid_lon"],
                bbox_min_lon=x0, bbox_min_lat=y0, bbox_max_lon=x1, bbox_max_lat=y1,
                attributes=body.get("attributes") or {})


@router.post("/plantations/{plantation_id}/plots", status_code=201,
             summary="Create a plot from a GeoJSON Polygon/MultiPolygon (validated; area in ha computed)")
def create_plot(plantation_id: str, body: PlotIn, c: Context = Depends(ctx)):
    try:
        with c.db.session() as s:
            _plantation(s, plantation_id)
            p = _new_plot(plantation_id, body.model_dump())
            s.add(p)
            s.flush()
            return ser.plot(p)
    except IntegrityError:
        raise conflict("duplicate_plot_name", f"plot '{body.name}' already exists in this plantation")


def _import(c: Context, plantation_id: str, fc: dict) -> dict:
    if fc.get("type") != "FeatureCollection":
        fc = {"type": "FeatureCollection", "features": [fc if fc.get("type") == "Feature" else
                                                        {"type": "Feature", "geometry": fc, "properties": {}}]}
    feats = fc.get("features") or []
    if not feats:
        raise invalid("empty_import", "FeatureCollection has no features")
    errors, plots = [], []
    for i, f in enumerate(feats):
        props = f.get("properties") or {}
        name = str(props.get("name") or props.get("Name") or f"Plot {i + 1}")
        try:
            plots.append(_new_plot(plantation_id, {"name": name, "geometry": f, "code": props.get("code"),
                                                   "planting_year": props.get("planting_year"),
                                                   "attributes": {k: v for k, v in props.items()
                                                                  if k not in {"name", "Name", "code", "planting_year"}}}))
        except Exception as e:  # collect every bad feature; nothing is saved unless all are valid
            errors.append({"feature": i, "name": name, "error": getattr(e, "message", str(e)),
                           "details": getattr(e, "details", None)})
    if errors:
        raise invalid("invalid_geometry", f"{len(errors)} of {len(feats)} feature(s) failed validation; nothing saved",
                      errors)
    try:
        with c.db.session() as s:
            _plantation(s, plantation_id)
            for p in plots:
                s.add(p)
            s.flush()
            return {"created": [ser.plot(p, geometry=False) for p in plots], "count": len(plots)}
    except IntegrityError:
        raise conflict("duplicate_plot_name", "a plot name in the file already exists in this plantation")


@router.post("/plantations/{plantation_id}/plots/import", status_code=201,
             summary="Import plots from a GeoJSON FeatureCollection (JSON body)")
def import_plots(plantation_id: str, body: PlotImport, c: Context = Depends(ctx)):
    return _import(c, plantation_id, body.geojson)


@router.post("/plantations/{plantation_id}/plots/upload", status_code=201,
             summary="Upload a .geojson file (multipart) and import its features as plots")
async def upload_plots(plantation_id: str, file: UploadFile = File(...), c: Context = Depends(ctx)):
    raw = await file.read()
    if len(raw) > 4_000_000:
        raise invalid("file_too_large", "GeoJSON upload limited to 4 MB")
    try:
        fc = json.loads(raw)
    except ValueError:
        raise invalid("invalid_json", "file is not valid JSON/GeoJSON")
    return _import(c, plantation_id, fc)


@router.get("/plantations/{plantation_id}/plots.geojson", summary="Export a plantation's plots as GeoJSON")
def export_plantation(plantation_id: str, c: Context = Depends(ctx)):
    with c.db.session() as s:
        _plantation(s, plantation_id)
        rows = s.execute(select(Plot).where(Plot.plantation_id == plantation_id).order_by(Plot.name)).scalars().all()
        fc = {"type": "FeatureCollection", "features": [ser.plot_feature(p) for p in rows]}
    return Response(json.dumps(fc), media_type="application/geo+json",
                    headers={"Content-Disposition": f'attachment; filename="{plantation_id}.geojson"'})


@router.get("/plots/{plot_id}", summary="Get a plot")
def get_plot(plot_id: str, c: Context = Depends(ctx)):
    with c.db.session() as s:
        return ser.plot(_plot(s, plot_id))


@router.get("/plots/{plot_id}/geojson", summary="Export a plot as a GeoJSON Feature")
def plot_geojson(plot_id: str, c: Context = Depends(ctx)):
    with c.db.session() as s:
        return Response(json.dumps(ser.plot_feature(_plot(s, plot_id))), media_type="application/geo+json")


@router.patch("/plots/{plot_id}", summary="Update a plot (geometry is re-validated)")
def patch_plot(plot_id: str, body: PlotPatch, c: Context = Depends(ctx)):
    try:
        with c.db.session() as s:
            p = _plot(s, plot_id)
            data = body.model_dump(exclude_unset=True)
            if "geometry" in data and data["geometry"] is not None:
                n = _new_plot(p.plantation_id, {"name": p.name, "geometry": data.pop("geometry")})
                for k in ("geometry", "area_ha", "centroid_lat", "centroid_lon", "bbox_min_lon", "bbox_min_lat",
                          "bbox_max_lon", "bbox_max_lat"):
                    setattr(p, k, getattr(n, k))
            for k, v in data.items():
                setattr(p, k, v)
            p.updated_at = utcnow()
            s.flush()
            return ser.plot(p)
    except IntegrityError:
        raise conflict("duplicate_plot_name", "a plot with that name already exists in this plantation")


@router.delete("/plots/{plot_id}", status_code=204, summary="Delete a plot and its MET files, configs and runs")
def delete_plot(plot_id: str, c: Context = Depends(ctx)):
    with c.db.session() as s:
        s.delete(_plot(s, plot_id))
    return Response(status_code=204)
