"""Plot weather, MET files, APSIM templates/configs/runs/outputs, plot summary."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from app import met as metlib
from app.adapters.weather import openmeteo
from app.api.v2.deps import Page, ctx, paged
from app.core.errors import not_found
from app.models import ApsimConfig, ApsimRun, ApsimTemplate, Artifact, Job, MetFile, Plot, RunOutput
from app.schemas import ConfigIn, MetRequest, RunIn
from app.services import apsim as apsim_svc
from app.services import jobs
from app.services import met as met_svc
from app.services import serialize as ser
from app.services.context import Context

router = APIRouter()


def _accepted(c: Context, job_id: str, extra: dict) -> JSONResponse:
    with c.db.session() as s:
        j = ser.job(jobs.get(s, job_id))
    return JSONResponse({"job": j, **extra}, status_code=202, headers={"Location": f"/api/v2/jobs/{job_id}"})


# ---------------------------------------------------------------- weather
@router.get("/plots/{plot_id}/weather", tags=["weather"],
            summary="Daily Open-Meteo weather at the plot centroid (archive + forecast), with provenance")
def plot_weather(plot_id: str, start_date: date | None = None, end_date: date | None = None,
                 c: Context = Depends(ctx)):
    with c.db.session() as s:
        p = s.get(Plot, plot_id)
        if p is None:
            raise not_found("plot", plot_id)
        lat, lon = p.centroid_lat, p.centroid_lon
    today = datetime.now(timezone.utc).date()
    end = end_date or today + timedelta(days=7)
    start = start_date or end - timedelta(days=60)
    wx = openmeteo.fetch_daily(lat, lon, start, end, api_key=c.settings.open_meteo_api_key,
                               timeout=c.settings.weather_timeout_seconds)
    return {"plot_id": plot_id, "start_date": start.isoformat(), "end_date": end.isoformat(), **wx,
            "available_strategies": metlib.STRATEGIES, "unavailable_strategies": metlib.UNAVAILABLE_STRATEGIES}


# ---------------------------------------------------------------- MET
@router.post("/plots/{plot_id}/met-files", tags=["met"], status_code=202,
             summary="Generate and validate a MET file for a period (long-running: returns 202 + job)")
def create_met(plot_id: str, body: MetRequest, background: BackgroundTasks, c: Context = Depends(ctx)):
    extra, jid = met_svc.request_met(c, plot_id, body.model_dump(), background)
    return _accepted(c, jid, extra)


@router.get("/plots/{plot_id}/met-files", tags=["met"], summary="List a plot's MET files")
def list_met(plot_id: str, status: str | None = None, page: Page = Depends(), c: Context = Depends(ctx)):
    with c.db.session() as s:
        stmt = select(MetFile).where(MetFile.plot_id == plot_id)
        if status:
            stmt = stmt.where(MetFile.status == status)
        total = s.execute(select(func.count()).select_from(stmt.subquery())).scalar()
        rows = s.execute(stmt.order_by(MetFile.created_at.desc()).limit(page.limit).offset(page.offset)).scalars().all()
        return paged([ser.met_file(m, s.get(Artifact, m.artifact_id) if m.artifact_id else None) for m in rows],
                     total, page)


@router.get("/met-files/{met_file_id}", tags=["met"], summary="Get a MET file with its validation report")
def get_met(met_file_id: str, c: Context = Depends(ctx)):
    with c.db.session() as s:
        m = s.get(MetFile, met_file_id)
        if m is None:
            raise not_found("met_file", met_file_id)
        return ser.met_file(m, s.get(Artifact, m.artifact_id) if m.artifact_id else None)


# ---------------------------------------------------------------- APSIM
@router.get("/apsim/templates", tags=["apsim"], summary="Registered APSIMX templates (versioned, sha256)")
def list_templates(c: Context = Depends(ctx)):
    with c.db.session() as s:
        rows = s.execute(select(ApsimTemplate).order_by(ApsimTemplate.slug, ApsimTemplate.version)).scalars().all()
        return {"items": [ser.template(t) for t in rows], "total": len(rows)}


@router.get("/apsim/templates/{template_id}", tags=["apsim"], summary="Get a template and its editable parameters")
def get_template(template_id: str, c: Context = Depends(ctx)):
    with c.db.session() as s:
        t = s.get(ApsimTemplate, template_id)
        if t is None:
            raise not_found("template", template_id)
        return ser.template(t)


@router.post("/plots/{plot_id}/apsim-configs", tags=["apsim"], status_code=201,
             summary="Create a versioned APSIM config tied to the plot and an exact, valid MET artifact")
def create_config(plot_id: str, body: ConfigIn, c: Context = Depends(ctx)):
    return apsim_svc.create_config(c, plot_id, body.model_dump())


@router.get("/apsim/configs", tags=["apsim"], summary="List APSIM configs")
def list_configs(plot_id: str | None = None, page: Page = Depends(), c: Context = Depends(ctx)):
    with c.db.session() as s:
        stmt = select(ApsimConfig)
        if plot_id:
            stmt = stmt.where(ApsimConfig.plot_id == plot_id)
        total = s.execute(select(func.count()).select_from(stmt.subquery())).scalar()
        rows = s.execute(stmt.order_by(ApsimConfig.created_at.desc()).limit(page.limit).offset(page.offset)).scalars()
        return paged([ser.config(x, s.get(Artifact, x.apsimx_artifact_id)) for x in rows], total, page)


@router.get("/apsim/configs/{config_id}", tags=["apsim"], summary="Get an APSIM config")
def get_config(config_id: str, c: Context = Depends(ctx)):
    with c.db.session() as s:
        x = s.get(ApsimConfig, config_id)
        if x is None:
            raise not_found("config", config_id)
        return ser.config(x, s.get(Artifact, x.apsimx_artifact_id))


@router.post("/apsim/runs", tags=["apsim"], status_code=202, summary="Run a config with APSIM (returns 202 + job)")
def create_run(body: RunIn, background: BackgroundTasks, c: Context = Depends(ctx)):
    extra, jid = apsim_svc.request_run(c, body.config_id, body.label, background)
    return _accepted(c, jid, extra)


@router.get("/apsim/runs", tags=["apsim"], summary="List runs")
def list_runs(plot_id: str | None = None, config_id: str | None = None, status: str | None = None,
              page: Page = Depends(), c: Context = Depends(ctx)):
    with c.db.session() as s:
        stmt = select(ApsimRun)
        for col, v in ((ApsimRun.plot_id, plot_id), (ApsimRun.config_id, config_id), (ApsimRun.status, status)):
            if v:
                stmt = stmt.where(col == v)
        total = s.execute(select(func.count()).select_from(stmt.subquery())).scalar()
        rows = s.execute(stmt.order_by(ApsimRun.created_at.desc()).limit(page.limit).offset(page.offset)).scalars()
        return paged([ser.run(r) for r in rows], total, page)


@router.get("/apsim/runs/compare", tags=["apsim"], summary="Compare runs on shared dates (values, stats, input diffs)")
def compare_runs(ids: str = Query(..., description="comma-separated run ids (first = baseline)"),
                 table: str = "ClimSmartDaily", vars: str = Query("OilPalm.LAI"),
                 start_date: date | None = None, end_date: date | None = None, c: Context = Depends(ctx)):
    return apsim_svc.compare(c, [i for i in ids.split(",") if i], table, [v for v in vars.split(",") if v],
                             start_date.isoformat() if start_date else None, end_date.isoformat() if end_date else None)


@router.get("/apsim/runs/{run_id}", tags=["apsim"], summary="Get a run with its output catalog")
def get_run(run_id: str, c: Context = Depends(ctx)):
    with c.db.session() as s:
        r = s.get(ApsimRun, run_id)
        if r is None:
            raise not_found("run", run_id)
        outs = s.execute(select(RunOutput).where(RunOutput.run_id == run_id)).scalars().all()
        return ser.run(r, list(outs))


@router.get("/apsim/runs/{run_id}/outputs/{table}", tags=["apsim"],
            summary="Query an output table (vars, date range, pagination) with provenance")
def query_outputs(run_id: str, table: str, vars: str | None = None, start_date: date | None = None,
                  end_date: date | None = None, limit: int = Query(5000, ge=1, le=100_000),
                  offset: int = Query(0, ge=0), c: Context = Depends(ctx)):
    return apsim_svc.query_output(c, run_id, table, [v for v in vars.split(",")] if vars else None,
                                  start_date.isoformat() if start_date else None,
                                  end_date.isoformat() if end_date else None, limit, offset)


# ---------------------------------------------------------------- plot summary
@router.get("/plots/{plot_id}/summary", tags=["plantations & plots"],
            summary="Everything linked to a plot: MET files, configs, runs + outputs, artifacts, job history")
def plot_summary(plot_id: str, c: Context = Depends(ctx)):
    with c.db.session() as s:
        p = s.get(Plot, plot_id)
        if p is None:
            raise not_found("plot", plot_id)
        mets = s.execute(select(MetFile).where(MetFile.plot_id == plot_id).order_by(MetFile.created_at.desc())).scalars()
        cfgs = s.execute(select(ApsimConfig).where(ApsimConfig.plot_id == plot_id)
                         .order_by(ApsimConfig.created_at.desc())).scalars().all()
        runs = s.execute(select(ApsimRun).where(ApsimRun.plot_id == plot_id).order_by(ApsimRun.created_at.desc())).scalars()
        arts = s.execute(select(Artifact).where(Artifact.plot_id == plot_id).order_by(Artifact.created_at.desc())).scalars()
        hist = s.execute(select(Job).where(Job.plot_id == plot_id).order_by(Job.created_at.desc()).limit(50)).scalars()
        run_list = []
        for r in runs:
            outs = s.execute(select(RunOutput).where(RunOutput.run_id == r.id)).scalars().all()
            run_list.append(ser.run(r, list(outs)))
        return {"plot": ser.plot(p),
                "met_files": [ser.met_file(m, s.get(Artifact, m.artifact_id) if m.artifact_id else None) for m in mets],
                "configs": [ser.config(x, s.get(Artifact, x.apsimx_artifact_id)) for x in cfgs],
                "runs": run_list, "artifacts": [ser.artifact(a) for a in arts],
                "history": [ser.job(j) for j in hist]}
