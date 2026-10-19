"""Request schemas (Pydantic v2). Responses are documented dicts built in app/services/serialize.py."""
from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlantationIn(_In):
    name: str = Field(min_length=1, max_length=200, examples=["Kebun Mambang Muda"])
    code: str | None = Field(None, max_length=64)
    organization: str | None = Field(None, max_length=200)
    description: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class PlantationPatch(_In):
    name: str | None = Field(None, min_length=1, max_length=200)
    code: str | None = Field(None, max_length=64)
    organization: str | None = None
    description: str | None = None
    attributes: dict[str, Any] | None = None


class PlotIn(_In):
    name: str = Field(min_length=1, max_length=200, examples=["Blok A1"])
    code: str | None = Field(None, max_length=64)
    crop: str = "oil_palm"
    planting_year: int | None = Field(None, ge=1900, le=2100)
    geometry: dict[str, Any] = Field(description="GeoJSON Polygon/MultiPolygon (or a Feature) in WGS84 lon/lat")
    attributes: dict[str, Any] = Field(default_factory=dict)


class PlotPatch(_In):
    name: str | None = Field(None, min_length=1, max_length=200)
    code: str | None = None
    planting_year: int | None = Field(None, ge=1900, le=2100)
    geometry: dict[str, Any] | None = None
    attributes: dict[str, Any] | None = None


class PlotImport(_In):
    geojson: dict[str, Any] = Field(description="FeatureCollection; each feature becomes a plot "
                                                "(name from properties.name, else 'Plot N')")


class MetRequest(_In):
    start_date: date
    end_date: date
    strategy: Literal["model_only", "sensor_assimilation"] = "model_only"
    name: str | None = None


class ConfigIn(_In):
    template_id: str
    met_file_id: str
    name: str | None = Field(None, max_length=200)
    params: dict[str, Any] = Field(default_factory=dict, description="Only the template's whitelisted parameters")


class RunIn(_In):
    config_id: str
    label: str | None = Field(None, max_length=200)
