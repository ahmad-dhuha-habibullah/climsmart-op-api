# ClimSmart-OP v2 — architecture (vertical slice)

**Indicative, not validated.** Weather is gridded model data; APSIM output is uncalibrated model output.

## Topology
- `api/index.py` → `app.main.create_combined_app()`: the unchanged v1 app (`climsmart/`) with the v2 FastAPI sub-app
  mounted at `/api/v2` (its own `/api/v2/openapi.json`, `/api/v2/docs`). The v1 `/openapi.json` is pinned byte-for-byte by
  `tests/v1/fixtures/openapi_v1_baseline.json`.
- Frontend: `frontend/` (Vite + React + TS + MapLibre + terra-draw + ECharts) built into `public/app`, served at `/app/`.
  The existing v1 UI in `public/` is untouched.
- Jobs: `JOB_MODE=inline` runs jobs in the API process after the 202 response; `JOB_MODE=worker` leaves them queued for
  `python -m app.workers.run` (claims with a compare-and-set UPDATE, so several workers are safe).

## Modules (`app/`)
| module | role |
|---|---|
| core | settings (env), SQLAlchemy engine/sessions, Alembic runner, error envelope `{error:{code,message,details}}` |
| models | Plantation, Plot (GeoJSON + area/centroid/bbox), Job, Artifact, MetFile, ApsimTemplate, ApsimConfig, ApsimRun, RunOutput |
| spatial | GeoJSON validation (closed rings, WGS84, OGC validity via shapely), geodesic area (pyproj) |
| adapters/weather | Open-Meteo archive (≤ today−8) + forecast; no gap filling; provenance |
| met | `model_only` strategy, validation report, tav/amp, APSIM MET rendering |
| adapters/apsim | template inspection + whitelisted params → server-generated APSIMX; runner (APSIM_COMMAND or docker `--network none`); output .db reader |
| storage | `ArtifactStore` interface, `LocalArtifactStore` (S3 implementation not written yet); sha256 + provenance per artifact |
| services | plots/MET/APSIM/jobs business logic |
| api/v2 | routers: plantations & plots, weather/MET/APSIM, jobs/artifacts/health |

## Data flow
plot polygon → centroid → Open-Meteo daily → MET (validated, sha256 artifact) → APSIM config (template sha + MET sha + params,
immutable, versioned) → run job → `simulation.db` artifact → output catalog/query/compare → plot summary.

## Deployment notes
- Vercel without `DATABASE_URL`: v2 uses an **ephemeral** `/tmp` sqlite (`ephemeral_storage: true` in `/api/v2/system/health`);
  data vanishes on cold starts. Set `DATABASE_URL` (PostgreSQL) for durable data, and an external artifact store.
- APSIM cannot run on Vercel: run a worker elsewhere with `APSIM_COMMAND` and the same `DATABASE_URL`/artifact store.

## Figma mapping (file `JKrAcja8OHBc9PaEHr0yWI`, "ClimSmart-OP v2 screens")
- Tokens (colours, Plus Jakarta Sans, 2 px radius, 208 px sidebar, source tags, status chips, trust bar, saved-data banner)
  from the *Design system* page → `frontend/src/styles.css`.
- Error/404 states follow frames 15:1995 (502), 15:2356 (503 retry), 19:5308 (APSIM 404); results view follows 19:3997.
- **Not in Figma yet:** Spatial Workspace, plot panel, MET generation/report, APSIM config/run/compare, jobs.
- Reference PNGs: `/workspace/figma/design_system_components.png`, `overview_map_mode.png`, `apsim_simulations.png` (box only, not committed).

## Not implemented (by design in this slice)
Authentication, sensor assimilation (returns 422 `strategy_not_available`), sensors, dashboard builder, PostGIS, S3 store.
The registered template's soil is the Higaturu series (PNG), not a Sumatran soil.
