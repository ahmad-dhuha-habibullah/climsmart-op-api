"""FastAPI app: /health, /v1/forecast, /v1/water-balance, /v1/outlook, /v1/apsim-outlook."""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from .. import __version__
from ..apsim.results import STALE_AFTER_HOURS, ApsimResults, ApsimResultsUnavailable
from ..agronomy import CUTOFF_STATUS, Agronomy, load_agronomy
from ..config import Settings
from ..engines import BucketEngine, EngineParams
from ..providers.base import ProviderError, WeatherProvider, WeatherSeries
from ..providers.cache import CachedProvider
from ..providers.openmeteo import OpenMeteoProvider
from ..rules import ThresholdSet, load_thresholds
from . import csv_export
from . import examples as ex
from .. import costbenefit as cb
from .. import fertiliser_timing as ftm
from . import fertiliser_build as ftb
from .costbenefit_schemas import CostBenefitResponse
from .fertiliser_schemas import FertiliserAvailability, FertiliserTimingResponse
from .series_schemas import ApsimSeriesResponse
from ..apsim.series_results import SeriesUnavailable, age_hours as series_age_hours, load_series
from .apsim_schemas import (ApsimBlock, ApsimEngineInfo, ApsimMeta, ApsimMonth, ApsimOutlookResponse,
                            ApsimPoint, ApsimRun, ApsimScenario, ApsimScenarioSummary, BalanceBlock, BalanceDay)
from . import schemas as s
from .security import RateLimiter, guard, identify, parse_networks

access_log = logging.getLogger("climsmart.access")

STATUS = "INDICATIVE: model-derived, not agronomically validated, not calibrated to field data."
DISCLAIMERS = [
    "Indicative decision-support only. Outputs are model-derived and have NOT been validated "
    "against field observations.",
    "Not a yield forecast. No yield, cost-saving or profit claim is made or implied.",
    "Weather values are gridded model data for the requested point, not on-site measurements.",
    "Thresholds and crop parameters are provisional; a local agronomist should review any decision.",
]

DESCRIPTION = """
**Weekly soil-water-stress outlook for a point location, aimed at oil-palm plantations.**
Think "Open-Meteo, but for plantation water stress".

> **INDICATIVE, NOT VALIDATED.** Every number is model-derived from gridded weather data. Nothing here
> has been checked against field observations; the class cut-offs (0.3 / 0.5 / 0.7 of plant-available
> water) have *no citable source*. This is not a yield forecast and makes no savings claim. Every
> response repeats this in its `meta` block.

## Quick start
```
curl "https://YOUR-HOST/v1/outlook?lat=-2.79&lon=104.60&days=7"
```
Examples in curl, JavaScript and Python are in the project README (`docs/EXAMPLES.md`).

## Endpoints
* `GET /v1/forecast`: normalised daily rain, ET0, temperature.
* `GET /v1/water-balance`: daily soil-water deficit and stress (FAO-56 style single bucket).
* `GET /v1/outlook`: Low / Moderate / High / Critical per day plus a summary (**start here**).
* `GET /health`: liveness.

## Authentication
Demo mode is open. If the operator sets API keys, send `X-API-Key: <key>` on every `/v1` request
(401 without a valid key).

## Errors (JSON `{"detail": "..."}`)
| Status | Meaning | What to do |
|---|---|---|
| 401 | Missing/invalid API key | Send `X-API-Key` |
| 422 | Invalid input (e.g. latitude outside -90..90). Body is a list of field errors | Fix the parameter |
| 429 | **You** exceeded the rate limit (per minute). `Retry-After` header = seconds | Wait, then retry |
| 502 | Weather provider failed (timeout, server error, bad data) | Retry shortly |
| 503 | Weather provider is rate-limiting **us**. `Retry-After` header = seconds | Wait, then retry |

## Stale data
If the weather provider is down and a cached answer (up to 48 h old by default, labelled stale) or a committed last-good snapshot for the demo site exists, it is served
with `meta.weather_stale = true`, `meta.weather_age_seconds`, a `STALE WEATHER` line in
`meta.assumptions`, and the response header `X-ClimSmart-Data: stale; age=<seconds>s`.

## Weather data and licence
Weather: [Open-Meteo.com](https://open-meteo.com), CC BY 4.0. **The free Open-Meteo tier is
non-commercial only.** Show the attribution text from `meta.sources`.
"""

TAGS = [
    {"name": "v1", "description": "Weather and water-stress endpoints (indicative, not validated)."},
    {"name": "system", "description": "Service status."},
]

ERROR_RESPONSES = {
    401: {"model": s.ErrorResponse, "description": "Missing or invalid `X-API-Key` (only when keys are configured)."},
    429: {"model": s.ErrorResponse, "description": "You exceeded the per-minute rate limit. See `Retry-After` (seconds).",
          "headers": {"Retry-After": {"description": "Seconds to wait", "schema": {"type": "integer"}}}},
    502: {"model": s.ErrorResponse, "description": "Weather provider failed (timeout, server error or unusable data) "
                                                   "and no recent cached answer exists."},
    503: {"model": s.ErrorResponse, "description": "Weather provider is rate-limiting this service. See `Retry-After`.",
          "headers": {"Retry-After": {"description": "Seconds to wait", "schema": {"type": "integer"}}}},
}
OK_HEADERS = {"X-ClimSmart-Data": {"description": "Present only when stale cached weather is served: "
                                                  "`stale; age=<seconds>s`.", "schema": {"type": "string"}}}



def _param_sources(agro: Agronomy, pu: s.ParamsUsed | None, thresholds: ThresholdSet | None
                   ) -> list[s.ParameterSource]:
    out: list[s.ParameterSource] = []
    if pu is not None:
        for name, spec in agro.parameters.items():
            used = getattr(pu, name)
            over = name in pu.overridden
            note = " ".join(spec.source.split())
            out.append(s.ParameterSource(
                name=name, value=used,
                status=("caller override (not the config default); default is " +
                        f"{spec.value} [{spec.status}]") if over else spec.status,
                source=note))
    if thresholds is not None:
        cuts = ", ".join(f"{c.name} >= {c.min:g}" for c in thresholds.classes[1:])
        out.append(s.ParameterSource(
            name="outlook_class_cutoffs", value=cuts, status=CUTOFF_STATUS,
            source=("The class cut-offs (fractions of plant-available water) have no citable source; "
                    "they are a provisional display scale, not agronomically validated. "
                    f"(config/thresholds.yaml, version {thresholds.version})")))
    return out


def weather_label(series: WeatherSeries | None) -> tuple[str | None, str | None]:
    """(weather_source, weather_note): where the weather came from and a plain sentence for the UI when it is not live."""
    if series is None:
        return None, None
    if not series.extra.get("stale"):
        return "live", None
    origin = series.extra.get("origin", "memory")
    try:
        t = datetime.fromisoformat(series.fetched_at).astimezone(ZoneInfo(series.timezone))
        when = t.strftime("%H:%M on %d %b")
    except Exception:
        when = series.fetched_at
    kind = "saved forecast" if origin == "snapshot" else "forecast"
    return ("snapshot" if origin == "snapshot" else "stale_cache"), f"Showing {kind} from {when} (local time at the site): the weather source is busy."


def _meta(series: WeatherSeries | None, engine=None, assumptions: list[str] | None = None,
          parameter_sources: list[s.ParameterSource] | None = None) -> s.Meta:
    assumptions = list(assumptions or [])
    if series and series.extra.get("leading_days_trimmed"):
        assumptions.append(f"The source had no data for the first {series.extra['leading_days_trimmed']} "
                           "requested past days at this point, so the spin-up period is shorter.")
    stale = bool(series and series.extra.get("stale"))
    if stale:
        assumptions.append(
            f"STALE WEATHER: the weather provider was unavailable, so cached data about "
            f"{series.extra.get('age_seconds', 0) // 60} min old is shown. Results may be out of date.")
    wsource, wnote = weather_label(series)
    sources = []
    if series:
        sources.append(s.SourceInfo(
            name=series.source_name, url=series.source_url,
            attribution=series.source_attribution, license_note=series.source_license_note))
    model = None
    all_assumptions = list(assumptions or [])
    if engine:
        model = s.ModelInfo(name=engine.info.name, version=engine.info.version,
                            description=engine.info.description)
        all_assumptions = engine.info.assumptions + all_assumptions
    return s.Meta(
        api_version=__version__, status=STATUS, sources=sources, model=model,
        assumptions=all_assumptions, disclaimers=DISCLAIMERS,
        parameter_sources=parameter_sources or [],
        weather_fetched_at=series.fetched_at if series else None, weather_stale=stale,
        weather_age_seconds=series.extra.get("age_seconds") if stale else None,
        weather_source=wsource, weather_note=wnote, weather_timezone=series.timezone if series else None,
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


def _spinup_note(pu: s.ParamsUsed) -> str:
    return (f"Spin-up: the soil-water model starts {pu.spinup_days} days before the first forecast day with a "
            f"deficit of {pu.initial_depletion_mm:g} mm (0 = full store) and runs forward on recent weather. "
            "The early part of the run is less reliable and the result is unvalidated.")


def _loc(series: WeatherSeries, lat: float, lon: float) -> s.Location:
    return s.Location(latitude=series.latitude if series.latitude is not None else lat,
                      longitude=series.longitude if series.longitude is not None else lon,
                      elevation_m=series.elevation_m, timezone=series.timezone)


class Point:
    def __init__(self, lat: float = Query(..., ge=-90, le=90, examples=[2.6235],
                                          description="Latitude, decimal degrees (-90..90). South is negative."),
                 lon: float = Query(..., ge=-180, le=180, examples=[99.6922],
                                    description="Longitude, decimal degrees (-180..180).")):
        self.lat, self.lon = lat, lon


class BalanceQuery(Point):
    """Query parameters. Soil/crop values left out use the defaults in config/agronomy.yaml."""

    def __init__(
        self,
        lat: float = Query(..., ge=-90, le=90, description="Latitude of the location, decimal degrees (-90..90). South is negative.", examples=[2.6235]),
        lon: float = Query(..., ge=-180, le=180, description="Longitude of the location, decimal degrees (-180..180).", examples=[99.6922]),
        days: int = Query(7, ge=1, le=16, description="Forecast days to return (1-16; 7 = weekly outlook)"),
        history_days: int = Query(7, ge=0, le=60, description="How many recent past days to include in the answer (the spin-up itself is always run)"),
        root_zone_capacity_mm: float | None = Query(
            None, ge=20, le=600,
            description="Plant-available water in the root zone, mm. Default 115 mm per 1 m root zone "
                        "(literature range about 90-150 mm; see meta.parameter_sources)"),
        kc: float | None = Query(None, ge=0.2, le=1.5,
                                 description="Crop coefficient, mature oil palm. Default 1.0 (provisional)"),
        depletion_fraction_p: float | None = Query(
            None, ge=0.1, le=0.9,
            description="Fraction of capacity usable before stress starts. Default 0.65 (provisional)"),
        initial_depletion_mm: float = Query(0.0, ge=0, le=600,
                                            description="Soil-water deficit at the first spin-up day, mm (0 = full store). Cannot exceed root_zone_capacity_mm"),
        runoff_fraction: float = Query(0.0, ge=0, le=0.9, description="Share of rain treated as runoff, 0-0.9 (default 0 = ignored)"),
        spinup_days: int = Query(60, ge=7, le=92, description="Past days used to warm the model up, 7-92 (more = less dependence on the full-store start)"),
    ):
        super().__init__(lat, lon)
        self.days, self.history_days, self.spinup_days = days, history_days, spinup_days
        self._raw = {"root_zone_capacity_mm": root_zone_capacity_mm, "kc": kc,
                     "depletion_fraction_p": depletion_fraction_p}
        self._initial, self._runoff = initial_depletion_mm, runoff_fraction

    def resolve(self, agro: Agronomy) -> tuple[EngineParams, list[str]]:
        vals = {k: (v if v is not None else agro.value(k)) for k, v in self._raw.items()}
        over = [k for k, v in self._raw.items() if v is not None]
        if self._initial > vals["root_zone_capacity_mm"]:
            raise HTTPException(422, "initial_depletion_mm cannot exceed root_zone_capacity_mm")
        return EngineParams(vals["root_zone_capacity_mm"], vals["kc"], vals["depletion_fraction_p"],
                            self._initial, self._runoff), over


class _ComparisonQuery:
    """Fixed query for the comparison block: same settings as the demo outlook defaults (7 days, 14 history, 60 spin-up)."""
    days, history_days, spinup_days = 7, 14, 60

    def __init__(self, lat: float, lon: float):
        self.lat, self.lon = lat, lon

    def resolve(self, agro: Agronomy) -> tuple[EngineParams, list[str]]:
        return EngineParams(agro.value("root_zone_capacity_mm"), agro.value("kc"), agro.value("depletion_fraction_p"),
                            0.0, 0.0), []

from pathlib import Path as _P
SNAPSHOT_DEFAULT = _P(__file__).resolve().parent.parent.parent / "data" / "forecast_snapshot"
HINDCAST_DEFAULT = _P(__file__).resolve().parent.parent.parent / "data" / "hindcast" / "founder_daily_rain_2024_2025.csv"


def create_app(provider: WeatherProvider | None = None, settings: Settings | None = None,
               thresholds: ThresholdSet | None = None, agronomy: Agronomy | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    if provider is None:
        provider = CachedProvider(
            OpenMeteoProvider(settings.open_meteo_api_key, retries=settings.provider_retries,
                              backoff_seconds=settings.provider_backoff_seconds,
                              budget_seconds=settings.provider_budget_seconds),
            settings.cache_ttl_seconds, stale_max_seconds=settings.stale_max_seconds,
            standard_window=(60, 14), realign_stale=True,
            snapshot_dir=settings.forecast_snapshot_dir or SNAPSHOT_DEFAULT)
    thresholds = thresholds or load_thresholds(settings.thresholds_file)
    agronomy = agronomy or (load_agronomy(settings.agronomy_file) if settings.agronomy_file
                            else load_agronomy())

    app = FastAPI(title="ClimSmart-OP API", version=__version__, description=DESCRIPTION,
                  openapi_tags=TAGS,
                  docs_url="/docs", redoc_url="/redoc")
    app.state.settings = settings
    app.state.provider = provider
    app.state.thresholds = thresholds
    app.state.agronomy = agronomy
    app.state.ft_assumptions = ftm.load_assumptions(settings.fertiliser_timing_file)
    app.state.trusted_networks = parse_networks(settings.trusted_proxies)
    app.state.engine = BucketEngine()
    app.state.limiter = RateLimiter(settings.rate_limit_per_min)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins,
                       allow_methods=["GET"], allow_headers=["X-API-Key"])

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        t0 = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:  # path only (no query string: it holds coordinates); client is a hash, never the key
            access_log.info("method=%s path=%s status=%s duration_ms=%.1f client=%s", request.method,
                            request.url.path, status, (time.perf_counter() - t0) * 1000,
                            identify(request))

    async def _weather(request: Request, lat, lon, past, fc, response: Response | None = None
                       ) -> WeatherSeries:
        try:
            series = await request.app.state.provider.get_daily(lat, lon, past, fc)
        except ProviderError as e:
            if e.kind == "rate_limited":
                wait = str(max(1, min(e.retry_after or 30, 120)))
                raise HTTPException(503, "Weather provider is rate-limiting us; try again shortly",
                                    headers={"Retry-After": wait})
            raise HTTPException(502, f"Weather provider unavailable ({e.kind}); try again shortly",
                                headers={"Retry-After": "15"})
        if response is not None and series.extra.get("stale"):
            response.headers["X-ClimSmart-Data"] = f"stale; age={series.extra.get('age_seconds', 0)}s"
        return series

    async def _balance(request: Request, q: BalanceQuery, response: Response):
        series = await _weather(request, q.lat, q.lon, q.spinup_days, q.days, response)
        params, overridden = q.resolve(request.app.state.agronomy)
        results = request.app.state.engine.run(series.days, params)
        keep = [r for r in results if r.is_forecast] + \
               ([r for r in results if not r.is_forecast][-q.history_days:] if q.history_days else [])
        keep.sort(key=lambda r: r.date)
        pu = s.ParamsUsed(root_zone_capacity_mm=params.root_zone_capacity_mm, kc=params.kc,
                          depletion_fraction_p=params.depletion_fraction_p,
                          initial_depletion_mm=params.initial_depletion_mm,
                          runoff_fraction=params.runoff_fraction, spinup_days=q.spinup_days,
                          overridden=overridden)
        return series, keep, pu

    def kind(r) -> str:
        return "forecast" if r.is_forecast else "past"

    def _load_apsim(request: Request, lat, lon) -> ApsimResults:
        try:
            res = ApsimResults.load(request.app.state.settings.apsim_results_file)
        except ApsimResultsUnavailable:
            raise HTTPException(503, "APSIM results have not been generated on this server yet")
        sp = res.data["supported_point"]
        if (lat is None) != (lon is None):
            raise HTTPException(422, "give both lat and lon, or neither")
        if lat is not None and (abs(lat - sp["lat"]) > 0.05 or abs(lon - sp["lon"]) > 0.05):
            raise HTTPException(404, f"APSIM results exist only for {sp['name']} (lat {sp['lat']}, lon {sp['lon']})")
        return res

    def _csv_response(text: str, filename: str, injected: Response) -> Response:
        out = Response(content=text, media_type="text/csv; charset=utf-8",
                       headers={"Content-Disposition": f'attachment; filename="{filename}"'})
        if "x-climsmart-data" in injected.headers:  # keep the stale-weather flag
            out.headers["X-ClimSmart-Data"] = injected.headers["x-climsmart-data"]
        return out

    @app.get("/health", tags=["system"], summary="Liveness check", response_model=s.HealthResponse,
             description="Returns `ok` when the service is running. Needs no API key and is not rate limited. "
                         "It does not call the weather provider.")
    async def health():
        return {"status": "ok", "version": __version__,
                "auth": "api-key" if settings.api_keys else "open (demo mode)"}

    @app.get("/v1/forecast", response_model=s.ForecastResponse, tags=["v1"],
             dependencies=[Depends(guard)],
             summary="Daily weather: recent past + forecast",
             description="Normalised daily rain, FAO-56 reference evapotranspiration (ET0) and temperature "
                         "for a point: `past_days` before today, then `forecast_days` from today. "
                         "Data come from a gridded weather model, not from on-site measurements. "
                         "Use it to check the inputs behind the water-stress numbers.",
             responses={200: {"description": "Daily weather for the point.", "headers": OK_HEADERS, "content": {"application/json": {"example": ex.FORECAST}}}, **ERROR_RESPONSES})
    async def forecast(request: Request, response: Response, p: Point = Depends(),
                       past_days: int = Query(7, ge=0, le=92, description="Recent past days to include (0-92)"),
                       forecast_days: int = Query(7, ge=1, le=16, description="Forecast days to include (1-16)")):
        series = await _weather(request, p.lat, p.lon, past_days, forecast_days, response)
        return s.ForecastResponse(
            meta=_meta(series), location=_loc(series, p.lat, p.lon),
            daily=[s.ForecastDay(date=d.date, kind="forecast" if d.is_forecast else "past",
                                 rain_mm=d.rain_mm, et0_mm=d.et0_mm, temp_max_c=d.temp_max_c,
                                 temp_min_c=d.temp_min_c, rain_probability_pct=d.rain_probability_pct)
                   for d in series.days])

    @app.get("/v1/water-balance", response_model=s.WaterBalanceResponse, tags=["v1"],
             dependencies=[Depends(guard)],
             summary="Daily soil-water balance (FAO-56 style bucket)",
             description="Runs a transparent single-layer FAO-56 style soil-water bucket over a spin-up period "
                         "(`spinup_days` of recent past, starting from a full store unless "
                         "`initial_depletion_mm` is set) and then the forecast. Returns the daily deficit (mm), "
                         "stress coefficient `ks` and water fluxes. Crop and soil defaults (115 mm per 1 m root "
                         "zone, Kc 1.0, p 0.65) are literature-based and **provisional**: see "
                         "`meta.parameter_sources`; override them with query parameters. "
                         "**Indicative, not validated; not a yield forecast.**",
             responses={200: {"description": "Daily soil-water balance.", "headers": OK_HEADERS, "content": {"application/json": {"example": ex.WATER_BALANCE}}}, **ERROR_RESPONSES})
    async def water_balance(request: Request, response: Response, q: BalanceQuery = Depends()):
        series, keep, pu = await _balance(request, q, response)
        return s.WaterBalanceResponse(
            meta=_meta(series, request.app.state.engine, [_spinup_note(pu)],
                       parameter_sources=_param_sources(request.app.state.agronomy, pu, None)),
            location=_loc(series, q.lat, q.lon), parameters=pu,
            daily=[s.WaterDay(date=r.date, kind=kind(r), rain_mm=r.rain_mm, et0_mm=r.et0_mm,
                              etc_potential_mm=round(r.etc_potential_mm, 3),
                              etc_actual_mm=round(r.etc_actual_mm, 3),
                              drainage_mm=round(r.drainage_mm, 3), deficit_mm=round(r.deficit_mm, 2),
                              deficit_fraction=round(r.deficit_fraction, 4), ks=round(r.ks, 4),
                              stress_ratio=round(r.stress_ratio, 4)) for r in keep])

    async def _build_outlook(request: Request, response: Response, q: BalanceQuery) -> s.OutlookResponse:
        ts: ThresholdSet = request.app.state.thresholds
        series, keep, pu = await _balance(request, q, response)
        days = []
        for r in keep:
            c = ts.classify(r.deficit_fraction)
            days.append(s.OutlookDay(date=r.date, kind=kind(r), category=c.name, label=c.label,
                                     deficit_mm=round(r.deficit_mm, 2),
                                     deficit_fraction=round(r.deficit_fraction, 4),
                                     stress_ratio=round(r.stress_ratio, 4), rain_mm=r.rain_mm))
        fdays = [d for d in days if d.kind == "forecast"]
        rank = {n: i for i, n in enumerate(ts.order)}
        peak = max(fdays, key=lambda d: (rank[d.category], d.deficit_fraction)) if fdays else None
        high_i = rank.get("high", len(rank))
        first_high = next((d.date for d in fdays if rank[d.category] >= high_i), None)
        counts = {n: sum(1 for d in fdays if d.category == n) for n in ts.order}
        summary = s.OutlookSummary(forecast_days=len(fdays),
                                   peak_category=peak.category if peak else ts.order[0],
                                   peak_date=peak.date if peak else None,
                                   first_high_or_worse_date=first_high, days_per_category=counts)
        tout = s.ThresholdsOut(version=ts.version, status=ts.status, metric=ts.metric,
                               cutoff_source=ts.cutoff_source,
                               classes=[s.ThresholdOut(**c.model_dump()) for c in ts.classes])
        meta = _meta(series, request.app.state.engine,
                     [_spinup_note(pu), "Category thresholds are provisional and not agronomically validated "
                      f"(threshold file version {ts.version}). The 0.3/0.5/0.7 class cut-offs have "
                      "no citable source."],
                     _param_sources(request.app.state.agronomy, pu, ts))
        return s.OutlookResponse(meta=meta, location=_loc(series, q.lat, q.lon), parameters=pu,
                                 thresholds=tout, summary=summary, daily=days)


    @app.get("/v1/outlook", response_model=s.OutlookResponse, tags=["v1"],
             dependencies=[Depends(guard)],
             summary="Traffic-light water-stress outlook (start here)",
             description="Same model as `/v1/water-balance`, then each day is classed Low / Moderate / High / "
                         "Critical by soil-water deficit as a fraction of capacity, with a summary (peak class, "
                         "first day at High or worse, days per class). **The 0.3 / 0.5 / 0.7 cut-offs have no "
                         "citable source and are provisional**; the thresholds used are returned in "
                         "`thresholds`. **Indicative, not validated; not a yield forecast.**",
             responses={200: {"description": "Per-day class and summary.", "headers": OK_HEADERS, "content": {"application/json": {"example": ex.OUTLOOK}}}, **ERROR_RESPONSES})
    async def outlook(request: Request, response: Response, q: BalanceQuery = Depends()):
        return await _build_outlook(request, response, q)

    @app.get("/v1/outlook.csv", tags=["v1"], dependencies=[Depends(guard)],
             summary="Outlook as CSV (simple water balance)",
             description="The `/v1/outlook` daily table as a CSV file (same query parameters, same model, same "
                         "auth and rate limit). Every row has a `source` column (`simple-water-balance <engine "
                         "version>`) and the file starts with `#` comment lines stating the source, location, "
                         "parameters, the provisional class cut-offs and **INDICATIVE, NOT VALIDATED**. Skip lines "
                         "starting with `#` when parsing (pandas: `comment=\"#\"`). This is not the APSIM-X series: "
                         "use `/v1/apsim-outlook.csv` for that.",
             responses={200: {"description": "CSV with comment header lines.", "headers": OK_HEADERS,
                              "content": {"text/csv": {"example": ex.OUTLOOK_CSV}}}, **ERROR_RESPONSES})
    async def outlook_csv(request: Request, response: Response, q: BalanceQuery = Depends()):
        o = await _build_outlook(request, response, q)
        return _csv_response(csv_export.outlook_csv(o), "climsmart-outlook.csv", response)

    @app.get("/v1/apsim-outlook", response_model=ApsimOutlookResponse, tags=["v1"],
             dependencies=[Depends(guard)],
             summary="APSIM-X crop-model outlook (one demo point only)",
             description="Daily output of a real **APSIM Next Generation** oil-palm run, precomputed offline "
                         "(never per request), for **one place only: Mambang Muda, North Sumatra "
                         "(lat 2.6235, lon 99.6922)**. Asking for any other point returns 404. "
                         "Every number in `apsim` has `source = \"apsimx\"` and the response names the exact APSIM "
                         "version, run time, weather inputs, edits made to the founder's model file and a "
                         "list of limitations. `comparison` holds the simple FAO-56 water balance for the same "
                         "point, labelled `source = \"simple-water-balance\"`: the two come from different models "
                         "and their classes must not be mixed up. `lat`/`lon` are optional here and only checked. "
                         "**Indicative, not validated; the model is not calibrated for Sumatra; not a yield forecast.**",
             responses={200: {"description": "APSIM-X outlook with comparison.", "headers": OK_HEADERS,
                              "content": {"application/json": {"example": ex.APSIM_OUTLOOK}}},
                        404: {"model": s.ErrorResponse, "description": "Requested point is not the APSIM demo point."},
                        **ERROR_RESPONSES})
    async def apsim_outlook(request: Request, response: Response,
                            lat: float | None = Query(None, ge=-90, le=90, description="Optional. If given it must be within 0.05 deg of the supported point, else 404", examples=[2.6235]),
                            lon: float | None = Query(None, ge=-180, le=180, description="Optional, see lat", examples=[99.6922]),
                            scenario: str = Query("as_is", description="Scenario id for the daily list: as_is (default), older_palms_2008, younger_palms_2016, rain_scaled_to_station"),
                            compare: bool = Query(True, description="Also return the simple water balance for the same point (needs the weather provider)")):
        res = _load_apsim(request, lat, lon)
        sp = res.data["supported_point"]
        try:
            sc = res.scenario(scenario)
        except KeyError:
            raise HTTPException(422, f"unknown scenario; choose one of {', '.join(res.scenario_ids)}")
        d, age = res.data, res.age_hours()
        note = f"APSIM data exist only for {sp['name']}; everything here is precomputed, indicative and not validated."
        meta = ApsimMeta(source="apsimx", status=d.get("status", STATUS), generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                         precomputed=True, supported_point=ApsimPoint(**sp), note=note)
        apsim = ApsimBlock(
            source="apsimx", engine=ApsimEngineInfo(**d["engine"]),
            run=ApsimRun(**{k: d["run"][k] for k in ("started_utc", "runtime_seconds", "today_utc", "clock_end", "clock_start", "history_days_served")},
                         age_hours=age, result_stale=age > STALE_AFTER_HOURS),
            weather_inputs=d["weather_inputs"], soil=d["soil"], edits=d["edits"], limitations=d["limitations"],
            thresholds=d["thresholds"], scenario=ApsimScenario(**sc),
            other_scenarios=[ApsimScenarioSummary(**{k: x[k] for k in ("id", "description", "palm_age_years_at_today", "summary_next_7_days")})
                             for x in d["scenarios"] if x["id"] != scenario],
            monthly_context=[ApsimMonth(**m) for m in d["monthly_context_as_is"]],
            sanity_check_founder_period=d["sanity_check_founder_period"])
        comparison, reason = None, None
        if compare:
            try:
                q = _ComparisonQuery(sp["lat"], sp["lon"])
                series, keep, pu = await _balance(request, q, response)
                ts: ThresholdSet = request.app.state.thresholds
                days = []
                for r in keep:
                    c = ts.classify(r.deficit_fraction)
                    days.append(BalanceDay(date=r.date, kind=kind(r), rain_mm=r.rain_mm, et0_mm=r.et0_mm,
                                           deficit_mm=round(r.deficit_mm, 2), deficit_fraction=round(r.deficit_fraction, 4),
                                           stress_ratio=round(r.stress_ratio, 4), category=c.name, label=c.label))
                fdays = [x for x in days if x.kind == "forecast"][:7]
                rank = {n: i for i, n in enumerate(ts.order)}
                peak = max(fdays, key=lambda x: (rank[x.category], x.deficit_fraction)).category if fdays else ts.order[0]
                eng = request.app.state.engine.info
                comparison = BalanceBlock(
                    source="simple-water-balance", engine=f"{eng.name} {eng.version}",
                    location=f"{series.latitude}, {series.longitude}",
                    parameters=f"capacity {pu.root_zone_capacity_mm} mm, Kc {pu.kc}, p {pu.depletion_fraction_p}, spin-up {pu.spinup_days} days (provisional literature defaults)",
                    peak_category=peak, daily=days, weather_stale=bool(series.extra.get("stale")),
                    note="FAO-56 style single bucket on Open-Meteo weather: a different model, weather source and soil "
                         "from APSIM. Its class is based on soil deficit, APSIM's on its water-stress index; compare trends, not classes.")
            except HTTPException as e:
                reason = f"weather provider unavailable ({e.status_code}); comparison skipped"
        else:
            reason = "compare=false"
        return ApsimOutlookResponse(meta=meta, apsim=apsim, comparison=comparison, comparison_unavailable_reason=reason)

    @app.get("/v1/apsim-outlook.csv", tags=["v1"], dependencies=[Depends(guard)],
             summary="APSIM-X daily series as CSV (one demo point only)",
             description="The daily APSIM-X time series of `/v1/apsim-outlook` (selected `scenario`: last 14 days plus "
                         "forecast) as a CSV file. Every row has `source = apsimx` and `source_version` (the exact "
                         "APSIM version); the file starts with `#` comment lines with the run time, assumed palm age, "
                         "weather inputs, provisional class cut-offs, **INDICATIVE, NOT VALIDATED** and the "
                         "limitations. Only Mambang Muda (lat 2.6235, lon 99.6922) has APSIM data: other points "
                         "return 404. Results are precomputed offline. Same auth and rate limit as the other endpoints. "
                         "Simple water balance rows are not mixed in: use `/v1/outlook.csv`.",
             responses={200: {"description": "CSV with comment header lines.",
                              "content": {"text/csv": {"example": ex.APSIM_CSV}}},
                        404: {"model": s.ErrorResponse, "description": "Requested point is not the APSIM demo point."},
                        **ERROR_RESPONSES})
    async def apsim_outlook_csv(request: Request, response: Response,
                                lat: float | None = Query(None, ge=-90, le=90, description="Optional. If given it must be within 0.05 deg of the supported point, else 404", examples=[2.6235]),
                                lon: float | None = Query(None, ge=-180, le=180, description="Optional, see lat", examples=[99.6922]),
                                scenario: str = Query("as_is", description="Scenario id: as_is (default), older_palms_2008, younger_palms_2016, rain_scaled_to_station")):
        res = _load_apsim(request, lat, lon)
        try:
            sc = res.scenario(scenario)
        except KeyError:
            raise HTTPException(422, f"unknown scenario; choose one of {', '.join(res.scenario_ids)}")
        return _csv_response(csv_export.apsim_csv(res.data, sc, res.age_hours()), "climsmart-apsim-outlook.csv", response)

    def _cost_benefit(request: Request, lat, lon, weather, p) -> dict:
        res = _load_apsim(request, lat, lon)
        if "management_scenarios" not in res.data:
            raise HTTPException(503, "this APSIM result file has no management scenarios: re-run scripts/run_apsim.py")
        if weather not in res.data["management_scenarios"]["variants"]:
            raise HTTPException(422, f"unknown weather; choose one of {', '.join(res.data['management_scenarios']['variants'])}")
        sup = dict(p)
        if sup.pop("fert_price_per_kg", None) is not None:
            fp, nf = p["fert_price_per_kg"], p["fert_n_fraction"]
            if p["n_price_per_kg"] is not None:
                raise HTTPException(422, "give either n_price_per_kg or fert_price_per_kg with fert_n_fraction, not both")
            if nf is None:
                raise HTTPException(422, "fert_price_per_kg needs fert_n_fraction (N content of the product, e.g. 0.46 for urea)")
            sup["n_price_per_kg"] = fp / nf
        elif p["fert_n_fraction"] is not None:
            raise HTTPException(422, "fert_n_fraction is only used together with fert_price_per_kg")
        sup.pop("fert_n_fraction", None)
        sens, wif, cur = sup.pop("sensitivity_pct"), sup.pop("yield_whatif_pct"), sup.pop("currency")
        return cb.build_response(res.data, res.age_hours(), sup, weather, sens, wif, cur,
                                 datetime.now(timezone.utc).isoformat(timespec="seconds"), STALE_AFTER_HOURS)

    CB_DESC = ("Cost-benefit view of APSIM-X management scenarios for **Mambang Muda only** (other points: 404). "
               "APSIM-X was run offline for: the founder's N fertiliser (0.5x, 1x, 1.5x), the same N in 6 doses instead of 3, "
               "and a rule-based irrigation top-up (the founder's file has an Irrigation model but nothing applies water; "
               "we add a simple trigger rule). The management is applied from 2023 and the numbers are the mean of the "
               "**2023-2025 hindcast years** (annual FFB needs full years; weather is Open-Meteo archive, not a forecast). "
               "**Prices are YOUR inputs**: anything you do not send is an `example, replace with your costs` placeholder and is flagged "
               "`is_example_default: true`. For each scenario versus base the response gives delta FFB (t/ha/yr), delta revenue, delta "
               "fertiliser cost, delta water cost, net difference, a +/-`sensitivity_pct` price sensitivity, a yield-response what-if, break-even "
               "FFB price and break-even yield factor, and the change in N leaching (APSIM output, no money value). All formulas are in "
               "`formulas`. `weather=rain_scaled` re-reads the same scenarios with rain scaled to the station. "
               "**Indicative, NOT VALIDATED: no Sumatra calibration, palm planting year ASSUMED 2012, one point. "
               "No savings, profit or ROI claim is made.** Leaching prevention: the model has no dedicated practice; leaching is reported as an output "
               "and only N rate/splitting change it.")
    CB_QUERY = dict(
        lat=Query(None, ge=-90, le=90, description="Optional. If given it must be within 0.05 deg of the supported point, else 404", examples=[2.6235]),
        lon=Query(None, ge=-180, le=180, description="Optional, see lat", examples=[99.6922]),
    )

    def _cb_params(ffb_price_per_tonne, n_price_per_kg, fert_price_per_kg, fert_n_fraction, water_price_per_m3,
                   irrigation_fixed_cost_per_ha_year, harvest_haul_cost_per_tonne, sensitivity_pct, yield_whatif_pct, currency):
        return dict(ffb_price_per_tonne=ffb_price_per_tonne, n_price_per_kg=n_price_per_kg, fert_price_per_kg=fert_price_per_kg,
                    fert_n_fraction=fert_n_fraction, water_price_per_m3=water_price_per_m3,
                    irrigation_fixed_cost_per_ha_year=irrigation_fixed_cost_per_ha_year,
                    harvest_haul_cost_per_tonne=harvest_haul_cost_per_tonne, sensitivity_pct=sensitivity_pct,
                    yield_whatif_pct=yield_whatif_pct, currency=currency)

    def _cb_dep(
        ffb_price_per_tonne: float | None = Query(None, ge=0, description="Your FFB price per tonne. Omitted = example placeholder 100", examples=[100.0]),
        n_price_per_kg: float | None = Query(None, ge=0, description="Your fertiliser cost per kg of N. Omitted = example placeholder 1.0", examples=[1.0]),
        fert_price_per_kg: float | None = Query(None, gt=0, description="Alternative to n_price_per_kg: product price per kg (needs fert_n_fraction)"),
        fert_n_fraction: float | None = Query(None, gt=0, le=1, description="N content of the product, e.g. 0.46 for urea (used with fert_price_per_kg)"),
        water_price_per_m3: float | None = Query(None, ge=0, description="Your irrigation water cost per m3 (1 mm/ha = 10 m3). Omitted = example placeholder 0.1", examples=[0.1]),
        irrigation_fixed_cost_per_ha_year: float | None = Query(None, ge=0, description="Irrigation operating cost per ha per year, charged only to scenarios that irrigate. Omitted = 0 (not counted)", examples=[0.0]),
        harvest_haul_cost_per_tonne: float | None = Query(None, ge=0, description="Extra harvest/transport cost per extra tonne of FFB. Omitted = 0 (not counted)", examples=[0.0]),
        sensitivity_pct: float = Query(20.0, ge=0, le=90, description="Price sensitivity: FFB price and cost prices moved by +/- this percent"),
        yield_whatif_pct: float = Query(50.0, ge=0, le=100, description="Yield what-if: APSIM-X's yield change scaled by (1 - w), 1, (1 + w). Illustration, not an error estimate"),
        currency: str = Query("currency units", max_length=16, description="Label only, no conversion is done"),
    ):
        return _cb_params(ffb_price_per_tonne, n_price_per_kg, fert_price_per_kg, fert_n_fraction, water_price_per_m3,
                          irrigation_fixed_cost_per_ha_year, harvest_haul_cost_per_tonne, sensitivity_pct, yield_whatif_pct, currency)

    @app.get("/v1/apsim-cost-benefit", response_model=CostBenefitResponse, tags=["v1"], dependencies=[Depends(guard)],
             summary="Cost-benefit view of APSIM-X management scenarios (one demo point only)", description=CB_DESC,
             responses={200: {"description": "Scenario comparison with your prices.", "headers": OK_HEADERS,
                              "content": {"application/json": {"example": ex.COST_BENEFIT}}},
                        404: {"model": s.ErrorResponse, "description": "Requested point is not the APSIM demo point."},
                        **ERROR_RESPONSES})
    async def apsim_cost_benefit(request: Request, p: dict = Depends(_cb_dep),
                                 lat: float | None = CB_QUERY["lat"], lon: float | None = CB_QUERY["lon"],
                                 weather: str = Query("as_delivered", description="APSIM weather variant: as_delivered (default) or rain_scaled")):
        return CostBenefitResponse(**_cost_benefit(request, lat, lon, weather, p))

    @app.get("/v1/apsim-cost-benefit.csv", tags=["v1"], dependencies=[Depends(guard)],
             summary="Cost-benefit view as CSV (one row per scenario)",
             description="The same numbers as `/v1/apsim-cost-benefit` (same query parameters), one row per alternative scenario, "
                         "with `source = apsimx` and `source_version` columns and `#` comment lines that list every input "
                         "(marking example placeholders), the assumed palm age, the hindcast years and the limitations. "
                         "Indicative, NOT VALIDATED; no savings or ROI claim.",
             responses={200: {"description": "CSV with comment header lines.", "content": {"text/csv": {"example": ex.COST_BENEFIT_CSV}}},
                        404: {"model": s.ErrorResponse, "description": "Requested point is not the APSIM demo point."},
                        **ERROR_RESPONSES})
    async def apsim_cost_benefit_csv(request: Request, response: Response, p: dict = Depends(_cb_dep),
                                     lat: float | None = CB_QUERY["lat"], lon: float | None = CB_QUERY["lon"],
                                     weather: str = Query("as_delivered", description="APSIM weather variant: as_delivered (default) or rain_scaled")):
        body = CostBenefitResponse(**_cost_benefit(request, lat, lon, weather, p)).model_dump(mode="json")
        return _csv_response(csv_export.costbenefit_csv(body), "climsmart-apsim-cost-benefit.csv", response)

    # ---- /v1/apsim-series: saved APSIM-X .db series for plotting (read-only, no run)
    @app.get("/v1/apsim-series", response_model=ApsimSeriesResponse, tags=["v1"], dependencies=[Depends(guard)],
             summary="Saved APSIM-X series for plotting: modelled FFB yield per year per scenario, LAI, soil water, N",
             description=("Series read from the saved APSIM-X output databases (`.db`) of the last offline run: **modelled FFB yield (t/ha/yr)** "
                          "per scenario (base/BAU vs management and palm-age scenarios), plus LAI, soil water, soil nitrate, N applied/leached and a "
                          "stem-mass biomass proxy. **INDICATIVE, NOT VALIDATED**: the oil-palm model is not calibrated for this site and the planting year (2012) is "
                          "assumed; yield is a modelled comparison between scenarios, not a harvest forecast. Saved data: it does not update by itself "
                          "(`meta.age_hours`, `meta.stale`). Weather: founder station history, then Open-Meteo hindcast/forecast (see `meta.weather`)."),
             responses={200: {"description": "Selected scenarios with annual and monthly series.", "content": {"application/json": {"example": ex.APSIM_SERIES}}},
                        503: {"model": s.ErrorResponse, "description": "The saved APSIM series file is not on this server."},
                        **{k: v for k, v in ERROR_RESPONSES.items() if k != 503}})
    async def apsim_series(
            request: Request,
            scenario: list[str] | None = Query(None, description="Scenario id(s) to return (repeat the parameter). Omitted = all. Ids: as_is, older_palms_2008, younger_palms_2016, as_delivered__base, as_delivered__n_0_5x, as_delivered__n_1_5x, as_delivered__n_split_6x, as_delivered__irrigate_fw_lt_0_9, as_delivered__irrigate_fw_lt_0_8 and the same with the rain_scaled__ prefix"),
            group: str | None = Query(None, pattern="^(planting_year|management_as_delivered|management_rain_scaled)$", description="Only scenarios of this group"),
            annual: bool = Query(True, description="Include the annual series (FFB t/ha per year first)"),
            monthly: bool = Query(True, description="Include the monthly series (LAI, soil water, nitrate, stem mass)"),
            from_year: int | None = Query(None, ge=1990, le=2100, description="Drop annual/monthly points before this year")):
        try:
            data = load_series(request.app.state.settings.apsim_series_file)
        except SeriesUnavailable:
            raise HTTPException(503, "APSIM series have not been generated on this server yet")
        known = [x["id"] for x in data["scenarios"]]
        bad = [x for x in (scenario or []) if x not in known]
        if bad:
            raise HTTPException(422, f"unknown scenario {bad}; known: {known}")
        chosen = [x for x in data["scenarios"] if (not scenario or x["id"] in scenario) and (not group or x["group"] == group)]

        def cut(tbl: dict, key: str) -> dict:
            if from_year is None:
                return tbl
            keep = [i for i, v in enumerate(tbl[key]) if int(str(v)[:4]) >= from_year]
            return {k: [v[i] for i in keep] for k, v in tbl.items()}

        out = []
        for x in chosen:
            y = {k: v for k, v in x.items() if k not in ("annual", "monthly")}
            if annual:
                y["annual"] = cut(x["annual"], "year")
            if monthly:
                y["monthly"] = cut(x["monthly"], "month")
            out.append(y)
        age = series_age_hours(data)
        e, sp = data["engine"], data["supported_point"]
        return ApsimSeriesResponse(
            meta={"source": "apsimx", "status": data["status"], "engine": f"{e['name']} {e['version']}",
                  "run_started_utc": data["run"]["started_utc"], "age_hours": age, "stale": age > STALE_AFTER_HOURS,
                  "point": sp["name"], "lat": sp["lat"], "lon": sp["lon"], "weather": data["weather"],
                  "assumptions": data["assumptions"], "limitations": data["limitations"]},
            units={"annual": data["units"]["annual"], "monthly": data["units"]["monthly"], "notes": data["unit_notes"]},
            soil=data.get("soil"), scenarios=out)

    # ---- /v1/fertiliser-timing: the founder's avoided-loss method (risk of loss, assumptions are editable)
    FT_DESC = ("Value at risk if you fertilise on a date, from the daily rain in D+1..D+4 after that date, and the safer day. "
               "Fertiliser value = palms x kg per palm x price per kg. A rain class (very high, high, dry, relatively safe) "
               "gives a BAU risk factor and a residual factor with the daily forecast; the benefit is value x (BAU - residual), "
               "a **risk of loss that could be avoided, never a proven loss**. **Every percentage is a founder assumption** "
               "(source label 'founder assumption'), editable per request. `mode=forecast` uses Open-Meteo daily rain "
               "(free tier non-commercial); `mode=hindcast` uses the founder's recorded 2024-2025 daily-rain file (needs "
               "`application_date`). Inputs you omit are the founder's worked-example numbers and are flagged as examples.")
    FT_PCT = "Override the founder's assumed {} factor for class '{}' (percent of fertiliser value, 0..100)"

    def _ft_overrides(q: dict) -> dict:
        out: dict = {}
        for cid in ("very_high", "high", "dry", "safe"):
            for k in ("bau", "residual"):
                v = q.get(f"{cid}_{k}_pct")
                if v is not None:
                    out.setdefault(cid, {})[f"{k}_pct"] = ftm.D(v)
        return out

    @app.get("/v1/fertiliser-timing", response_model=FertiliserTimingResponse, tags=["v1"], dependencies=[Depends(guard)],
             summary="Fertiliser timing: value at risk (assumption-based) on a date vs the safer day", description=FT_DESC,
             responses={200: {"description": "Risk of loss on the planned date and on the suggested day.", "headers": OK_HEADERS,
                              "content": {"application/json": {"example": ex.FERTILISER_TIMING}}},
                        503: {"model": s.ErrorResponse, "description": "Weather provider rate-limiting us, or hindcast file not on this server."},
                        **{k: v for k, v in ERROR_RESPONSES.items() if k != 503}})
    async def fertiliser_timing(
            request: Request, response: Response,
            palms: int | None = Query(None, ge=1, le=100000, description="Palms in the block. Omitted = founder's example 3900", examples=[3900]),
            kg_per_palm: float | None = Query(None, gt=0, le=50, description="Fertiliser per palm, kg. Omitted = founder's example 2.5", examples=[2.5]),
            price_per_kg: float | None = Query(None, gt=0, le=10_000_000, description="Fertiliser price, Rp per kg. Omitted = founder's example 14500", examples=[14500]),
            area_ha: float | None = Query(None, gt=0, le=100000, description="Block area, ha. Omitted = founder's example 30", examples=[30]),
            application_date: date | None = Query(None, description="Application date D (the last day of the application), YYYY-MM-DD. Forecast mode default = today; hindcast mode: required"),
            mode: str = Query("forecast", pattern="^(forecast|hindcast)$", description="forecast (Open-Meteo) or hindcast (founder's 2024-2025 daily rain file)"),
            lat: float = Query(2.6235, ge=-90, le=90, description="Latitude for forecast mode (default Mambang Muda demo point)", examples=[2.6235]),
            lon: float = Query(99.6922, ge=-180, le=180, description="Longitude for forecast mode", examples=[99.6922]),
            scale_ha: float | None = Query(None, gt=0, le=10_000_000, description="What-if: scale the per-hectare figure to this area, ha. Omitted = the block area"),
            very_high_bau_pct: float | None = Query(None, ge=0, le=100, description=FT_PCT.format("BAU", "very_high")),
            very_high_residual_pct: float | None = Query(None, ge=0, le=100, description=FT_PCT.format("residual", "very_high")),
            high_bau_pct: float | None = Query(None, ge=0, le=100, description=FT_PCT.format("BAU", "high")),
            high_residual_pct: float | None = Query(None, ge=0, le=100, description=FT_PCT.format("residual", "high")),
            dry_bau_pct: float | None = Query(None, ge=0, le=100, description=FT_PCT.format("BAU", "dry")),
            dry_residual_pct: float | None = Query(None, ge=0, le=100, description=FT_PCT.format("residual", "dry")),
            safe_bau_pct: float | None = Query(None, ge=0, le=100, description=FT_PCT.format("BAU", "safe")),
            safe_residual_pct: float | None = Query(None, ge=0, le=100, description=FT_PCT.format("residual", "safe"))):
        defaults = {"palms": 3900, "kg_per_palm": 2.5, "price_per_kg": 14500, "area_ha": 30}
        given = {"palms": palms, "kg_per_palm": kg_per_palm, "price_per_kg": price_per_kg, "area_ha": area_ha}
        used = {k: (v if v is not None else defaults[k]) for k, v in given.items()}
        example = {k for k, v in given.items() if v is None}
        try:
            a = ftm.with_overrides(request.app.state.ft_assumptions, _ft_overrides(locals()))
        except ftm.TimingError as e:
            raise HTTPException(422, str(e))
        kinds: dict[date, str] = {}
        if mode == "forecast":
            past_days, fc_days = 7, 14  # 14 not 16: Open-Meteo sometimes returns null for the 16th day, which the provider rejects as a gap
            series_w = await _weather(request, lat, lon, past_days, fc_days, response)
            series = {d.date: ftm.D(d.rain_mm) for d in series_w.days}
            kinds = {d.date: ("forecast" if d.is_forecast else "past") for d in series_w.days}
            today = next((d.date for d in series_w.days if d.is_forecast), None)
            planned = application_date or today
            rain_source = f"{series_w.source_name} daily precipitation (past days and forecast)"
            note = f"{series_w.source_attribution}. {series_w.source_license_note}"
        else:
            if application_date is None:
                raise HTTPException(422, "application_date is required in hindcast mode")
            f = request.app.state.settings.hindcast_rain_file or HINDCAST_DEFAULT
            try:
                series = ftm.load_daily_rain_csv(f)
            except (OSError, ValueError, KeyError):
                raise HTTPException(503, "The hindcast daily-rain file is not available on this server")
            kinds = {d: "recorded" for d in series}
            today, planned = None, application_date
            rain_source = f"Founder's recorded daily rain {min(series).isoformat()} to {max(series).isoformat()} (station not stated)"
            note = "Recorded daily rainfall supplied by the founder; station and units as in his workbook (mm per day)."
        try:
            body = ftb.build(a=a, mode=mode, area_ha=used["area_ha"], palms=used["palms"], kg_per_palm=used["kg_per_palm"],
                             price_per_kg=used["price_per_kg"], planned=planned, series=series, kinds=kinds, today=today,
                             scale_ha=scale_ha, example_defaults=example, rain_source=rain_source, rain_source_note=note)
        except ftm.TimingError as e:
            raise HTTPException(422, str(e))
        if mode == "forecast":
            wsrc, wnote = weather_label(series_w)
            stale = bool(series_w.extra.get("stale"))
            body["meta"].update(weather_fetched_at=series_w.fetched_at, weather_stale=stale,
                                weather_age_seconds=series_w.extra.get("age_seconds") if stale else None,
                                weather_source=wsrc, weather_note=wnote, weather_timezone=series_w.timezone)
        return FertiliserTimingResponse(**body)

    @app.get("/v1/fertiliser-timing/availability", response_model=FertiliserAvailability, tags=["v1"], dependencies=[Depends(guard)],
             summary="Which fertiliser-timing modes this server can offer (cheap, no weather call)",
             description=("Tells a client whether `mode=hindcast` (the founder's recorded daily-rain file) is available on this server, "
                          "so the app can hide that option instead of showing an error. Always answers 200: `hindcast.available` is false when "
                          "the file is missing or unreadable. It does not call the weather provider."),
             responses={200: {"description": "Availability of the modes."}, **{k: v for k, v in ERROR_RESPONSES.items() if k in (401, 429)}})
    async def fertiliser_availability(request: Request):
        f = request.app.state.settings.hindcast_rain_file or HINDCAST_DEFAULT
        try:
            rain = ftm.load_daily_rain_csv(f)
            h = {"available": True, "start": min(rain).isoformat(), "end": max(rain).isoformat(), "days": len(rain)}
        except (OSError, ValueError, KeyError, ftm.TimingError):
            h = {"available": False}
        return FertiliserAvailability(forecast=True, hindcast=h)

    return app


def get_app() -> FastAPI:  # for `uvicorn climsmart.api.main:get_app --factory`
    settings = Settings.from_env()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    for noisy in ("httpx", "httpcore"):  # their INFO lines contain the requested coordinates
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return create_app(settings=settings)
