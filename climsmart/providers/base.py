"""Weather provider interface. Implement this to swap Open-Meteo for another source."""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import date


class ProviderError(Exception):
    """The upstream weather source failed or returned unusable data.

    kind: "rate_limited" (upstream 429; API answers 503), "timeout", "upstream" (5xx/transport/4xx),
    "data" (unusable payload). retry_after: seconds the upstream asked us to wait, if known.
    """

    def __init__(self, message: str, kind: str = "upstream", status: int | None = None,
                 retry_after: int | None = None, reason: str | None = None):
        super().__init__(message)
        self.kind, self.status, self.retry_after = kind, status, retry_after
        self.reason = reason  # upstream's own explanation (e.g. Open-Meteo's 429 JSON "reason"), for logs only


@dataclass(frozen=True)
class DailyWeather:
    date: date
    is_forecast: bool  # False = recent past (model analysis), True = forecast
    rain_mm: float
    et0_mm: float  # FAO-56 reference evapotranspiration
    temp_max_c: float | None = None
    temp_min_c: float | None = None
    rain_probability_pct: float | None = None  # forecast days only, if the source has it


@dataclass(frozen=True)
class WeatherSeries:
    latitude: float
    longitude: float
    elevation_m: float | None
    timezone: str
    days: list[DailyWeather]
    source_name: str
    source_url: str
    source_attribution: str
    source_license_note: str
    fetched_at: str  # ISO timestamp (UTC)
    extra: dict = field(default_factory=dict)


class WeatherProvider(abc.ABC):
    """Returns consecutive daily weather: `past_days` before today + `forecast_days` from today."""

    name: str = "provider"

    @abc.abstractmethod
    async def get_daily(
        self, lat: float, lon: float, past_days: int, forecast_days: int
    ) -> WeatherSeries: ...
