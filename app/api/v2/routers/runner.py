"""Endpoints used by the GitHub Actions APSIM runner (secret-protected, not for browsers)."""
from __future__ import annotations

import gzip
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Header, UploadFile
from fastapi.responses import Response

from app.adapters.apsim import actions
from app.adapters.apsim import template as tpl
from app.api.v2.deps import ctx
from app.core.errors import ApiError, conflict, not_found
from app.models import ApsimConfig, ApsimRun, Artifact, Job, MetFile
from app.services import apsim as apsim_svc
from app.services import jobs
from app.services.context import Context

router = APIRouter(tags=["apsim runner"])
MAX_DB_BYTES = 4 * 1024 * 1024  # Vercel request bodies are capped at 4.5 MB; the workflow gzips the .db


def _running(s, run_id: str) -> ApsimRun:
    r = s.get(ApsimRun, run_id)
    if r is None:
        raise not_found("run", run_id)
    if r.status != "running":
        raise conflict("run_not_running", f"run is '{r.status}', not waiting for a runner")
    return r


@router.get("/apsim/runs/{run_id}/bundle", summary="Runner only: tar.gz with simulation.apsimx + MET (secret header)")
def get_bundle(run_id: str, x_climsmart_callback_secret: str | None = Header(None), c: Context = Depends(ctx)):
    actions.verify_secret(c.settings, x_climsmart_callback_secret)
    with c.db.session() as s:
        r = _running(s, run_id)
        cfg = s.get(ApsimConfig, r.config_id)
        met = s.get(MetFile, cfg.met_file_id)
        apsimx = c.store.get_bytes(s.get(Artifact, cfg.apsimx_artifact_id).storage_key)
        met_bytes = c.store.get_bytes(s.get(Artifact, met.artifact_id).storage_key)
    return Response(actions.bundle(apsimx, met_bytes, tpl.MET_FILENAME), media_type="application/gzip")


@router.post("/apsim/runs/{run_id}/callback", summary="Runner only: report the outcome with the .db (gzip) and log")
async def callback(run_id: str, status: str = Form(..., pattern="^(succeeded|failed)$"), message: str = Form(""),
                   apsim_version: str = Form(""), seconds: float | None = Form(None), runner_url: str = Form(""),
                   db: UploadFile | None = File(None), log: UploadFile | None = File(None),
                   x_climsmart_callback_secret: str | None = Header(None), c: Context = Depends(ctx)):
    actions.verify_secret(c.settings, x_climsmart_callback_secret)
    with c.db.session() as s:
        r = _running(s, run_id)
        job_id = r.job_id
        cancel = s.get(Job, job_id).cancel_requested
    log_text = (await log.read()).decode("utf-8", "replace") if log is not None else ""
    runtime = {"mode": "github-actions", "apsim_version": apsim_version or None, "version": apsim_version or None,
               "runner_url": runner_url or None}
    if cancel:
        apsim_svc._fail(c, run_id, "cancelled", "cancelled by user", None, status="cancelled")
        jobs.finish(c, job_id, "cancelled", error={"code": "cancelled", "message": "cancelled by user"})
        return {"run_id": run_id, "status": "cancelled"}
    if status == "failed" or db is None:
        msg = message or ("runner reported success but sent no output database" if status == "succeeded"
                          else "APSIM failed in GitHub Actions")
        laid = apsim_svc.fail_run_with_log(c, job_id, run_id, "apsim_failed", msg, log_text, runtime)
        jobs.finish(c, job_id, "failed", error={"code": "apsim_failed", "message": msg,
                                                "details": {"log_artifact_id": laid, "runner_url": runner_url}})
        return {"run_id": run_id, "status": "failed"}
    raw = await db.read()
    if len(raw) > MAX_DB_BYTES:
        raise ApiError(413, "payload_too_large", "output database too large")
    workdir = Path(tempfile.mkdtemp(prefix=f"cb-{run_id}-"))
    try:
        p = workdir / "simulation.db"
        try:
            p.write_bytes(gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw)
            result = apsim_svc.complete_run(c, job_id, run_id, p, log_text, seconds, runtime)
        except Exception as e:  # corrupt upload: fail honestly
            msg = f"could not read the uploaded output database: {e.__class__.__name__}"
            apsim_svc.fail_run_with_log(c, job_id, run_id, "apsim_output_invalid", msg, log_text, runtime)
            jobs.finish(c, job_id, "failed", error={"code": "apsim_output_invalid", "message": msg})
            return {"run_id": run_id, "status": "failed"}
        jobs.finish(c, job_id, "succeeded", result=result)
        return {"run_id": run_id, "status": "succeeded", **result}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
