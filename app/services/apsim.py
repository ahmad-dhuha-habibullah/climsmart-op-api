"""APSIM templates, configs (versioned, linked to an exact MET artifact), runs (jobs), outputs and comparison."""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from datetime import date
from pathlib import Path

from sqlalchemy import func, select

from app.adapters.apsim import outputs as outlib
from app.adapters.apsim import runner
from app.adapters.apsim import template as tpl
from app.core.errors import ApiError, conflict, invalid, not_found
from app.core.util import new_id, utcnow
from app.models import ApsimConfig, ApsimRun, ApsimTemplate, Artifact, MetFile, Plot, RunOutput
from app.services import jobs
from app.services.context import Context
from app.storage import register_artifact


def seed_templates(ctx: Context) -> int:
    """Register every <slug>_<version>.apsimx + .json in the template dir (idempotent, sha256-checked)."""
    n = 0
    for meta_path in sorted(Path(ctx.settings.template_dir).glob("*.json")):
        meta = json.loads(meta_path.read_text())
        apsimx = meta_path.with_suffix(".apsimx")
        text = apsimx.read_text(encoding="utf-8")
        sha = hashlib.sha256(apsimx.read_bytes()).hexdigest()
        info = tpl.inspect_template(text)
        with ctx.db.session() as s:
            existing = s.execute(select(ApsimTemplate).where(ApsimTemplate.slug == meta["slug"],
                                                             ApsimTemplate.version == meta["version"])).scalar_one_or_none()
            if existing is not None:
                if existing.sha256 != sha:
                    raise RuntimeError(f"template {meta['slug']} {meta['version']} changed on disk; bump its version")
                continue
            art = register_artifact(s, ctx.store, kind="template", filename=apsimx.name, media_type="application/json",
                                    src=apsimx, provenance={"source": meta.get("source"), "registered_from": apsimx.name})
            s.add(ApsimTemplate(id=new_id("tpl"), slug=meta["slug"], version=meta["version"], name=meta["name"],
                                crop=meta["crop"], description=meta.get("description"), sha256=sha, artifact_id=art.id,
                                apsimx_version=info["apsimx_version"],
                                parameters={"editable": info["parameters"], "soil": info["soil"]},
                                source=meta.get("source") or {}))
            n += 1
    return n


def create_config(ctx: Context, plot_id: str, body: dict) -> dict:
    with ctx.db.session() as s:
        p = s.get(Plot, plot_id)
        if p is None:
            raise not_found("plot", plot_id)
        t = s.get(ApsimTemplate, body["template_id"])
        if t is None:
            raise not_found("template", body["template_id"])
        m = s.get(MetFile, body["met_file_id"])
        if m is None:
            raise not_found("met_file", body["met_file_id"])
        if m.plot_id != p.id:
            raise invalid("met_plot_mismatch", "the MET file belongs to a different plot")
        if m.status != "valid" or not m.artifact_id:
            raise conflict("met_not_valid", f"MET file is '{m.status}'; only a validated MET artifact can be used",
                           {"met_file_id": m.id, "status": m.status})
        params, _ = tpl.validate_params(body.get("params") or {}, m.start_date, m.end_date)
        met_art = s.get(Artifact, m.artifact_id)
        tpl_art = s.get(Artifact, t.artifact_id)
        tpl_text = ctx.store.get_bytes(tpl_art.storage_key).decode("utf-8")
        if hashlib.sha256(tpl_text.encode("utf-8")).hexdigest() != t.sha256:
            raise ApiError(500, "template_integrity", "stored template does not match its registered sha256")
        apsimx, edits = tpl.generate(tpl_text, params)
        name = body.get("name") or f"{t.slug} config"
        version = (s.execute(select(func.max(ApsimConfig.version)).where(ApsimConfig.plot_id == p.id,
                                                                         ApsimConfig.name == name)).scalar() or 0) + 1
        cid = new_id("cfg")
        art = register_artifact(s, ctx.store, kind="apsimx", filename=f"{cid}.apsimx", media_type="application/json",
                                data=apsimx.encode("utf-8"), plot_id=p.id,
                                provenance={"config_id": cid, "config_name": name, "config_version": version,
                                            "template": {"id": t.id, "slug": t.slug, "version": t.version,
                                                         "sha256": t.sha256},
                                            "met": {"met_file_id": m.id, "artifact_id": met_art.id,
                                                    "sha256": met_art.sha256},
                                            "params": params, "edits": edits})
        c = ApsimConfig(id=cid, plot_id=p.id, template_id=t.id, met_file_id=m.id, name=name, version=version,
                        params=params, status="generated", validation={"status": "valid", "errors": []}, edits=edits,
                        apsimx_artifact_id=art.id)
        s.add(c)
        s.flush()
        from app.services import serialize as ser
        return ser.config(c, art)


def request_run(ctx: Context, config_id: str, label: str | None, background) -> tuple[dict, str]:
    with ctx.db.session() as s:
        c = s.get(ApsimConfig, config_id)
        if c is None:
            raise not_found("config", config_id)
        if c.status != "generated" or not c.apsimx_artifact_id:
            raise conflict("config_not_ready", "configuration has no generated APSIMX artifact")
        m = s.get(MetFile, c.met_file_id)
        if m is None or m.status != "valid" or not m.artifact_id:
            raise conflict("met_not_valid", "the configuration's MET artifact is no longer valid/available")
        if ctx.settings.apsim_runner == "actions":
            from app.adapters.apsim import actions
            actions.check_ready(ctx.settings)
        r = ApsimRun(id=new_id("run"), config_id=c.id, plot_id=c.plot_id, label=label, status="queued")
        s.add(r)
        j = jobs.create(s, "apsim.run", {"run_id": r.id, "config_id": c.id}, plot_id=c.plot_id,
                        resource_type="apsim_run", resource_id=r.id)
        r.job_id = j.id
        rid, jid = r.id, j.id
    jobs.dispatch(ctx, jid, background)
    return {"run_id": rid}, jid


def _prepare(ctx: Context, job_id: str) -> dict:
    """Load the run's inputs, verify artifact integrity, mark the run running and pin its provenance."""
    with ctx.db.session() as s:
        j = jobs.get(s, job_id)
        r = s.get(ApsimRun, j.params["run_id"])
        c = s.get(ApsimConfig, r.config_id)
        m = s.get(MetFile, c.met_file_id)
        cfg_art, met_art = s.get(Artifact, c.apsimx_artifact_id), s.get(Artifact, m.artifact_id)
        t = s.get(ApsimTemplate, c.template_id)
        apsimx = ctx.store.get_bytes(cfg_art.storage_key)
        met_bytes = ctx.store.get_bytes(met_art.storage_key)
        for art, data in ((cfg_art, apsimx), (met_art, met_bytes)):
            if hashlib.sha256(data).hexdigest() != art.sha256:
                raise jobs.JobFailed("artifact_integrity", f"artifact {art.id} does not match its sha256")
        provenance = {"plot_id": r.plot_id, "config": {"id": c.id, "name": c.name, "version": c.version,
                                                       "params": c.params, "apsimx_sha256": cfg_art.sha256},
                      "template": {"id": t.id, "slug": t.slug, "version": t.version, "sha256": t.sha256},
                      "met": {"met_file_id": m.id, "artifact_id": met_art.id, "sha256": met_art.sha256,
                              "strategy": m.strategy, "start_date": m.start_date.isoformat(),
                              "end_date": m.end_date.isoformat(),
                              "period": [m.start_date.isoformat(), m.end_date.isoformat()]}}
        r.status, r.provenance = "running", provenance
        return {"run_id": r.id, "plot_id": r.plot_id, "provenance": provenance, "apsimx": apsimx, "met": met_bytes}


def complete_run(ctx: Context, job_id: str, run_id: str, db_path: Path, log_text: str, seconds: float | None,
                 runtime: dict) -> dict:
    """Register a finished APSIM run's .db + log, parse its output catalog and mark run (not job) succeeded."""
    cat = outlib.catalog(db_path)
    warnings = [x for x in outlib.messages(db_path) if x.get("MessageType") in (0, 1)][:20]
    with ctx.db.session() as s:
        r = s.get(ApsimRun, run_id)
        provenance = {**(r.provenance or {}), "apsim": runtime}
        dba = register_artifact(s, ctx.store, kind="apsim_db", filename=f"{run_id}.db",
                                media_type="application/vnd.sqlite3", src=db_path, plot_id=r.plot_id,
                                job_id=job_id, provenance={"run_id": run_id, **provenance})
        la = register_artifact(s, ctx.store, kind="apsim_log", filename=f"{run_id}.log", media_type="text/plain",
                               data=log_text.encode(), plot_id=r.plot_id, job_id=job_id, provenance={"run_id": run_id})
        for tbl in cat:
            s.add(RunOutput(id=new_id("out"), run_id=run_id, table_name=tbl["table_name"],
                            variables=tbl["variables"], row_count=tbl["row_count"],
                            first_date=date.fromisoformat(tbl["first_date"]) if tbl["first_date"] else None,
                            last_date=date.fromisoformat(tbl["last_date"]) if tbl["last_date"] else None))
        r.status, r.runtime, r.runtime_seconds = "succeeded", runtime, seconds
        r.db_artifact_id, r.log_artifact_id, r.finished_at = dba.id, la.id, utcnow()
        r.provenance = {**provenance, "apsim_messages": warnings}
        dbid = dba.id
    jobs.append_log(ctx, job_id, f"registered {len(cat)} output table(s): "
                    + ", ".join(f"{t['table_name']}({t['row_count']})" for t in cat))
    return {"run_id": run_id, "db_artifact_id": dbid, "tables": [t["table_name"] for t in cat]}


def fail_run_with_log(ctx: Context, job_id: str, run_id: str, code: str, message: str, log_text: str,
                      runtime: dict | None = None) -> str:
    with ctx.db.session() as s:
        r = s.get(ApsimRun, run_id)
        la = register_artifact(s, ctx.store, kind="apsim_log", filename=f"{run_id}.log", media_type="text/plain",
                               data=log_text.encode(), plot_id=r.plot_id, job_id=job_id, provenance={"run_id": run_id})
        laid, prov = la.id, {**(r.provenance or {}), **({"apsim": runtime} if runtime else {})}
    jobs.append_log(ctx, job_id, "APSIM failed; log tail:\n" + log_text[-2000:])
    _fail(ctx, run_id, code, message, prov, log_artifact_id=laid)
    return laid


@jobs.handler("apsim.run")
def run_apsim_job(ctx: Context, job_id: str) -> dict:
    log = lambda line: jobs.append_log(ctx, job_id, line)  # noqa: E731
    p = _prepare(ctx, job_id)
    run_id, provenance = p["run_id"], p["provenance"]
    if ctx.settings.apsim_runner == "actions":
        from app.adapters.apsim import actions
        try:
            url = actions.dispatch(ctx.settings, run_id)
        except actions.DispatchFailed as e:
            _fail(ctx, run_id, "actions_dispatch_failed", str(e), provenance)
            raise jobs.JobFailed("actions_dispatch_failed", str(e))
        raise jobs.JobDeferred(f"dispatched to GitHub Actions ({url}); waiting for the workflow callback")
    workdir = Path(tempfile.mkdtemp(prefix=f"apsim-{run_id}-"))
    try:
        try:
            res = runner.run(p["apsimx"].decode("utf-8"), p["met"], workdir, apsim_command=ctx.settings.apsim_command,
                             docker_image=ctx.settings.apsim_docker_image, timeout=ctx.settings.apsim_timeout_seconds,
                             met_filename=tpl.MET_FILENAME, should_cancel=lambda: jobs.cancel_requested(ctx, job_id),
                             log_cb=log)
        except runner.RuntimeUnavailable as e:
            _fail(ctx, run_id, "apsim_runtime_unavailable", str(e), provenance)
            raise jobs.JobFailed("apsim_runtime_unavailable", str(e))
        except runner.Cancelled:
            _fail(ctx, run_id, "cancelled", "cancelled by user", provenance, status="cancelled")
            raise jobs.JobCancelled()
        except runner.RunFailed as e:
            laid = fail_run_with_log(ctx, job_id, run_id, "apsim_failed", str(e), e.log)
            raise jobs.JobFailed("apsim_failed", str(e), {"log_artifact_id": laid})
        log(f"APSIM finished in {res.seconds} s")
        return complete_run(ctx, job_id, run_id, res.db_path, res.log, res.seconds, res.runtime)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _fail(ctx, run_id, code, message, provenance, status="failed", log_artifact_id=None):
    with ctx.db.session() as s:
        r = s.get(ApsimRun, run_id)
        r.status, r.error, r.finished_at = status, {"code": code, "message": message}, utcnow()
        if provenance is not None:
            r.provenance = provenance
        if log_artifact_id:
            r.log_artifact_id = log_artifact_id


def _retry_hook(s, old, new):
    r = s.get(ApsimRun, old.params["run_id"])
    if r is not None:
        r.status, r.job_id, r.error, r.finished_at = "queued", new.id, None, None


def _cancel_hook(s, j):
    r = s.get(ApsimRun, j.params["run_id"])
    if r is not None:
        r.status, r.error = "cancelled", {"code": "cancelled", "message": "cancelled before it started"}


jobs.RETRY_HOOKS["apsim.run"] = _retry_hook
jobs.CANCEL_HOOKS["apsim.run"] = _cancel_hook


def _db_path(ctx: Context, s, run: ApsimRun) -> Path:
    if run.status != "succeeded" or not run.db_artifact_id:
        raise conflict("run_has_no_outputs", f"run is '{run.status}' and has no output database")
    return ctx.store.local_path(s.get(Artifact, run.db_artifact_id).storage_key)


def query_output(ctx: Context, run_id: str, table: str, variables: list[str] | None, start, end, limit, offset) -> dict:
    with ctx.db.session() as s:
        r = s.get(ApsimRun, run_id)
        if r is None:
            raise not_found("run", run_id)
        o = s.execute(select(RunOutput).where(RunOutput.run_id == run_id, RunOutput.table_name == table)).scalar_one_or_none()
        if o is None:
            raise not_found("output table", table)
        path = _db_path(ctx, s, r)
        units = {v["name"]: v.get("unit") for v in o.variables}
        prov = r.provenance
    try:
        cols, rows, total = outlib.query(path, table, variables, start, end, limit, offset)
    except ValueError as e:
        raise invalid("unknown_variables", "unknown output variable(s)", {"unknown": e.args[0],
                                                                         "available": sorted(units)})
    return {"run_id": run_id, "table": table, "columns": cols, "units": {c: units.get(c) for c in cols},
            "rows": rows, "total": total, "limit": limit, "offset": offset,
            "provenance": {k: prov.get(k) for k in ("plot_id", "config", "template", "met", "apsim")}}


def compare(ctx: Context, ids: list[str], table: str, variables: list[str], start, end) -> dict:
    if len(ids) < 2:
        raise invalid("compare_needs_two", "give at least two run ids")
    series, meta = {}, []
    for rid in ids:
        q = query_output(ctx, rid, table, variables, start, end, 100_000, 0)
        idx = {c: i for i, c in enumerate(q["columns"])}
        series[rid] = {row[idx[outlib.DATE_COL]]: row for row in q["rows"]}, idx
        meta.append({"run_id": rid, "provenance": q["provenance"]})
    dates = sorted(set.intersection(*[set(v[0]) for v in series.values()]))
    out_vars = {}
    for v in variables:
        per_run = {rid: [series[rid][0][dd][series[rid][1][v]] for dd in dates] for rid in ids}
        stats = {rid: _stats(vals) for rid, vals in per_run.items()}
        base = ids[0]
        diff = [None if a is None or b is None else b - a for a, b in zip(per_run[base], per_run[ids[1]], strict=True)]
        out_vars[v] = {"values": per_run, "stats": stats,
                       "difference": {"of": f"{ids[1]} - {base}", **_stats(diff)}}
    # what differs between the runs' inputs
    p0, p1 = meta[0]["provenance"], meta[1]["provenance"]
    differences = {
        "met_sha256": [m["provenance"]["met"]["sha256"] for m in meta],
        "met_strategy": [m["provenance"]["met"]["strategy"] for m in meta],
        "params": {k: [m["provenance"]["config"]["params"].get(k) for m in meta]
                   for k in sorted(set(p0["config"]["params"]) | set(p1["config"]["params"]))
                   if p0["config"]["params"].get(k) != p1["config"]["params"].get(k)},
        "same_met_artifact": p0["met"]["sha256"] == p1["met"]["sha256"],
    }
    return {"table": table, "dates": dates, "variables": out_vars, "runs": meta, "input_differences": differences}


def _stats(vals):
    v = [x for x in vals if isinstance(x, int | float)]
    if not v:
        return {"n": 0}
    return {"n": len(v), "mean": sum(v) / len(v), "min": min(v), "max": max(v), "last": v[-1]}
