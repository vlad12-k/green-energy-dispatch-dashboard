"""SQLAlchemy 2.x persistence schema."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class RawPayload(Base):
    __tablename__ = "raw_payloads"
    __table_args__ = (
        CheckConstraint("region_id > 0", name="ck_raw_payload_region_positive"),
        CheckConstraint("length(payload_sha256) = 64", name="ck_raw_payload_hash_length"),
        CheckConstraint("schema_version > 0", name="ck_raw_payload_schema_version"),
        UniqueConstraint(
            "region_id", "source_api", "payload_sha256", "retrieved_at",
            name="uq_raw_payload_identity",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    region_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    source_api: Mapped[str] = mapped_column(String(120), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    payload_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    validation_fresh: Mapped[bool] = mapped_column(Boolean, nullable=False)
    validation_error: Mapped[str | None] = mapped_column(Text)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    observations: Mapped[list[CarbonObservation]] = relationship(back_populates="raw_payload")


class CarbonObservation(Base):
    __tablename__ = "carbon_observations"
    __table_args__ = (
        CheckConstraint("region_id > 0", name="ck_carbon_observation_region_positive"),
        CheckConstraint("intensity_gco2_per_kwh >= 0 AND intensity_gco2_per_kwh < 'Infinity'::float8", name="ck_carbon_observation_intensity_nonnegative_finite"),
        CheckConstraint("valid_until > observed_at", name="ck_carbon_observation_interval_order"),
        CheckConstraint("measurement_type IN ('actual', 'forecast')", name="ck_carbon_observation_measurement_type"),
        CheckConstraint("interval_status IN ('upcoming', 'in_progress', 'completed')", name="ck_carbon_observation_interval_status"),
        CheckConstraint("interval_lag_seconds >= 0", name="ck_carbon_observation_lag_nonnegative"),
        CheckConstraint("interval_lag_seconds < 'Infinity'::float8", name="ck_carbon_observation_lag_finite"),
        CheckConstraint("age_seconds >= 0 AND age_seconds < 'Infinity'::float8", name="ck_carbon_observation_age_nonnegative_finite"),
        UniqueConstraint("raw_payload_id", "region_id", "observed_at", "valid_until", "measurement_type", name="uq_carbon_observation_identity"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    raw_payload_id: Mapped[int] = mapped_column(ForeignKey("raw_payloads.id", ondelete="RESTRICT"), nullable=False, index=True)
    region_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    region_name: Mapped[str] = mapped_column(String(160), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    measurement_type: Mapped[str] = mapped_column(String(16), nullable=False)
    intensity_gco2_per_kwh: Mapped[float] = mapped_column(Float, nullable=False)
    intensity_index: Mapped[str] = mapped_column(String(32), nullable=False)
    interval_status: Mapped[str] = mapped_column(String(16), nullable=False)
    interval_lag_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    is_fresh: Mapped[bool] = mapped_column(Boolean, nullable=False)
    age_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    source_api: Mapped[str] = mapped_column(String(120), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    raw_payload: Mapped[RawPayload] = relationship(back_populates="observations")


class NesoCollectorRun(Base):
    """Durable outcome and quality summary for one scheduled collection cycle."""

    __tablename__ = "neso_collector_runs"
    __table_args__ = (
        CheckConstraint("region_id > 0", name="ck_neso_collector_run_region_positive"),
        CheckConstraint("status IN ('running', 'succeeded', 'failed')", name="ck_neso_collector_run_status"),
        CheckConstraint("attempt_count >= 0", name="ck_neso_collector_run_attempt_count"),
        CheckConstraint("consecutive_failures >= 0", name="ck_neso_collector_run_failure_count"),
        CheckConstraint("missing_interval_count >= 0", name="ck_neso_collector_run_gap_count"),
        CheckConstraint("measurement_type IS NULL OR measurement_type IN ('actual', 'forecast')", name="ck_neso_collector_run_measurement_type"),
        CheckConstraint("duration_seconds IS NULL OR duration_seconds >= 0", name="ck_neso_collector_run_duration"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    region_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'running'"))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    error_category: Mapped[str | None] = mapped_column(String(24))
    error_message: Mapped[str | None] = mapped_column(Text)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    payload_sha256: Mapped[str | None] = mapped_column(String(64))
    source_retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    interval_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    interval_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    measurement_type: Mapped[str | None] = mapped_column(String(16))
    intensity_gco2_per_kwh: Mapped[float | None] = mapped_column(Float)
    source_fresh: Mapped[bool | None] = mapped_column(Boolean)
    raw_evidence_inserted: Mapped[bool | None] = mapped_column(Boolean)
    observation_inserted: Mapped[bool | None] = mapped_column(Boolean)
    duplicate_interval: Mapped[bool | None] = mapped_column(Boolean)
    missing_interval_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    gap_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    gap_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    attempts: Mapped[list[NesoCollectorAttempt]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class NesoCollectorAttempt(Base):
    """One separately tracked HTTP attempt, including bounded retry metadata."""

    __tablename__ = "neso_collector_attempts"
    __table_args__ = (
        CheckConstraint("attempt_number > 0", name="ck_neso_collector_attempt_number"),
        CheckConstraint(
            "outcome IN ('in_progress', 'success', 'duplicate', 'source_failure', 'validation_failure', 'persistence_failure', 'internal_failure')",
            name="ck_neso_collector_attempt_outcome",
        ),
        CheckConstraint("http_status IS NULL OR http_status BETWEEN 100 AND 599", name="ck_neso_collector_attempt_http_status"),
        CheckConstraint("retry_delay_seconds IS NULL OR retry_delay_seconds >= 0", name="ck_neso_collector_attempt_retry_delay"),
        CheckConstraint("measurement_type IS NULL OR measurement_type IN ('actual', 'forecast')", name="ck_neso_collector_attempt_measurement_type"),
        UniqueConstraint("run_id", "attempt_number", name="uq_neso_collector_attempt_number"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("neso_collector_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str] = mapped_column(String(24), nullable=False, server_default=text("'in_progress'"))
    error_category: Mapped[str | None] = mapped_column(String(24))
    error_message: Mapped[str | None] = mapped_column(Text)
    retryable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    http_status: Mapped[int | None] = mapped_column(Integer)
    retry_after: Mapped[str | None] = mapped_column(String(128))
    retry_delay_seconds: Mapped[float | None] = mapped_column(Float)
    payload_sha256: Mapped[str | None] = mapped_column(String(64))
    source_retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    interval_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    interval_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    measurement_type: Mapped[str | None] = mapped_column(String(16))
    source_fresh: Mapped[bool | None] = mapped_column(Boolean)
    raw_evidence_inserted: Mapped[bool | None] = mapped_column(Boolean)
    observation_inserted: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    run: Mapped[NesoCollectorRun] = relationship(back_populates="attempts")
