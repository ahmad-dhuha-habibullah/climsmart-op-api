"""Entity -> JSON dicts (one place, so every endpoint shows the same fields)."""
from __future__ import annotations

from datetime import date, datetime, timezone

from app.models import ApsimConfig, ApsimRun, ApsimTemplate, Artifact, Job, MetFile, Plantation, Plot, RunOutput


def ts(v: datetime | None) -> str | None:
    if v is None:
        return None
    if v.tzinfo is None:
        v = v.replace(tzinfo=timezone.utc)
    return v.isoformat()


def d(v: date | None) -> str | None:
    return v.isoformat() if v else None


def plantation(p: Plantation, plot_count: int | None = None) -> dict:
    out = {"id": p.id, "name": p.name, "code": p.code, "organization": p.organization, "description": p.description,
           "attributes": p.attributes, "created_at": ts(p.created_at), "updated_at": ts(p.updated_at)}
    if plot_count is not None:
        out["plot_count"] = plot_count
    return out


def plot(p: Plot, geometry: bool = True) -> dict:
    out = {"id": p.id, "plantation_id": p.plantation_id, "name": p.name, "code": p.code, "crop": p.crop,
           "planting_year": p.planting_year, "area_ha": p.area_ha,
           "centroid": {"lat": p.centroid_lat, "lon": p.centroid_lon},
           "bbox": [p.bbox_min_lon, p.bbox_min_lat, p.bbox_max_lon, p.bbox_max_lat],
           "attributes": p.attributes, "created_at": ts(p.created_at), "updated_at": ts(p.updated_at)}
    if geometry:
        out["geometry"] = p.geometry
    return out


def plot_feature(p: Plot) -> dict:
    props = plot(p, geometry=False)
    return {"type": "Feature", "id": p.id, "geometry": p.geometry, "properties": props}


def artifact(a: Artifact | None) -> dict | None:
    if a is None:
        return None
    return {"id": a.id, "kind": a.kind, "filename": a.filename, "media_type": a.media_type, "sha256": a.sha256,
            "size_bytes": a.size_bytes, "plot_id": a.plot_id, "job_id": a.job_id, "provenance": a.provenance,
            "storage_backend": a.storage_backend, "created_at": ts(a.created_at),
            "download_url": f"/api/v2/artifacts/{a.id}/download"}


def job(j: Job, logs: bool = False) -> dict:
    dur = None
    if j.started_at and j.finished_at:
        dur = round((j.finished_at - j.started_at).total_seconds(), 2)
    out = {"id": j.id, "type": j.type, "status": j.status, "params": j.params, "result": j.result, "error": j.error,
           "plot_id": j.plot_id, "resource_type": j.resource_type, "resource_id": j.resource_id, "attempt": j.attempt,
           "retry_of": j.retry_of, "cancel_requested": j.cancel_requested, "created_at": ts(j.created_at),
           "started_at": ts(j.started_at), "finished_at": ts(j.finished_at), "duration_seconds": dur,
           "links": {"self": f"/api/v2/jobs/{j.id}", "logs": f"/api/v2/jobs/{j.id}/logs"}}
    if logs:
        out["logs"] = j.logs
    return out


def met_file(m: MetFile, art: Artifact | None = None) -> dict:
    return {"id": m.id, "plot_id": m.plot_id, "name": m.name, "start_date": d(m.start_date), "end_date": d(m.end_date),
            "strategy": m.strategy, "strategy_params": m.strategy_params, "status": m.status,
            "latitude": m.latitude, "longitude": m.longitude, "tav": m.tav, "amp": m.amp, "validation": m.validation,
            "sources": m.sources, "artifact_id": m.artifact_id, "artifact": artifact(art), "job_id": m.job_id,
            "created_at": ts(m.created_at)}


def template(t: ApsimTemplate) -> dict:
    return {"id": t.id, "slug": t.slug, "version": t.version, "name": t.name, "crop": t.crop,
            "description": t.description, "sha256": t.sha256, "artifact_id": t.artifact_id,
            "apsimx_version": t.apsimx_version, "parameters": t.parameters, "source": t.source,
            "created_at": ts(t.created_at)}


def config(c: ApsimConfig, art: Artifact | None = None) -> dict:
    return {"id": c.id, "plot_id": c.plot_id, "template_id": c.template_id, "met_file_id": c.met_file_id,
            "name": c.name, "version": c.version, "params": c.params, "status": c.status, "validation": c.validation,
            "edits": c.edits, "apsimx_artifact_id": c.apsimx_artifact_id, "apsimx_artifact": artifact(art),
            "created_at": ts(c.created_at)}


def run(r: ApsimRun, outputs: list[RunOutput] | None = None) -> dict:
    out = {"id": r.id, "config_id": r.config_id, "plot_id": r.plot_id, "label": r.label, "status": r.status,
           "job_id": r.job_id, "runtime": r.runtime, "runtime_seconds": r.runtime_seconds,
           "db_artifact_id": r.db_artifact_id, "log_artifact_id": r.log_artifact_id, "provenance": r.provenance,
           "error": r.error, "created_at": ts(r.created_at), "finished_at": ts(r.finished_at)}
    if outputs is not None:
        out["outputs"] = [output(o) for o in outputs]
    return out


def output(o: RunOutput) -> dict:
    return {"id": o.id, "run_id": o.run_id, "table_name": o.table_name, "row_count": o.row_count,
            "first_date": d(o.first_date), "last_date": d(o.last_date), "variables": o.variables}
