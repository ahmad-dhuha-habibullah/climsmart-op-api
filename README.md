# ClimSmart-OP on Vercel: bundle layout and notes

This describes the **separate Vercel-layout copy** (`/workspace/climsmart-deploy`). It is built from this repo
(`api/`, `climsmart/`, `config/`, `docs/examples/`, `data/apsim_results/`) plus the built front end in `public/`.
Indicative, not validated: nothing here has been calibrated to field data.

## Layout

```
api/index.py            ASGI entry: `app = create_app()` (Vercel Python runtime)
climsmart/              the API package (no tests, no scripts)
config/*.yaml           thresholds + agronomy defaults (read at runtime)
data/apsim_results/     cached REAL APSIM-X result (mambang_muda_latest.json), served by /v1/apsim-*
docs/examples/*.json    OpenAPI examples, loaded at import (the app does not start without them)
public/                 the built web app (= frontend/dist), served as static files by Vercel
vercel.json             rewrites /health, /v1/*, /docs, /redoc, /openapi.json to api/index.py; maxDuration 60; includeFiles
requirements.txt        fastapi, httpx, pydantic, pyyaml (no uvicorn needed on Vercel)
.env.example            every variable (all optional)
```

NOT in the bundle: the founder's `.apsimx` / `.met` files (`data/apsim/`), tests, front-end source and `node_modules`,
scripts, screenshots, `.env`, any `.vercel` link. The web app is routed by query string (`?page=weather`), so no SPA
path fallback rewrite is needed.

## Environment variables (none required)

| Variable | Default | Vercel note |
|---|---|---|
| `CLIMSMART_API_KEYS` | empty (open) | **Leave empty** for the public site: the web app sends no key, a key would make every call return 401 |
| `CLIMSMART_RATE_LIMIT_PER_MIN` | 60 | per client **per serverless instance** (see below) |
| `CLIMSMART_CACHE_TTL_SECONDS` | 3600 | per instance |
| `CLIMSMART_CORS_ORIGINS` | `*` | the web app is same-origin; set to your domain(s) to stop other sites' browsers calling the API |
| `CLIMSMART_TRUSTED_PROXIES` | `0.0.0.0/0,::/0` (set by `api/index.py` if unset or empty) | see "Proxy trust" |
| `CLIMSMART_PROVIDER_RETRIES` / `_BACKOFF_SECONDS` | 2 / 0.5 | Open-Meteo: 10 s timeout x 3 tries + backoff = about 31 s worst case, hence `maxDuration` 60 |
| `CLIMSMART_STALE_MAX_SECONDS` | 21600 | stale-weather window, per instance |
| `CLIMSMART_LOG_LEVEL` | INFO | access log (hashed client id; no keys, no coordinates) |
| `OPEN_METEO_API_KEY` | empty | the FREE Open-Meteo tier is **non-commercial only**; set a paid key for commercial use |
| `CLIMSMART_APSIM_RESULTS_FILE`, `_THRESHOLDS_FILE`, `_AGRONOMY_FILE` | bundled files | only to override |

## Proxy trust (X-Forwarded-For)

Vercel's edge overwrites `X-Forwarded-For` with the real client IP; the function's direct peer is an internal address.
`api/index.py` therefore trusts any peer (`0.0.0.0/0,::/0`) and the rate limiter keys on the first `X-Forwarded-For` hop.
This is safe **only** while Vercel is the outermost hop. If a CDN/WAF/proxy is ever put in front of Vercel, set
`CLIMSMART_TRUSTED_PROXIES` to that proxy's ranges, otherwise either everyone shares one bucket or the header is spoofable.

## What "per instance" means on serverless

The rate limiter, response cache and stale-weather store are plain in-memory objects. Vercel may run several instances at
once and recycles them, so: (a) the real limit is roughly 60/min **per instance** per client, not global; (b) after a cold
start the cache and the stale store are empty, so the first request for a place always calls Open-Meteo and a provider
outage right after a cold start returns 502/503 instead of "stale weather" (the app shows its saved-example/error states);
(c) the stale banner is only possible while an instance stays warm. A shared store (e.g. Redis/KV) would fix all three; it is not built.

## Limits that matter (Vercel docs read 17 Oct 2026; plan not inspected)

Python function bundle up to 500 MB uncompressed (this bundle is about 2.8 MB); 4.5 MB request/response body (largest
response here about 120 KB, `/openapi.json`); duration: Hobby with fluid compute defaults/maxes at 300 s, without it the
default is short (10 s), so `vercel.json` pins `maxDuration` 60 (valid on Hobby). The APSIM-X numbers are a cached file
(the server never runs APSIM-X).

## Local check of this layout

`scripts/vercel_layout_local.py --root <bundle> --port 4180` is a **local mimic** (static `public/` first, then the
`vercel.json` rewrites to `api/index.py`, `X-Forwarded-For` overwritten). It does not prove the real Vercel build, Python
version, `includeFiles`, cold starts, per-instance behaviour, edge caching or timeouts.

## v2 platform (alpha) — `/api/v2` and `/app/`

The v1 API above is unchanged. v2 adds plantations/plots (GeoJSON), plot weather, model-only MET generation, APSIM configs
linked to a MET artifact, APSIM runs as jobs, outputs/compare and a plot summary. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

```bash
pip install -r requirements.txt -r requirements-dev.txt
alembic upgrade head                                   # optional: the API migrates on start (DB_AUTO_MIGRATE=1)
APSIM_COMMAND="dotnet /opt/apsim/Models.dll" uvicorn api.index:app --port 8077   # backend (v1 + /api/v2)
JOB_MODE=worker ... && python -m app.workers.run       # optional separate worker
cd frontend && npm ci && npm run build && npm run preview   # UI on http://localhost:5173/app/ (proxies /api to 8077)
python scripts/e2e_v2.py --base http://127.0.0.1:8077  # end-to-end check (real Open-Meteo, real APSIM if configured)
pytest -q && ruff check .
```

Without an APSIM runtime, runs fail honestly with `apsim_runtime_unavailable`. On Vercel without `DATABASE_URL`, v2 storage is an
ephemeral `/tmp` sqlite. No authentication yet. Indicative, not validated.
