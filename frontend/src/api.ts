// Thin client for /api/v2. Every non-2xx is turned into ApiError carrying the server's {error:{code,message,details}}.
export const BASE = "/api/v2";

const TOKEN_KEY = "climsmart_v2_write_token";
export const getToken = () => localStorage.getItem(TOKEN_KEY);
export const setToken = (t: string) => (t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY));

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public details?: unknown) { super(message); }
}

async function req<T>(method: string, path: string, body?: unknown, form?: FormData): Promise<T> {
  let res: Response;
  try {
    res = await fetch(BASE + path, {
      method,
      headers: { ...(body !== undefined ? { "Content-Type": "application/json" } : {}), ...(method !== "GET" && getToken() ? { "X-API-Key": getToken()! } : {}) },
      body: form ?? (body !== undefined ? JSON.stringify(body) : undefined),
    });
  } catch (e) {
    throw new ApiError(0, "network_error", "Could not reach the ClimSmart-OP API. Check your connection and retry.");
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  let data: any = null;
  try { data = text ? JSON.parse(text) : null; } catch { /* non-JSON */ }
  if (!res.ok) {
    const e = data?.error;
    throw new ApiError(res.status, e?.code ?? `http_${res.status}`, e?.message ?? (data?.detail ?? res.statusText), e?.details);
  }
  return data as T;
}

export const api = {
  get: <T,>(p: string) => req<T>("GET", p),
  post: <T,>(p: string, b?: unknown) => req<T>("POST", p, b ?? {}),
  patch: <T,>(p: string, b: unknown) => req<T>("PATCH", p, b),
  del: (p: string) => req<void>("DELETE", p),
  upload: <T,>(p: string, f: File) => { const fd = new FormData(); fd.append("file", f); return req<T>("POST", p, undefined, fd); },
};

export interface Page<T> { items: T[]; total: number; limit?: number; offset?: number }
export interface Plantation { id: string; name: string; code?: string | null; organization?: string | null; plot_count?: number }
export interface Plot {
  id: string; plantation_id: string; name: string; code?: string | null; crop: string; planting_year?: number | null;
  area_ha: number; centroid: { lat: number; lon: number }; bbox: [number, number, number, number]; geometry?: any;
  created_at: string;
}
export interface Artifact { id: string; kind: string; filename: string; sha256: string; size_bytes: number; download_url: string; provenance: any; created_at: string }
export interface Job {
  id: string; type: string; status: "queued" | "running" | "succeeded" | "failed" | "cancelled"; error?: { code: string; message: string; details?: any } | null;
  result?: any; attempt: number; created_at: string; started_at?: string | null; finished_at?: string | null; duration_seconds?: number | null;
  resource_type?: string; resource_id?: string; logs?: string;
}
export interface MetFile {
  id: string; plot_id: string; name: string; start_date: string; end_date: string; strategy: string; status: string;
  latitude: number; longitude: number; tav?: number | null; amp?: number | null; validation?: any; sources?: any;
  artifact?: Artifact | null; job_id?: string; created_at: string;
}
export interface Param { type: "date" | "number"; label: string; unit?: string; min?: number; max?: number; default?: any; required?: boolean; help?: string }
export interface Template { id: string; slug: string; version: string; name: string; description?: string; sha256: string; parameters: Record<string, Param>; source?: any }
export interface Config { id: string; plot_id: string; template_id: string; met_file_id: string; name: string; version: number; params: Record<string, any>; status: string; apsimx_artifact?: Artifact | null; created_at: string }
export interface RunOutput { id: string; table_name: string; row_count: number; first_date?: string; last_date?: string; variables: { name: string; dtype: string; unit?: string | null }[] }
export interface Run {
  id: string; config_id: string; plot_id: string; label?: string | null; status: string; job_id: string; runtime?: any;
  runtime_seconds?: number | null; db_artifact_id?: string | null; log_artifact_id?: string | null; provenance?: any; error?: any;
  created_at: string; finished_at?: string | null; outputs?: RunOutput[];
}
export interface Summary { plot: Plot; met_files: MetFile[]; configs: Config[]; runs: Run[]; artifacts: Artifact[]; history: Job[] }
export interface Accepted<K extends string, V> { job: Job; [k: string]: any; _?: Record<K, V> }
