import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import logo from "./assets/logo.png";
import { useState } from "react";
import { api, getToken, setToken } from "./api";
import { SpatialWorkspace } from "./pages/SpatialWorkspace";
import { RunView } from "./pages/RunView";
import { ApsimRuns } from "./pages/ApsimRuns";
import { NotBuilt } from "./pages/NotBuilt";

const NAV: { to: string; label: string; pending?: string }[] = [
  { to: "/workspace", label: "Spatial workspace" },
  { to: "/apsim", label: "APSIM runs" },
  { to: "/sensors", label: "Sensors", pending: "Sensor registry and observation ingestion are not built yet. MET generation currently supports the model-only strategy." },
  { to: "/dashboards", label: "Dashboards", pending: "The dashboard builder is not built yet. Use the plot panel and run pages." },
];

function Health() {
  const h = useQuery({ queryKey: ["health"], queryFn: () => api.get<any>("/system/health"), refetchInterval: 30000 });
  if (h.error) return <div className="banner">API unavailable: {(h.error as Error).message}</div>;
  if (h.data && !h.data.apsim?.available) return <div className="banner">APSIM runtime not configured on this server — APSIM runs will fail honestly with “runtime unavailable”.</div>;
  return null;
}

function TokenBox() {
  const [t, setT] = useState(getToken() ?? "");
  const [saved, setSaved] = useState(!!getToken());
  return (
    <div style={{ padding: "8px 16px", fontSize: 11 }}>
      <label htmlFor="tok" style={{ color: "#cfe0d6" }}>Write token {saved ? "(saved)" : "(read-only without it)"}</label>
      <input id="tok" type="password" value={t} onChange={(e) => { setT(e.target.value); setSaved(false); }} style={{ width: "100%" }} />
      <button className="btn small" style={{ marginTop: 4 }} onClick={() => { setToken(t.trim()); setSaved(!!t.trim()); }}>Save</button>
    </div>
  );
}

export function App() {
  return (
    <div className="shell">
      <nav className="sidebar" aria-label="Main">
        <div className="logo"><img src={logo} alt="ClimSmart-OP" /></div>
        <div className="nav">{NAV.map((n) => <NavLink key={n.to} to={n.to} className={({ isActive }) => `${isActive ? "active" : ""} ${n.pending ? "pending" : ""}`}>{n.label}</NavLink>)}</div>
        <a href="/" style={{ color: "#cfe0d6", padding: "9px 16px", fontSize: 12 }}>← v1 forecast dashboard</a>
        <TokenBox />
        <div className="foot">ClimSmart-OP v2 (alpha)<br /><a href="/api/v2/docs" style={{ color: "#cfe0d6" }}>API docs</a></div>
      </nav>
      <div className="main">
        <div className="trust"><b>INDICATIVE, NOT VALIDATED.</b><span>Weather is gridded model data (Open-Meteo), not on-site measurement. APSIM outputs are model estimates.</span></div>
        <Health />
        <main className="content">
          <Routes>
            <Route path="/" element={<Navigate to="/workspace" replace />} />
            <Route path="/workspace" element={<SpatialWorkspace />} />
            <Route path="/apsim" element={<ApsimRuns />} />
            <Route path="/runs/:runId" element={<RunView />} />
            {NAV.filter((n) => n.pending).map((n) => <Route key={n.to} path={n.to} element={<NotBuilt title={n.label} note={n.pending!} />} />)}
            <Route path="*" element={<NotBuilt title="Page not found" note="This address does not match any ClimSmart-OP page." />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}
