"""v2 sub-application, mounted at /api/v2 (own OpenAPI at /api/v2/openapi.json, docs at /api/v2/docs).

Mounting keeps the v1 app's /openapi.json, error shapes and middleware behaviour exactly as before.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v2.routers import plantations, system, workflow
from app.core.config import V2Settings
from app.core.db import Database, run_migrations
from app.core.errors import install_handlers
from app.services import apsim as apsim_svc
from app.services import met as _met  # noqa: F401  (registers the met.generate job handler)
from app.services.context import Context
from app.storage import LocalArtifactStore

log = logging.getLogger("climsmart.v2")

DESCRIPTION = """ClimSmart-OP platform API (v2): plantations and plot polygons, plot weather, MET generation with
validation, versioned APSIM configurations linked to exact MET artifacts, APSIM runs as jobs, queryable outputs with
provenance. Long-running operations return **202 + a job**; poll `/api/v2/jobs/{id}`.

Errors use one envelope: `{"error": {"code", "message", "details"}}`. Lists are paginated with `limit`/`offset` and
return `{"items", "total", "limit", "offset"}`. **Indicative, not validated**: model output is not calibrated for
any estate. Authentication is NOT implemented yet (do not expose a writable v2 publicly)."""


def build_context(settings: V2Settings | None = None) -> Context | None:
    settings = settings or V2Settings.from_env()
    if not settings.storage_configured:
        log.warning("v2 disabled: no DATABASE_URL on this deployment")
        return None
    if settings.auto_migrate:
        run_migrations(settings.database_url)
    ctx = Context(settings=settings, db=Database(settings.database_url),
                  store=LocalArtifactStore(settings.artifact_dir))
    apsim_svc.seed_templates(ctx)
    return ctx


def create_v2_app(ctx: Context | None = None, settings: V2Settings | None = None) -> FastAPI:
    v2 = FastAPI(title="ClimSmart-OP API v2", version="2.0.0-alpha", description=DESCRIPTION,
                 docs_url="/docs", redoc_url=None, openapi_url="/openapi.json")
    v2.state.ctx = ctx if ctx is not None else build_context(settings)
    install_handlers(v2)
    v2.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST", "PATCH", "DELETE"],
                      allow_headers=["Content-Type"])
    v2.include_router(plantations.router)
    v2.include_router(workflow.router)
    v2.include_router(system.router)
    return v2
