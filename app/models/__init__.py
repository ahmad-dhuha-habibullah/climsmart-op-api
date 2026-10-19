"""ORM entities. Geometry is stored as GeoJSON (JSON) plus computed bbox/centroid/area columns so the schema is
portable (SQLite for dev/tests, PostgreSQL in production). PostGIS geometry columns are a later upgrade."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.core.util import utcnow

JSONType = JSON().with_variant(JSONB(), "postgresql")


def _ts():
    return mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Plantation(Base):
    __tablename__ = "plantations"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    code: Mapped[str | None] = mapped_column(String(64), unique=True)
    organization: Mapped[str | None] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    attributes: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()
    plots: Mapped[list[Plot]] = relationship(back_populates="plantation", cascade="all, delete-orphan")


class Plot(Base):
    __tablename__ = "plots"
    __table_args__ = (UniqueConstraint("plantation_id", "name", name="uq_plot_name_per_plantation"),
                      Index("ix_plots_bbox", "bbox_min_lon", "bbox_min_lat", "bbox_max_lon", "bbox_max_lat"))
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    plantation_id: Mapped[str] = mapped_column(ForeignKey("plantations.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    code: Mapped[str | None] = mapped_column(String(64))
    crop: Mapped[str] = mapped_column(String(64), default="oil_palm", nullable=False)
    planting_year: Mapped[int | None] = mapped_column(Integer)
    geometry: Mapped[dict] = mapped_column(JSONType, nullable=False)  # GeoJSON Polygon/MultiPolygon, WGS84
    area_ha: Mapped[float] = mapped_column(Float, nullable=False)
    centroid_lat: Mapped[float] = mapped_column(Float, nullable=False)
    centroid_lon: Mapped[float] = mapped_column(Float, nullable=False)
    bbox_min_lon: Mapped[float] = mapped_column(Float, nullable=False)
    bbox_min_lat: Mapped[float] = mapped_column(Float, nullable=False)
    bbox_max_lon: Mapped[float] = mapped_column(Float, nullable=False)
    bbox_max_lat: Mapped[float] = mapped_column(Float, nullable=False)
    attributes: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()
    plantation: Mapped[Plantation] = relationship(back_populates="plots")


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_status_created", "status", "created_at"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)  # met.generate | apsim.run
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    params: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    result: Mapped[dict | None] = mapped_column(JSONType)
    error: Mapped[dict | None] = mapped_column(JSONType)
    logs: Mapped[str] = mapped_column(Text, default="", nullable=False)
    plot_id: Mapped[str | None] = mapped_column(ForeignKey("plots.id", ondelete="SET NULL"), index=True)
    resource_type: Mapped[str | None] = mapped_column(String(32))
    resource_id: Mapped[str | None] = mapped_column(String(40), index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    retry_of: Mapped[str | None] = mapped_column(String(40))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    claimed_by: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = _ts()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Artifact(Base):
    __tablename__ = "artifacts"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)  # met|apsimx|apsim_db|apsim_log|template
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(100), nullable=False)
    storage_backend: Mapped[str] = mapped_column(String(16), nullable=False, default="local")
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    plot_id: Mapped[str | None] = mapped_column(ForeignKey("plots.id", ondelete="SET NULL"), index=True)
    job_id: Mapped[str | None] = mapped_column(String(40))
    provenance: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    created_at: Mapped[datetime] = _ts()


class MetFile(Base):
    __tablename__ = "met_files"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    plot_id: Mapped[str] = mapped_column(ForeignKey("plots.id", ondelete="CASCADE"), index=True)
    name: Mapped[str | None] = mapped_column(String(200))
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    strategy: Mapped[str] = mapped_column(String(32), nullable=False)  # model_only | sensor_assimilation
    strategy_params: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")  # pending|valid|invalid|failed
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    tav: Mapped[float | None] = mapped_column(Float)
    amp: Mapped[float | None] = mapped_column(Float)
    validation: Mapped[dict | None] = mapped_column(JSONType)
    sources: Mapped[dict | None] = mapped_column(JSONType)
    artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.id", ondelete="SET NULL"))
    job_id: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = _ts()


class ApsimTemplate(Base):
    __tablename__ = "apsim_templates"
    __table_args__ = (UniqueConstraint("slug", "version", name="uq_template_slug_version"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    crop: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.id"), nullable=False)
    apsimx_version: Mapped[int | None] = mapped_column(Integer)
    parameters: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)  # editable-param schema+defaults
    source: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    created_at: Mapped[datetime] = _ts()


class ApsimConfig(Base):
    __tablename__ = "apsim_configs"
    __table_args__ = (UniqueConstraint("plot_id", "name", "version", name="uq_config_plot_name_version"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    plot_id: Mapped[str] = mapped_column(ForeignKey("plots.id", ondelete="CASCADE"), index=True)
    template_id: Mapped[str] = mapped_column(ForeignKey("apsim_templates.id"), nullable=False)
    met_file_id: Mapped[str] = mapped_column(ForeignKey("met_files.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    params: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # generated | invalid
    validation: Mapped[dict | None] = mapped_column(JSONType)
    edits: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    apsimx_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _ts()


class ApsimRun(Base):
    __tablename__ = "apsim_runs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    config_id: Mapped[str] = mapped_column(ForeignKey("apsim_configs.id", ondelete="CASCADE"), index=True)
    plot_id: Mapped[str] = mapped_column(ForeignKey("plots.id", ondelete="CASCADE"), index=True)
    label: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    job_id: Mapped[str | None] = mapped_column(String(40))
    runtime: Mapped[dict | None] = mapped_column(JSONType)  # how APSIM was invoked + version
    runtime_seconds: Mapped[float | None] = mapped_column(Float)
    db_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.id", ondelete="SET NULL"))
    log_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.id", ondelete="SET NULL"))
    provenance: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    error: Mapped[dict | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = _ts()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RunOutput(Base):
    __tablename__ = "run_outputs"
    __table_args__ = (UniqueConstraint("run_id", "table_name", name="uq_output_run_table"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("apsim_runs.id", ondelete="CASCADE"), index=True)
    table_name: Mapped[str] = mapped_column(String(128), nullable=False)
    variables: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)  # [{name, unit, dtype}]
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    first_date: Mapped[date | None] = mapped_column(Date)
    last_date: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = _ts()
