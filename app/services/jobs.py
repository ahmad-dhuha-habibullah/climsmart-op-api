"""DB-backed job queue: queued -> running -> succeeded | failed | cancelled.

Claiming is a compare-and-set UPDATE (... WHERE id=? AND status='queued'), portable to SQLite and PostgreSQL, so
several workers can poll the same table safely. Inline mode runs the job in the API process after the response.
"""
from __future__ import annotations

import logging
import os
import socket
import traceback
from collections.abc import Callable

from sqlalchemy import select, update

from app.core.errors import ApiError, conflict, not_found
from app.core.util import new_id, utcnow
from app.models import Job
from app.services.context import Context

log = logging.getLogger("climsmart.jobs")
HANDLERS: dict[str, Callable[[Context, str], dict]] = {}
TERMINAL = {"succeeded", "failed", "cancelled"}
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"


class JobFailed(Exception):
    def __init__(self, code: str, message: str, details=None):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details


class JobCancelled(Exception):
    pass


class JobDeferred(Exception):
    """The handler handed the work to an external runner; the job stays 'running' until finish() is called."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def handler(job_type: str):
    def deco(fn):
        HANDLERS[job_type] = fn
        return fn
    return deco


def create(s, job_type: str, params: dict, *, plot_id=None, resource_type=None, resource_id=None,
           retry_of=None, attempt=1) -> Job:
    j = Job(id=new_id("job"), type=job_type, status="queued", params=params, plot_id=plot_id,
            resource_type=resource_type, resource_id=resource_id, retry_of=retry_of, attempt=attempt,
            logs=f"{utcnow().isoformat()} queued ({job_type})\n")
    s.add(j)
    s.flush()
    return j


def append_log(ctx: Context, job_id: str, line: str) -> None:
    with ctx.db.session() as s:
        j = s.get(Job, job_id)
        if j is not None:
            j.logs = (j.logs or "") + f"{utcnow().isoformat()} {line}\n"


def cancel_requested(ctx: Context, job_id: str) -> bool:
    with ctx.db.session() as s:
        j = s.get(Job, job_id)
        return bool(j and j.cancel_requested)


def claim(ctx: Context, job_id: str | None = None) -> str | None:
    """Atomically move one queued job to running. Returns its id or None."""
    with ctx.db.session() as s:
        if job_id is None:
            job_id = s.execute(select(Job.id).where(Job.status == "queued").order_by(Job.created_at)
                               .limit(1)).scalar_one_or_none()
            if job_id is None:
                return None
        res = s.execute(update(Job).where(Job.id == job_id, Job.status == "queued")
                        .values(status="running", started_at=utcnow(), claimed_by=WORKER_ID))
        return job_id if res.rowcount == 1 else None


def execute(ctx: Context, job_id: str) -> None:
    """Run a claimed job's handler and record the outcome. Never raises."""
    with ctx.db.session() as s:
        j = s.get(Job, job_id)
        jtype = j.type
    append_log(ctx, job_id, f"running on {WORKER_ID}")
    fn = HANDLERS.get(jtype)
    status, result, error = "failed", None, None
    try:
        if fn is None:
            raise JobFailed("unknown_job_type", f"no handler for job type '{jtype}'")
        result = fn(ctx, job_id)
        status = "succeeded"
    except JobCancelled:
        status, error = "cancelled", {"code": "cancelled", "message": "cancelled by user"}
    except JobDeferred as e:
        append_log(ctx, job_id, e.message)
        return
    except JobFailed as e:
        error = {"code": e.code, "message": e.message, "details": e.details}
    except ApiError as e:
        error = {"code": e.code, "message": e.message, "details": e.details}
    except Exception as e:  # unexpected: keep a traceback in the job log, not in the API error
        log.exception("job %s crashed", job_id)
        append_log(ctx, job_id, "unexpected error:\n" + traceback.format_exc()[-3000:])
        error = {"code": "internal_error", "message": f"{e.__class__.__name__}: {e}"}
    with ctx.db.session() as s:
        j = s.get(Job, job_id)
        j.status, j.result, j.error, j.finished_at = status, result, error, utcnow()
        j.logs = (j.logs or "") + f"{utcnow().isoformat()} {status}" + (f": {error['message']}" if error else "") + "\n"


def finish(ctx: Context, job_id: str, status: str, result=None, error=None) -> None:
    """Record the outcome of a deferred (externally executed) job."""
    with ctx.db.session() as s:
        j = s.get(Job, job_id)
        j.status, j.result, j.error, j.finished_at = status, result, error, utcnow()
        j.logs = (j.logs or "") + f"{utcnow().isoformat()} {status}" + (f": {error['message']}" if error else "") + "\n"


def run_inline(ctx: Context, job_id: str) -> None:
    if claim(ctx, job_id):
        execute(ctx, job_id)


def dispatch(ctx: Context, job_id: str, background) -> None:
    """Inline mode: schedule in-process after the response. Worker mode: leave it queued for app.workers.run."""
    if ctx.settings.job_mode == "inline" and background is not None:
        background.add_task(run_inline, ctx, job_id)


def get(s, job_id: str) -> Job:
    j = s.get(Job, job_id)
    if j is None:
        raise not_found("job", job_id)
    return j


def cancel(ctx: Context, job_id: str) -> Job:
    with ctx.db.session() as s:
        j = get(s, job_id)
        if j.status in TERMINAL:
            raise conflict("job_not_cancellable", f"job is already {j.status}")
        if j.status == "queued":
            j.status, j.finished_at = "cancelled", utcnow()
            j.error = {"code": "cancelled", "message": "cancelled before it started"}
            j.logs += f"{utcnow().isoformat()} cancelled before start\n"
            on_cancel = CANCEL_HOOKS.get(j.type)
            if on_cancel:
                on_cancel(s, j)
        else:
            j.cancel_requested = True
            j.logs += f"{utcnow().isoformat()} cancel requested\n"
        return j


CANCEL_HOOKS: dict[str, Callable] = {}
RETRY_HOOKS: dict[str, Callable] = {}


def retry(ctx: Context, job_id: str, background) -> Job:
    with ctx.db.session() as s:
        old = get(s, job_id)
        if old.status not in {"failed", "cancelled"}:
            raise conflict("job_not_retryable", f"only failed or cancelled jobs can be retried (job is {old.status})")
        hook = RETRY_HOOKS.get(old.type)
        new = create(s, old.type, dict(old.params), plot_id=old.plot_id, resource_type=old.resource_type,
                     resource_id=old.resource_id, retry_of=old.id, attempt=old.attempt + 1)
        if hook:
            hook(s, old, new)
        new_id_ = new.id
    dispatch(ctx, new_id_, background)
    with ctx.db.session() as s:
        return get(s, new_id_)
