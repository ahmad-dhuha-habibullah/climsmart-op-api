import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, Page, Plantation, Plot } from "../api";
import { MapView } from "../components/MapView";
import { Empty, ErrorState } from "../components/ui";
import { PlotPanel } from "./PlotPanel";

/** Spatial Workspace: plantations → plots on a map; draw or upload GeoJSON; the right panel is the plot's whole workflow. */
export function SpatialWorkspace() {
  const qc = useQueryClient();
  const [sp, setSp] = useSearchParams();
  const plantationId = sp.get("plantation") ?? undefined;
  const plotId = sp.get("plot") ?? undefined;
  const set = (k: string, v?: string) => { const n = new URLSearchParams(sp); v ? n.set(k, v) : n.delete(k); if (k === "plantation") n.delete("plot"); setSp(n); };

  const plantations = useQuery({ queryKey: ["plantations"], queryFn: () => api.get<Page<Plantation>>("/plantations?limit=200") });
  const plots = useQuery({
    queryKey: ["plots", plantationId], enabled: !!plantationId,
    queryFn: () => api.get<Page<Plot>>(`/plots?plantation_id=${plantationId}&limit=500`),
  });
  useEffect(() => { // pick the first plantation by default
    if (!plantationId && plantations.data?.items.length) set("plantation", plantations.data.items[0].id);
  }, [plantations.data, plantationId]);

  const [drawing, setDrawing] = useState(false);
  const [pendingGeom, setPendingGeom] = useState<GeoJSON.Polygon | null>(null);
  const [newName, setNewName] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const [note, setNote] = useState<string | null>(null);
  const [newPlantation, setNewPlantation] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const refresh = () => { qc.invalidateQueries({ queryKey: ["plots"] }); qc.invalidateQueries({ queryKey: ["plantations"] }); };

  const createPlantation = async () => {
    setErr(null);
    try { const p = await api.post<Plantation>("/plantations", { name: newPlantation.trim() }); setNewPlantation(""); refresh(); set("plantation", p.id); }
    catch (e) { setErr(e); }
  };
  const savePlot = async () => {
    if (!pendingGeom || !plantationId) return;
    setErr(null);
    try { const p = await api.post<Plot>(`/plantations/${plantationId}/plots`, { name: newName.trim(), geometry: pendingGeom }); setPendingGeom(null); setNewName(""); refresh(); set("plot", p.id); }
    catch (e) { setErr(e); }
  };
  const upload = async (f: File) => {
    setErr(null); setNote(null);
    try { const r = await api.upload<{ created: Plot[] }>(`/plantations/${plantationId}/plots/upload`, f); setNote(`Imported ${r.created?.length ?? 0} plot(s) from ${f.name}.`); refresh(); }
    catch (e) { setErr(e); }
    if (fileRef.current) fileRef.current.value = "";
  };

  const list = plots.data?.items ?? [];
  return (
    <div className="ws">
      <aside>
        <h1>Spatial workspace</h1>
        <h3>Plantation</h3>
        {plantations.error ? <ErrorState error={plantations.error} onRetry={() => plantations.refetch()} /> : (
          <select value={plantationId ?? ""} onChange={(e) => set("plantation", e.target.value || undefined)} style={{ width: "100%" }} aria-label="Plantation">
            {!plantations.data?.items.length && <option value="">No plantations yet</option>}
            {plantations.data?.items.map((p) => <option key={p.id} value={p.id}>{p.name}{p.plot_count != null ? ` (${p.plot_count})` : ""}</option>)}
          </select>
        )}
        <div className="row" style={{ marginTop: 6 }}>
          <input placeholder="New plantation name" value={newPlantation} onChange={(e) => setNewPlantation(e.target.value)} style={{ flex: 1 }} aria-label="New plantation name" />
          <button className="btn small" disabled={!newPlantation.trim()} onClick={createPlantation}>Add</button>
        </div>

        <h3>Plots {plots.data ? `(${plots.data.total})` : ""}</h3>
        {plantationId && (
          <div className="row" style={{ marginBottom: 8 }}>
            <button className="btn small" onClick={() => setDrawing(true)} disabled={drawing}>Draw</button>
            <button className="btn secondary small" onClick={() => fileRef.current?.click()}>Upload GeoJSON</button>
            <input ref={fileRef} type="file" accept=".geojson,.json,application/geo+json" hidden onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
            <a className="btn secondary small" href={`/api/v2/plantations/${plantationId}/plots.geojson`} download>Export</a>
          </div>
        )}
        {err != null && <ErrorState error={err} />}
        {note && <div className="state info">{note}</div>}
        {pendingGeom && (
          <div className="card" style={{ marginBottom: 8 }}>
            <b>New plot</b>
            <div className="muted">Polygon drawn with {pendingGeom.coordinates[0].length - 1} vertices. The server validates it and computes the area.</div>
            <div className="row" style={{ marginTop: 6 }}>
              <input autoFocus placeholder="Plot name, e.g. Blok A1" value={newName} onChange={(e) => setNewName(e.target.value)} style={{ flex: 1 }} aria-label="Plot name" />
              <button className="btn small" disabled={!newName.trim()} onClick={savePlot}>Save</button>
              <button className="btn secondary small" onClick={() => setPendingGeom(null)}>Discard</button>
            </div>
          </div>
        )}
        {plots.error ? <ErrorState error={plots.error} onRetry={() => plots.refetch()} /> : plantationId && plots.data && !list.length ? (
          <Empty title="No plots yet">Draw a polygon on the map or upload a GeoJSON FeatureCollection (WGS84 lon/lat).</Empty>
        ) : (
          <div className="list">
            {list.map((p) => (
              <button key={p.id} className={`item ${p.id === plotId ? "on" : ""}`} onClick={() => set("plot", p.id)}>
                <b>{p.name}</b><br /><span className="muted">{p.area_ha.toFixed(2)} ha · {p.centroid.lat.toFixed(4)}, {p.centroid.lon.toFixed(4)}</span>
              </button>
            ))}
          </div>
        )}
        {!plantations.isLoading && !plantations.data?.items.length && <Empty title="Start here">Create a plantation (kebun), then draw or upload its plots.</Empty>}
      </aside>
      <MapView plots={list} selected={plotId} onSelect={(id) => set("plot", id)} drawing={drawing} onDrawingChange={setDrawing}
        onDrawn={(g) => { setPendingGeom(g); setNewName(""); }} />
      <section>{plotId ? <PlotPanel plotId={plotId} onDeleted={() => { set("plot"); refresh(); }} /> : <Empty title="No plot selected">Select a plot on the map or in the list to see its weather, MET files, APSIM configs and runs.</Empty>}</section>
    </div>
  );
}
