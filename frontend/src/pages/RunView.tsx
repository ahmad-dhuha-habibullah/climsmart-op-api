import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, Run } from "../api";
import { TimeChart } from "../components/Chart";
import { ErrorState, JobPanel, SourceTag, Status } from "../components/ui";

interface Q { columns: string[]; units: Record<string, string | null>; rows: any[][]; total: number; provenance: any }
const TABLE = "ClimSmartDaily";

/** One APSIM run: status/logs, provenance, outputs; chart overlays APSIM state with the MET weather the run used. */
export function RunView() {
  const { runId } = useParams();
  const run = useQuery({ queryKey: ["run", runId], queryFn: () => api.get<Run>(`/apsim/runs/${runId}`),
    refetchInterval: (q) => (q.state.data && ["queued", "running"].includes(q.state.data.status) ? 2000 : false) });
  const done = run.data?.status === "succeeded";
  const table = run.data?.outputs?.find((o) => o.table_name === TABLE) ?? run.data?.outputs?.[0];
  const numeric = (table?.variables ?? []).filter((v) => v.name !== "Clock.Today" && !["CheckpointID", "SimulationID", "Zone"].includes(v.name) && /real|int|float|double|num/i.test(v.dtype || "real"));
  const [left, setLeft] = useState("OilPalm.LAI");
  const [right, setRight] = useState("Weather.Rain");
  const q = useQuery({ enabled: done && !!table, queryKey: ["out", runId, table?.table_name, left, right],
    queryFn: () => api.get<Q>(`/apsim/runs/${runId}/outputs/${table!.table_name}?vars=${encodeURIComponent([left, right].join(","))}&limit=100000`) });
  const series = useMemo(() => {
    if (!q.data) return [];
    const i = (c: string) => q.data!.columns.indexOf(c);
    const di = i("Clock.Today");
    const mk = (c: string) => q.data!.rows.map((r) => [String(r[di]).slice(0, 10), r[i(c)]] as [string, number | null]);
    return [
      { name: `${left} ${q.data.units[left] ? `(${q.data.units[left]})` : ""}`, data: mk(left), color: "#6b4e9b" },
      { name: `${right} ${q.data.units[right] ? `(${q.data.units[right]})` : ""}`, data: mk(right), axis: 1 as const, type: right.startsWith("Weather.Rain") ? "bar" as const : "line" as const, color: "#2f5870" },
    ];
  }, [q.data]);

  if (run.error) return <ErrorState error={run.error} onRetry={() => run.refetch()} />;
  if (!run.data) return <div className="muted">Loading run…</div>;
  const r = run.data; const p = r.provenance ?? {};
  return (
    <div style={{ maxWidth: 1100 }}>
      <div className="muted"><Link to={`/workspace?plot=${r.plot_id}`}>← Plot</Link> · <Link to="/apsim">All runs</Link></div>
      <h1>{r.label ?? r.id} <Status s={r.status} /></h1>
      <JobPanel jobId={r.job_id} compact={done} onDone={() => run.refetch()} />
      {r.status === "failed" && r.error?.code === "apsim_runtime_unavailable" && (
        <div className="state warn"><b>APSIM did not run</b>No APSIM runtime is configured on this server (set APSIM_COMMAND or APSIM_DOCKER_IMAGE for the worker). No results were produced — nothing on this page is simulated output.</div>)}
      <div className="row" style={{ alignItems: "flex-start", marginTop: 12 }}>
        <div className="card" style={{ flex: 1, minWidth: 320 }}>
          <h2>Provenance</h2>
          <dl className="kv">
            <dt>Run id</dt><dd className="mono">{r.id}</dd>
            <dt>APSIM</dt><dd>{p.apsim?.version ?? r.runtime?.version ?? "—"} {r.runtime_seconds != null && `· ${r.runtime_seconds.toFixed(1)} s`}</dd>
            <dt>Template</dt><dd>{p.template ? `${p.template.slug} v${p.template.version}` : "—"} <span className="mono">{p.template?.sha256?.slice(0, 12)}</span></dd>
            <dt>Config</dt><dd>{p.config ? `${p.config.name} v${p.config.version}` : r.config_id}</dd>
            <dt>Parameters</dt><dd>{p.config ? Object.entries(p.config.params).map(([k, v]) => `${k}=${v}`).join(" · ") : "—"}</dd>
            <dt>MET</dt><dd>{p.met ? <>{p.met.strategy} · {p.met.start_date} → {p.met.end_date} <SourceTag kind="om" /><br /><span className="mono">sha256 {p.met.sha256}</span></> : "—"}</dd>
            {r.db_artifact_id && <><dt>Files</dt><dd><a href={`/api/v2/artifacts/${r.db_artifact_id}/download`}>simulation.db</a>{r.log_artifact_id && <> · <a href={`/api/v2/artifacts/${r.log_artifact_id}/download`}>APSIM log</a></>}</dd></>}
          </dl>
        </div>
        <div className="card" style={{ flex: 1, minWidth: 260 }}>
          <h2>Output tables</h2>
          {!r.outputs?.length ? <div className="muted">{done ? "No tables." : "Outputs appear when the run succeeds."}</div> : (
            <table><thead><tr><th>Table</th><th>Rows</th><th>Period</th></tr></thead><tbody>
              {r.outputs.map((o) => <tr key={o.id}><td>{o.table_name}</td><td>{o.row_count}</td><td>{o.first_date} → {o.last_date}</td></tr>)}
            </tbody></table>)}
        </div>
      </div>
      {done && table && (
        <div className="card" style={{ marginTop: 12 }}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <h2>{table.table_name} <SourceTag kind="apsim" /></h2>
            <div className="row">
              <label className="field">Left axis<select value={left} onChange={(e) => setLeft(e.target.value)}>{numeric.map((v) => <option key={v.name}>{v.name}</option>)}</select></label>
              <label className="field">Right axis<select value={right} onChange={(e) => setRight(e.target.value)}>{numeric.map((v) => <option key={v.name}>{v.name}</option>)}</select></label>
            </div>
          </div>
          {q.error ? <ErrorState error={q.error} /> : <TimeChart series={series} yNames={[left, right]} />}
          <div className="muted">Weather.* columns are the MET inputs APSIM read for this run; other columns are APSIM model state. Indicative, not validated against field data.</div>
        </div>)}
    </div>
  );
}
