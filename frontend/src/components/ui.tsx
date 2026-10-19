import { useEffect, useState } from "react";
import { ApiError, BASE, Job, api } from "../api";

export function Status({ s }: { s: string }) { return <span className={`status ${s}`}>{s}</span>; }

export function SourceTag({ kind }: { kind: "apsim" | "om" | "user" | "synthetic" | "example"; }) {
  const label = { apsim: "APSIM-X", om: "Open-Meteo", user: "User input", synthetic: "Synthetic", example: "Example value" }[kind];
  return <span className={`tag ${kind}`}>{label}</span>;
}

/** Error state modelled on the Figma "Source error 502" / "503 retry" / "APSIM 404" frames: say what failed, why, and what to do. */
export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const e = error instanceof ApiError ? error : new ApiError(0, "unknown", String(error));
  const title = e.status === 404 ? "Not found" : e.status === 503 ? "Service unavailable" : e.status === 502 ? "Data source error"
    : e.status === 401 ? "Write token required" : e.status === 0 ? "Connection problem" : e.status === 409 ? "Not allowed in the current state" : e.status === 422 ? "Check the input" : "Something went wrong";
  return (
    <div className="state" role="alert">
      <b>{title} <span className="mono muted">{e.status ? `HTTP ${e.status} · ` : ""}{e.code}</span></b>
      <div>{e.message}</div>
      {e.details != null && <Details d={e.details} />}
      {onRetry && <button className="btn secondary small" style={{ marginTop: 6 }} onClick={onRetry}>Retry</button>}
    </div>
  );
}

function Details({ d }: { d: any }) {
  if (d?.errors && Array.isArray(d.errors)) return <ul>{d.errors.map((x: any, i: number) => <li key={i}>{x.param ? <b>{x.param}: </b> : null}{x.message ?? x.msg ?? JSON.stringify(x)}</li>)}</ul>;
  if (Array.isArray(d)) return <ul>{d.map((x: any, i: number) => <li key={i}>{x.loc ? <b>{x.loc.join(".")}: </b> : null}{x.msg ?? JSON.stringify(x)}</li>)}</ul>;
  return <pre className="mono" style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(d, null, 1)}</pre>;
}

export function Empty({ title, children }: { title: string; children?: React.ReactNode }) {
  return <div className="state info"><b>{title}</b>{children}</div>;
}

/** Polls a job until it is terminal. Shows status, attempt, timings, logs; offers cancel/retry. */
export function JobPanel({ jobId, onDone, compact }: { jobId: string; onDone?: (j: Job) => void; compact?: boolean }) {
  const [job, setJob] = useState<Job | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [showLogs, setShowLogs] = useState(!compact);
  const [id, setId] = useState(jobId);
  useEffect(() => setId(jobId), [jobId]);
  useEffect(() => {
    let alive = true; let timer: any;
    const tick = async () => {
      try {
        const j = await api.get<Job>(`/jobs/${id}`);
        if (!alive) return;
        setJob(j); setErr(null);
        if (j.status === "queued" || j.status === "running") timer = setTimeout(tick, 1500);
        else onDone?.(j);
      } catch (e) { if (alive) { setErr(e); timer = setTimeout(tick, 4000); } }
    };
    tick();
    return () => { alive = false; clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);
  if (err && !job) return <ErrorState error={err} />;
  if (!job) return <div className="muted">Loading job…</div>;
  const active = job.status === "queued" || job.status === "running";
  return (
    <div className="card" style={{ marginTop: 8 }}>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
        <div><Status s={job.status} /> <span className="mono muted">{job.type} · {job.id} · attempt {job.attempt}{job.duration_seconds != null ? ` · ${job.duration_seconds}s` : ""}</span></div>
        <div className="row">
          {active && <button className="btn secondary small" onClick={() => api.post(`/jobs/${job.id}/cancel`).then(() => setId(job.id + ""))}>Cancel</button>}
          {(job.status === "failed" || job.status === "cancelled") && <button className="btn secondary small" onClick={async () => { const j = await api.post<Job>(`/jobs/${job.id}/retry`); setJob(null); setId(j.id); }}>Retry</button>}
          <button className="btn secondary small" onClick={() => setShowLogs(!showLogs)}>{showLogs ? "Hide" : "Show"} logs</button>
        </div>
      </div>
      {active && <div className="muted" style={{ marginTop: 4 }}>{job.status === "queued" ? "Waiting for a worker to pick this up…" : "Running…"}</div>}
      {job.error && <ErrorState error={new ApiError(0, job.error.code, job.error.message, job.error.details)} />}
      {showLogs && <JobLogs id={job.id} status={job.status} />}
    </div>
  );
}

function JobLogs({ id, status }: { id: string; status: string }) {
  const [logs, setLogs] = useState("");
  useEffect(() => { fetch(`${BASE}/jobs/${id}/logs`).then((r) => r.text()).then(setLogs).catch(() => setLogs("(logs unavailable)")); }, [id, status]);
  return <pre className="log">{logs || "(no log lines yet)"}</pre>;
}

export function Bytes({ n }: { n: number }) { return <>{n > 1e6 ? `${(n / 1e6).toFixed(2)} MB` : `${(n / 1e3).toFixed(1)} kB`}</>; }
