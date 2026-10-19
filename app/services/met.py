"""MET generation: request (MetFile pending + job) and the job handler (fetch -> validate -> render -> artifact)."""
from __future__ import annotations

from datetime import date

from app import met as metlib
from app.adapters.weather import openmeteo
from app.core.errors import ApiError, invalid, not_found
from app.core.util import new_id, utcnow
from app.models import MetFile, Plot
from app.services import jobs
from app.services.context import Context
from app.storage import register_artifact

MAX_DAYS = 366 * 40


def request_met(ctx: Context, plot_id: str, body: dict, background) -> tuple[dict, str]:
    strategy = body.get("strategy", "model_only")
    if strategy in metlib.UNAVAILABLE_STRATEGIES:
        raise ApiError(422, "strategy_not_available", metlib.UNAVAILABLE_STRATEGIES[strategy],
                       {"available": list(metlib.STRATEGIES)})
    if strategy not in metlib.STRATEGIES:
        raise invalid("unknown_strategy", f"unknown strategy '{strategy}'", {"available": list(metlib.STRATEGIES)})
    start, end = body["start_date"], body["end_date"]
    if end < start:
        raise invalid("invalid_period", "end_date must not be before start_date")
    if (end - start).days + 1 > MAX_DAYS:
        raise invalid("invalid_period", f"period longer than {MAX_DAYS} days")
    with ctx.db.session() as s:
        p = s.get(Plot, plot_id)
        if p is None:
            raise not_found("plot", plot_id)
        m = MetFile(id=new_id("met"), plot_id=p.id, name=body.get("name"), start_date=start, end_date=end,
                    strategy=strategy, strategy_params={}, status="pending", latitude=p.centroid_lat,
                    longitude=p.centroid_lon)
        s.add(m)
        j = jobs.create(s, "met.generate", {"met_file_id": m.id, "plot_id": p.id, "start_date": start.isoformat(),
                                            "end_date": end.isoformat(), "strategy": strategy},
                        plot_id=p.id, resource_type="met_file", resource_id=m.id)
        m.job_id = j.id
        mid, jid = m.id, j.id
    jobs.dispatch(ctx, jid, background)
    return {"met_file_id": mid}, jid


@jobs.handler("met.generate")
def run_met_job(ctx: Context, job_id: str) -> dict:
    with ctx.db.session() as s:
        j = jobs.get(s, job_id)
        params = dict(j.params)
        m = s.get(MetFile, params["met_file_id"])
        p = s.get(Plot, params["plot_id"])
        lat, lon, start, end = m.latitude, m.longitude, m.start_date, m.end_date
        plot_name, plantation_id, geometry_area = p.name, p.plantation_id, p.area_ha
    log = lambda line: jobs.append_log(ctx, job_id, line)  # noqa: E731
    log(f"fetching Open-Meteo daily weather {start}..{end} at plot centroid {lat:.5f}, {lon:.5f}")
    try:
        wx = openmeteo.fetch_daily(lat, lon, start, end, api_key=ctx.settings.open_meteo_api_key,
                                   timeout=ctx.settings.weather_timeout_seconds)
    except ApiError as e:
        _finish(ctx, m.id, "failed", None, None, None, None, {"error": e.message})
        raise
    rows = wx["days"]
    log(f"received {len(rows)} day(s) from {len(wx['provenance']['requests'])} request(s)")
    if jobs.cancel_requested(ctx, job_id):
        _finish(ctx, m.id, "failed", None, None, None, None, {"error": "cancelled"})
        raise jobs.JobCancelled()
    report = metlib.validate(rows, start, end)
    tav, amp, w = metlib.tav_amp(rows)
    report["warnings"] += w
    sources = {"strategy": "model_only", "description": metlib.STRATEGIES["model_only"],
               "per_day_source_counts": _count(rows, "source"), "weather": wx["provenance"]}
    if report["status"] != "valid":
        log(f"validation FAILED: {[e['code'] for e in report['errors']]}; no MET artifact written")
        _finish(ctx, m.id, "invalid", None, tav, amp, report, sources)
        raise jobs.JobFailed("met_validation_failed", "MET validation failed; see met_file.validation",
                             {"met_file_id": m.id, "errors": report["errors"]})
    comments = [f"ClimSmart-OP MET {m.id} for plot '{plot_name}' ({params['plot_id']}), generated {utcnow().isoformat()}",
                "Strategy: model_only (Open-Meteo archive + forecast at plot centroid; no observations; no gap filling)",
                f"Grid cell: {wx['provenance'].get('grid_lat')}, {wx['provenance'].get('grid_lon')}",
                openmeteo.ATTRIBUTION]
    text = metlib.render(rows, lat, lon, tav, amp, comments)
    data = text.encode("utf-8")
    with ctx.db.session() as s:
        art = register_artifact(s, ctx.store, kind="met", filename=f"{m.id}.met", media_type="text/plain",
                                data=data, plot_id=params["plot_id"], job_id=job_id,
                                provenance={"met_file_id": m.id, "plot_id": params["plot_id"],
                                            "plantation_id": plantation_id, "plot_area_ha": geometry_area,
                                            "strategy": "model_only", "period": [start.isoformat(), end.isoformat()],
                                            "weather": wx["provenance"], "tav": tav, "amp": amp,
                                            "generator": "app.met.render v1"})
        aid, sha = art.id, art.sha256
    _finish(ctx, m.id, "valid", aid, tav, amp, report, sources)
    log(f"MET artifact {aid} written ({len(data)} bytes, sha256 {sha[:12]}…)")
    return {"met_file_id": m.id, "artifact_id": aid, "sha256": sha, "status": "valid"}


def _count(rows, key):
    out: dict = {}
    for r in rows:
        out[r.get(key)] = out.get(r.get(key), 0) + 1
    return out


def _finish(ctx, met_id, status, artifact_id, tav, amp, validation, sources):
    with ctx.db.session() as s:
        m = s.get(MetFile, met_id)
        m.status, m.artifact_id, m.tav, m.amp = status, artifact_id, tav, amp
        m.validation, m.sources = validation, sources


def _retry_hook(s, old, new):
    m = s.get(MetFile, old.params["met_file_id"])
    if m is not None:
        m.status, m.job_id, m.validation, m.artifact_id = "pending", new.id, None, None


def _cancel_hook(s, j):
    m = s.get(MetFile, j.params["met_file_id"])
    if m is not None and m.status == "pending":
        m.status = "failed"


jobs.RETRY_HOOKS["met.generate"] = _retry_hook
jobs.CANCEL_HOOKS["met.generate"] = _cancel_hook


def parse_dates(body) -> dict:
    return {**body, "start_date": date.fromisoformat(str(body["start_date"])),
            "end_date": date.fromisoformat(str(body["end_date"]))}
