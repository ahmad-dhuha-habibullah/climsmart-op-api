import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, Config, MetFile, Page, Run, Summary, Template } from "../api";
import { TimeChart } from "../components/Chart";
import { Bytes, Empty, ErrorState, JobPanel, SourceTag, Status } from "../components/ui";

const TABS = ["Overview", "Weather", "MET", "APSIM", "History"] as const;
type Tab = (typeof TABS)[number];
const iso = (d: Date) => d.toISOString().slice(0, 10);
const addDays = (n: number) => { const d = new Date(); d.setUTCDate(d.getUTCDate() + n); return iso(d); };

export function PlotPanel({ plotId, onDeleted }: { plotId: string; onDeleted: () => void }) {
  const [tab, setTab] = useState<Tab>("Overview");
  const summary = useQuery({ queryKey: ["summary", plotId], queryFn: () => api.get<Summary>(`/plots/${plotId}/summary`) });
  if (summary.error) return <ErrorState error={summary.error} onRetry={() => summary.refetch()} />;
  if (!summary.data) return <div className="muted">Loading plot…</div>;
  const s = summary.data;
  return (
    <div>
      <h1 style={{ marginBottom: 2 }}>{s.plot.name}</h1>
      <div className="muted" style={{ marginBottom: 8 }}>{s.plot.area_ha.toFixed(2)} ha · {s.plot.crop.replace("_", " ")} <SourceTag kind="user" /></div>
      <div className="tabs" role="tablist">{TABS.map((t) => <button key={t} role="tab" aria-selected={t === tab} className={t === tab ? "on" : ""} onClick={() => setTab(t)}>{t}</button>)}</div>
      {tab === "Overview" && <Overview s={s} onDeleted={onDeleted} />}
      {tab === "Weather" && <Weather plotId={plotId} />}
      {tab === "MET" && <MetTab s={s} />}
      {tab === "APSIM" && <ApsimTab s={s} />}
      {tab === "History" && <History s={s} />}
    </div>
  );
}

function Overview({ s, onDeleted }: { s: Summary; onDeleted: () => void }) {
  const p = s.plot;
  const valid = s.met_files.filter((m) => m.status === "valid").length;
  const ok = s.runs.filter((r) => r.status === "succeeded").length;
  return (
    <div>
      <dl className="kv">
        <dt>Plot id</dt><dd className="mono">{p.id}</dd>
        <dt>Area</dt><dd>{p.area_ha.toFixed(3)} ha (geodesic, WGS84)</dd>
        <dt>Centroid</dt><dd>{p.centroid.lat.toFixed(5)}, {p.centroid.lon.toFixed(5)}</dd>
        <dt>Bounding box</dt><dd className="mono">{p.bbox.map((x) => x.toFixed(5)).join(", ")}</dd>
        <dt>Planting year</dt><dd>{p.planting_year ?? "—"}</dd>
        <dt>MET files</dt><dd>{s.met_files.length} ({valid} valid)</dd>
        <dt>APSIM configs</dt><dd>{s.configs.length}</dd>
        <dt>APSIM runs</dt><dd>{s.runs.length} ({ok} succeeded)</dd>
        <dt>Artifacts</dt><dd>{s.artifacts.length}</dd>
      </dl>
      <div className="row" style={{ marginTop: 12 }}>
        <a className="btn secondary small" href={`/api/v2/plots/${p.id}/geojson`} download={`${p.name}.geojson`}>Download GeoJSON</a>
        <button className="btn secondary small" onClick={async () => { if (confirm(`Delete plot "${p.name}" and all its MET files, configs and runs?`)) { await api.del(`/plots/${p.id}`); onDeleted(); } }}>Delete plot</button>
      </div>
    </div>
  );
}

interface WeatherResp { days: { date: string; source: string; kind: string; radn: number | null; maxt: number | null; mint: number | null; rain: number | null; et0: number | null }[]; provenance: any }

function Weather({ plotId }: { plotId: string }) {
  const [start, setStart] = useState(addDays(-60));
  const [end, setEnd] = useState(addDays(7));
  const [range, setRange] = useState({ start, end });
  const w = useQuery({ queryKey: ["weather", plotId, range], queryFn: () => api.get<WeatherResp>(`/plots/${plotId}/weather?start_date=${range.start}&end_date=${range.end}`) });
  const series = useMemo(() => {
    const d = w.data?.days ?? [];
    return [
      { name: "Rain (mm)", type: "bar" as const, axis: 1 as const, color: "#2f5870", data: d.map((x) => [x.date, x.rain] as [string, number | null]) },
      { name: "Max T (°C)", color: "#b8473a", data: d.map((x) => [x.date, x.maxt] as [string, number | null]) },
      { name: "Min T (°C)", color: "#d99a2b", data: d.map((x) => [x.date, x.mint] as [string, number | null]) },
    ];
  }, [w.data]);
  const kinds = (w.data?.days ?? []).reduce<Record<string, number>>((a, x) => ({ ...a, [x.kind]: (a[x.kind] ?? 0) + 1 }), {});
  const gaps = (w.data?.days ?? []).filter((x) => x.rain == null || x.maxt == null || x.mint == null || x.radn == null).length;
  return (
    <div>
      <div className="row">
        <label className="field">From<input type="date" value={start} onChange={(e) => setStart(e.target.value)} /></label>
        <label className="field">To<input type="date" value={end} onChange={(e) => setEnd(e.target.value)} /></label>
        <button className="btn small" onClick={() => setRange({ start, end })}>Load</button>
      </div>
      {w.error ? <ErrorState error={w.error} onRetry={() => w.refetch()} /> : !w.data ? <div className="muted">Loading weather…</div> : (
        <>
          <div style={{ margin: "8px 0" }}><SourceTag kind="om" /> <span className="muted">{w.data.provenance.note}</span></div>
          <TimeChart series={series} yNames={["°C", "mm"]} height={260} />
          <div className="muted">{w.data.days.length} days · {Object.entries(kinds).map(([k, n]) => `${n} ${k}`).join(" · ")} · grid {w.data.provenance.grid_lat}, {w.data.provenance.grid_lon}{gaps ? ` · ${gaps} day(s) with missing values (not filled)` : ""}</div>
          <div className="muted" style={{ fontSize: 11 }}>{w.data.provenance.attribution}</div>
        </>
      )}
    </div>
  );
}

function MetTab({ s }: { s: Summary }) {
  const qc = useQueryClient();
  const year = new Date().getUTCFullYear();
  const [f, setF] = useState({ start_date: `${year - 2}-01-01`, end_date: `${year - 1}-12-31`, strategy: "model_only", name: "" });
  const [err, setErr] = useState<unknown>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(s.met_files[0]?.id ?? null);
  const submit = async () => {
    setErr(null);
    try { const r = await api.post<{ job: { id: string } }>(`/plots/${s.plot.id}/met-files`, { ...f, name: f.name || undefined }); setJobId(r.job.id); }
    catch (e) { setErr(e); }
  };
  return (
    <div>
      <h3>Generate MET file</h3>
      <div className="row">
        <label className="field">Start<input type="date" value={f.start_date} onChange={(e) => setF({ ...f, start_date: e.target.value })} /></label>
        <label className="field">End<input type="date" value={f.end_date} onChange={(e) => setF({ ...f, end_date: e.target.value })} /></label>
        <label className="field">Strategy
          <select value={f.strategy} onChange={(e) => setF({ ...f, strategy: e.target.value })}>
            <option value="model_only">Model only (Open-Meteo)</option>
            <option value="sensor_assimilation" disabled>Sensor assimilation — not available yet</option>
          </select>
        </label>
        <label className="field">Name <small>optional</small><input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
        <button className="btn" onClick={submit}>Generate</button>
      </div>
      <div className="muted" style={{ marginTop: 4 }}>Gridded model weather at the plot centroid. Missing values are never filled: a gap makes the MET file invalid and it cannot be used for APSIM.</div>
      {err != null && <ErrorState error={err} />}
      {jobId && <JobPanel jobId={jobId} compact onDone={(j) => { qc.invalidateQueries({ queryKey: ["summary", s.plot.id] }); if (j.resource_id) setOpen(j.resource_id); }} />}
      <h3>MET files ({s.met_files.length})</h3>
      {!s.met_files.length && <Empty title="No MET files yet">Generate one above; APSIM configs must point to a valid MET file.</Empty>}
      <table><thead><tr><th>Name</th><th>Period</th><th>Status</th><th></th></tr></thead><tbody>
        {s.met_files.map((m) => (
          <tr key={m.id} className={m.id === open ? "sel" : ""} onClick={() => setOpen(m.id)} style={{ cursor: "pointer" }}>
            <td>{m.name}<br /><span className="mono muted">{m.id}</span></td><td>{m.start_date} → {m.end_date}</td><td><Status s={m.status} /></td>
            <td>{m.artifact && <a href={m.artifact.download_url} onClick={(e) => e.stopPropagation()}>.met</a>}</td>
          </tr>))}
      </tbody></table>
      {open && s.met_files.find((m) => m.id === open) && <MetReport m={s.met_files.find((m) => m.id === open)!} />}
    </div>
  );
}

function MetReport({ m }: { m: MetFile }) {
  const v = m.validation ?? {};
  return (
    <div className="card" style={{ marginTop: 8 }}>
      <h2>Validation report · <Status s={m.status} /></h2>
      <dl className="kv">
        <dt>Strategy</dt><dd>{m.strategy} <SourceTag kind="om" /></dd>
        <dt>Coverage</dt><dd>{v.complete_days ?? "?"} / {v.expected_days ?? "?"} days complete ({v.coverage_pct ?? "?"}%)</dd>
        <dt>Day kinds</dt><dd>{v.day_kinds ? Object.entries(v.day_kinds).map(([k, n]) => `${n} ${k}`).join(", ") : "—"}</dd>
        <dt>tav / amp</dt><dd>{m.tav?.toFixed(2) ?? "—"} °C / {m.amp?.toFixed(2) ?? "—"} °C</dd>
        <dt>Location</dt><dd>{m.latitude.toFixed(4)}, {m.longitude.toFixed(4)}</dd>
        {m.artifact && <><dt>Artifact</dt><dd><a href={m.artifact.download_url}>{m.artifact.filename}</a> · <Bytes n={m.artifact.size_bytes} /><br /><span className="mono">sha256 {m.artifact.sha256}</span></dd></>}
      </dl>
      {v.errors?.length > 0 && <div className="state"><b>Errors (file not usable)</b><ul>{v.errors.map((e: any, i: number) => <li key={i}>{e.message}{e.dates ? `: ${e.dates.slice(0, 8).join(", ")}${e.dates.length > 8 ? "…" : ""}` : ""}</li>)}</ul></div>}
      {v.warnings?.length > 0 && <div className="state warn"><b>Warnings</b><ul>{v.warnings.map((e: any, i: number) => <li key={i}>{e.message ?? String(e)}</li>)}</ul></div>}
      {v.stats && <table><thead><tr><th>Variable</th><th>min</th><th>mean</th><th>max</th></tr></thead><tbody>
        {Object.entries(v.stats).map(([k, st]: [string, any]) => <tr key={k}><td>{k}</td><td>{st?.min?.toFixed?.(1)}</td><td>{st?.mean?.toFixed?.(1)}</td><td>{st?.max?.toFixed?.(1)}</td></tr>)}
      </tbody></table>}
    </div>
  );
}

function ApsimTab({ s }: { s: Summary }) {
  const qc = useQueryClient();
  const templates = useQuery({ queryKey: ["templates"], queryFn: () => api.get<Page<Template>>("/apsim/templates") });
  const validMet = s.met_files.filter((m) => m.status === "valid");
  const [tplId, setTplId] = useState<string>("");
  const [metId, setMetId] = useState<string>(validMet[0]?.id ?? "");
  const [params, setParams] = useState<Record<string, string>>({});
  const [name, setName] = useState("baseline");
  const [err, setErr] = useState<unknown>(null);
  const [jobs, setJobs] = useState<Record<string, string>>({});
  const tpl = templates.data?.items.find((t) => t.id === tplId) ?? templates.data?.items[0];
  const met = validMet.find((m) => m.id === metId);
  const val = (k: string) => params[k] ?? (k === "clock_start" || k === "sowing_date" ? met?.start_date : k === "clock_end" ? met?.end_date : String(tpl?.parameters[k]?.default ?? "")) ?? "";
  const refresh = () => qc.invalidateQueries({ queryKey: ["summary", s.plot.id] });

  const createConfig = async () => {
    if (!tpl) return; setErr(null);
    const p: Record<string, any> = {};
    for (const [k, d] of Object.entries(tpl.parameters)) { const v = val(k); if (v !== "") p[k] = d.type === "number" ? Number(v) : v; }
    try { await api.post<Config>(`/plots/${s.plot.id}/apsim-configs`, { template_id: tpl.id, met_file_id: metId, name, params: p }); refresh(); }
    catch (e) { setErr(e); }
  };
  const run = async (c: Config) => {
    setErr(null);
    try { const r = await api.post<{ job: { id: string } }>(`/apsim/runs`, { config_id: c.id, label: `${c.name} v${c.version}` }); setJobs({ ...jobs, [c.id]: r.job.id }); refresh(); }
    catch (e) { setErr(e); }
  };
  return (
    <div>
      <h3>New APSIM configuration</h3>
      {templates.error && <ErrorState error={templates.error} />}
      {!validMet.length ? <Empty title="A valid MET file is required">Generate and validate a MET file in the MET tab first. Configs are pinned to one exact MET artifact (by sha256).</Empty> : tpl && (
        <div className="card">
          <div className="row">
            <label className="field">Template<select value={tpl.id} onChange={(e) => setTplId(e.target.value)}>{templates.data!.items.map((t) => <option key={t.id} value={t.id}>{t.name} v{t.version}</option>)}</select></label>
            <label className="field">MET file<select value={metId} onChange={(e) => { setMetId(e.target.value); setParams({}); }}>{validMet.map((m) => <option key={m.id} value={m.id}>{m.name} ({m.start_date} → {m.end_date})</option>)}</select></label>
            <label className="field">Config name<input value={name} onChange={(e) => setName(e.target.value)} /></label>
          </div>
          <div className="muted" style={{ margin: "6px 0" }}>{tpl.description} <span className="mono">sha256 {tpl.sha256.slice(0, 12)}…</span></div>
          <div className="row">
            {Object.entries(tpl.parameters).map(([k, d]) => (
              <label key={k} className="field" title={d.help}>{d.label}{d.unit ? <small>{d.unit}{d.min != null ? ` · ${d.min}–${d.max}` : ""}</small> : null}
                <input type={d.type === "date" ? "date" : "number"} step="any" min={d.min} max={d.max} value={val(k)} onChange={(e) => setParams({ ...params, [k]: e.target.value })} style={{ width: d.type === "date" ? 140 : 90 }} />
              </label>))}
          </div>
          <div className="row" style={{ marginTop: 8 }}><button className="btn" onClick={createConfig}>Validate &amp; save config</button><span className="muted">Only these whitelisted parameters can be changed; the server generates the APSIMX.</span></div>
        </div>
      )}
      {err != null && <ErrorState error={err} />}
      <h3>Configs ({s.configs.length})</h3>
      {!s.configs.length && <div className="muted">No configs yet.</div>}
      {s.configs.map((c) => (
        <div key={c.id} className="card" style={{ marginBottom: 6 }}>
          <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
            <div><b>{c.name}</b> v{c.version} <span className="mono muted">{c.id}</span><br />
              <span className="muted">{Object.entries(c.params).map(([k, v]) => `${k}=${v}`).join(" · ")}</span><br />
              <span className="muted">MET {c.met_file_id}</span>{c.apsimx_artifact && <> · <a href={c.apsimx_artifact.download_url}>APSIMX</a></>}</div>
            <button className="btn small" onClick={() => run(c)}>Run APSIM</button>
          </div>
          {jobs[c.id] && <JobPanel jobId={jobs[c.id]} compact onDone={refresh} />}
        </div>))}
      <h3>Runs ({s.runs.length})</h3>
      <RunsTable runs={s.runs} />
    </div>
  );
}

export function RunsTable({ runs }: { runs: Run[] }) {
  if (!runs.length) return <div className="muted">No runs yet.</div>;
  return (
    <table><thead><tr><th>Run</th><th>Status</th><th>Runtime</th><th>Finished</th></tr></thead><tbody>
      {runs.map((r) => <tr key={r.id}>
        <td><Link to={`/runs/${r.id}`}>{r.label ?? r.id}</Link><br /><span className="mono muted">{r.id}</span></td>
        <td><Status s={r.status} />{r.error && <div className="muted">{r.error.code}</div>}</td>
        <td>{r.runtime_seconds != null ? `${r.runtime_seconds.toFixed(1)} s` : "—"}</td>
        <td>{r.finished_at ? new Date(r.finished_at).toLocaleString() : "—"}</td></tr>)}
    </tbody></table>
  );
}

function History({ s }: { s: Summary }) {
  return (
    <table><thead><tr><th>Job</th><th>Status</th><th>Created</th><th>Duration</th></tr></thead><tbody>
      {s.history.map((j) => <tr key={j.id}><td>{j.type}<br /><span className="mono muted">{j.id}</span></td><td><Status s={j.status} />{j.error && <div className="muted">{j.error.code}</div>}</td>
        <td>{new Date(j.created_at).toLocaleString()}</td><td>{j.duration_seconds != null ? `${j.duration_seconds}s` : "—"}</td></tr>)}
      {!s.history.length && <tr><td colSpan={4} className="muted">No jobs yet.</td></tr>}
    </tbody></table>
  );
}
