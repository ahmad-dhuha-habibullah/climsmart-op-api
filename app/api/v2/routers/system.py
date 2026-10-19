from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy import func, select

from app.adapters.apsim import runner
from app.api.v2.deps import Page, ctx, paged
from app.core.errors import not_found
from app.models import Artifact, Job
from app.services import jobs
from app.services import serialize as ser
from app.services.context import Context

router = APIRouter()


@router.get("/jobs", tags=["jobs"], summary="List jobs (filter by status, type, plot)")
def list_jobs(status: str | None = None, type: str | None = None, plot_id: str | None = None,
              page: Page = Depends(), c: Context = Depends(ctx)):
    with c.db.session() as s:
        stmt = select(Job)
        for col, v in ((Job.status, status), (Job.type, type), (Job.plot_id, plot_id)):
            if v:
                stmt = stmt.where(col == v)
        total = s.execute(select(func.count()).select_from(stmt.subquery())).scalar()
        rows = s.execute(stmt.order_by(Job.created_at.desc()).limit(page.limit).offset(page.offset)).scalars()
        return paged([ser.job(j) for j in rows], total, page)


@router.get("/jobs/{job_id}", tags=["jobs"], summary="Job status, timings, result and error")
def get_job(job_id: str, c: Context = Depends(ctx)):
    with c.db.session() as s:
        return ser.job(jobs.get(s, job_id))


@router.get("/jobs/{job_id}/logs", tags=["jobs"], response_class=PlainTextResponse, summary="Job log (plain text)")
def job_logs(job_id: str, c: Context = Depends(ctx)):
    with c.db.session() as s:
        return PlainTextResponse(jobs.get(s, job_id).logs or "")


@router.post("/jobs/{job_id}/cancel", tags=["jobs"], summary="Cancel a queued job or request cancel of a running one")
def cancel_job(job_id: str, c: Context = Depends(ctx)):
    return ser.job(jobs.cancel(c, job_id))


@router.post("/jobs/{job_id}/retry", tags=["jobs"], status_code=202, summary="Retry a failed/cancelled job")
def retry_job(job_id: str, background: BackgroundTasks, c: Context = Depends(ctx)):
    return ser.job(jobs.retry(c, job_id, background))


@router.get("/artifacts", tags=["artifacts"], summary="List artifacts (filter by plot, kind)")
def list_artifacts(plot_id: str | None = None, kind: str | None = None, page: Page = Depends(),
                   c: Context = Depends(ctx)):
    with c.db.session() as s:
        stmt = select(Artifact)
        for col, v in ((Artifact.plot_id, plot_id), (Artifact.kind, kind)):
            if v:
                stmt = stmt.where(col == v)
        total = s.execute(select(func.count()).select_from(stmt.subquery())).scalar()
        rows = s.execute(stmt.order_by(Artifact.created_at.desc()).limit(page.limit).offset(page.offset)).scalars()
        return paged([ser.artifact(a) for a in rows], total, page)


@router.get("/artifacts/{artifact_id}", tags=["artifacts"], summary="Artifact metadata (sha256, size, provenance)")
def get_artifact(artifact_id: str, c: Context = Depends(ctx)):
    with c.db.session() as s:
        a = s.get(Artifact, artifact_id)
        if a is None:
            raise not_found("artifact", artifact_id)
        return ser.artifact(a)


@router.get("/artifacts/{artifact_id}/download", tags=["artifacts"], summary="Download artifact bytes")
def download_artifact(artifact_id: str, c: Context = Depends(ctx)):
    with c.db.session() as s:
        a = s.get(Artifact, artifact_id)
        if a is None:
            raise not_found("artifact", artifact_id)
        data = c.store.get_bytes(a.storage_key)
        return Response(data, media_type=a.media_type, headers={
            "Content-Disposition": f'attachment; filename="{a.filename}"', "X-Content-SHA256": a.sha256})


@router.get("/system/health", tags=["system"], summary="v2 health: database, artifact store, APSIM runtime, job mode")
def health(c: Context = Depends(ctx)):
    with c.db.session() as s:
        queued = s.execute(select(func.count()).where(Job.status == "queued")).scalar()
        running = s.execute(select(func.count()).where(Job.status == "running")).scalar()
    if c.settings.apsim_runner == "actions":
        from app.adapters.apsim import actions
        try:
            actions.check_ready(c.settings)
            apsim = {"available": True, "mode": "github-actions", "workflow": c.settings.github_workflow}
        except Exception as e:  # noqa: BLE001
            apsim = {"available": False, "mode": "github-actions", "reason": getattr(e, "message", str(e)),
                     "details": getattr(e, "details", None)}
        return _health(c, queued, running, apsim)
    try:
        mode, argv = runner.resolve(c.settings.apsim_command, c.settings.apsim_docker_image)
        apsim = {"available": True, "mode": mode, "version": runner.version(mode, argv)}
    except runner.RuntimeUnavailable as e:
        apsim = {"available": False, "reason": str(e)}
    return _health(c, queued, running, apsim)


def _health(c, queued, running, apsim):
    return {"status": "ok", "database": c.db.engine.dialect.name, "artifact_store": c.store.backend,
            "ephemeral_storage": c.settings.ephemeral, "apsim_runner": c.settings.apsim_runner,
            "writes_require_token": bool(c.settings.write_token),
            "job_mode": c.settings.job_mode, "jobs": {"queued": queued, "running": running}, "apsim": apsim}
