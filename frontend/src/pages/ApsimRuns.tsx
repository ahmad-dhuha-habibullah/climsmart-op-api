import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, Page, Run } from "../api";
import { TimeChart } from "../components/Chart";
import { Empty, ErrorState, SourceTag } from "../components/ui";
import { RunsTable } from "./PlotPanel";

const VARS = ["OilPalm.LAI", "SoilWaterMM", "OilPalm.EP", "OilPalm.FW"];
const COLORS = ["#6b4e9b", "#267653", "#d99a2b", "#2f5870"];

/** APSIM runs across plots, plus side-by-side comparison of two or more succeeded runs. */
export function ApsimRuns() {
  const runs = useQuery({ queryKey: ["runs"], queryFn: () => api.get<Page<Run>>("/apsim/runs?limit=200"), refetchInterval: 5000 });
  const [sel, setSel] = useState<string[]>([]);
  const [v, setV] = useState(VARS[0]);
  const ok = (runs.data?.items ?? []).filter((r) => r.status === "succeeded");
  const cmp = useQuery({ enabled: sel.length >= 2, queryKey: ["cmp", sel, v], queryFn: () => api.get<any>(`/apsim/runs/compare?ids=${sel.join(",")}&vars=${encodeURIComponent(v)}`) });
  const series = useMemo(() => {
    if (!cmp.data) return [];
    const vals = cmp.data.variables[v].values as Record<string, (number | null)[]>;
    return sel.map((id, i) => ({ name: runs.data?.items.find((r) => r.id === id)?.label ?? id, color: COLORS[i % 4], dashed: i > 0,
      data: cmp.data.dates.map((d: string, k: number) => [String(d).slice(0, 10), vals[id][k]] as [string, number | null]) }));
  }, [cmp.data]);
  return (
    <div style={{ maxWidth: 1100 }}>
      <h1>APSIM runs</h1>
      {runs.error ? <ErrorState error={runs.error} onRetry={() => runs.refetch()} /> : <RunsTable runs={runs.data?.items ?? []} />}
      <h3>Compare runs</h3>
      {ok.length < 2 ? <Empty title="Need two succeeded runs">Create a second config for a plot (e.g. a different palm density) and run it to compare.</Empty> : (
        <div className="card">
          <div className="row">
            {ok.map((r) => <label key={r.id} className="row" style={{ alignItems: "center", gap: 4 }}>
              <input type="checkbox" checked={sel.includes(r.id)} onChange={(e) => setSel(e.target.checked ? [...sel, r.id] : sel.filter((x) => x !== r.id))} />{r.label ?? r.id}</label>)}
            <label className="field">Variable<select value={v} onChange={(e) => setV(e.target.value)}>{VARS.map((x) => <option key={x}>{x}</option>)}</select></label>
          </div>
          {sel.length < 2 ? <div className="muted" style={{ marginTop: 6 }}>Tick at least two runs; the first ticked is the baseline.</div> : cmp.error ? <ErrorState error={cmp.error} /> : cmp.data && (
            <>
              <div style={{ marginTop: 8 }}><SourceTag kind="apsim" /> {cmp.data.dates.length} shared days</div>
              <TimeChart series={series} yNames={[v]} />
              <dl className="kv">
                <dt>Same MET artifact</dt><dd>{cmp.data.input_differences.same_met_artifact ? "yes" : "no"}</dd>
                <dt>Parameter differences</dt><dd>{Object.entries(cmp.data.input_differences.params).map(([k, x]) => `${k}: ${(x as any[]).join(" → ")}`).join(" · ") || "none"}</dd>
                <dt>Mean difference</dt><dd>{cmp.data.variables[v].difference.mean?.toFixed?.(3) ?? "—"} ({cmp.data.variables[v].difference.of})</dd>
              </dl>
            </>)}
        </div>)}
    </div>
  );
}
