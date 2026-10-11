"""Persistence for standalone NESO collector health and attempt history."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.persistence.models import NesoCollectorAttempt, NesoCollectorRun
from app.services.neso_collector import count_missing_intervals
from app.services.neso_ingestion import IngestionResult

_ATTEMPT_OUTCOMES = {
    "in_progress",
    "success",
    "duplicate",
    "source_failure",
    "validation_failure",
    "persistence_failure",
    "internal_failure",
}
_RUN_STATUSES = {"running", "succeeded", "failed"}


class NesoCollectorStateRepository:
    """Write each cycle/HTTP attempt in short independent transactions."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def begin_run(self, region_id: int, started_at: datetime) -> int:
        _require_aware(started_at, "started_at")
        if region_id <= 0:
            raise ValueError("region_id must be positive")
        with Session(self.engine) as session, session.begin():
            previous_completed = session.scalar(
                select(NesoCollectorRun)
                .where(
                    NesoCollectorRun.region_id == region_id,
                    NesoCollectorRun.status != "running",
                )
                .order_by(NesoCollectorRun.id.desc())
                .limit(1)
            )
            run = NesoCollectorRun(
                region_id=region_id,
                started_at=started_at.astimezone(UTC),
                status="running",
                consecutive_failures=(
                    previous_completed.consecutive_failures if previous_completed else 0
                ),
            )
            session.add(run)
            session.flush()
            return run.id

    def begin_attempt(self, run_id: int, attempt_number: int, started_at: datetime) -> int:
        _require_aware(started_at, "started_at")
        if attempt_number <= 0:
            raise ValueError("attempt_number must be positive")
        with Session(self.engine) as session, session.begin():
            run = session.get(NesoCollectorRun, run_id)
            if run is None or run.status != "running":
                raise ValueError("collector run is not active")
            if attempt_number != run.attempt_count + 1:
                raise ValueError("attempt numbers must be sequential")
            run.attempt_count = attempt_number
            attempt = NesoCollectorAttempt(
                run_id=run_id,
                attempt_number=attempt_number,
                started_at=started_at.astimezone(UTC),
                outcome="in_progress",
            )
            session.add(attempt)
            session.flush()
            return attempt.id

    def finish_attempt(self, attempt_id: int, **values: Any) -> None:
        outcome = values.pop("outcome", None)
        if outcome not in _ATTEMPT_OUTCOMES - {"in_progress"}:
            raise ValueError("invalid collector attempt outcome")
        completed_at = values.pop("completed_at", None)
        if completed_at is not None:
            _require_aware(completed_at, "completed_at")
        result: IngestionResult | None = values.pop("result", None)
        with Session(self.engine) as session, session.begin():
            attempt = session.get(NesoCollectorAttempt, attempt_id)
            if attempt is None:
                raise ValueError("collector attempt does not exist")
            if attempt.outcome != "in_progress":
                raise ValueError("collector attempt is already complete")
            attempt.outcome = outcome
            attempt.completed_at = completed_at.astimezone(UTC) if completed_at else None
            for key in (
                "error_category",
                "error_message",
                "retryable",
                "http_status",
                "retry_after",
                "retry_delay_seconds",
                "source_retrieved_at",
                "payload_sha256",
            ):
                if key in values:
                    setattr(attempt, key, values[key])
            if attempt.source_retrieved_at is not None:
                attempt.source_retrieved_at = attempt.source_retrieved_at.astimezone(UTC)
            if result is not None:
                _copy_result_to_attempt(attempt, result)

    def finish_run(self, run_id: int, **values: Any) -> None:
        status = values.pop("status", None)
        if status not in _RUN_STATUSES - {"running"}:
            raise ValueError("invalid collector run status")
        completed_at = values.pop("completed_at", None)
        if completed_at is not None:
            _require_aware(completed_at, "completed_at")
        result: IngestionResult | None = values.pop("result", None)
        attempt_count = values.pop("attempt_count", None)
        if not isinstance(attempt_count, int) or attempt_count < 0:
            raise ValueError("attempt_count must be a non-negative integer")

        with Session(self.engine) as session, session.begin():
            run = session.get(NesoCollectorRun, run_id)
            if run is None or run.status != "running":
                raise ValueError("collector run is not active")
            if attempt_count != run.attempt_count:
                raise ValueError("run attempt count does not match its persisted attempts")
            prior_completed = session.scalar(
                select(NesoCollectorRun)
                .where(
                    NesoCollectorRun.region_id == run.region_id,
                    NesoCollectorRun.id < run.id,
                    NesoCollectorRun.status != "running",
                )
                .order_by(NesoCollectorRun.id.desc())
                .limit(1)
            )
            run.status = status
            run.completed_at = completed_at.astimezone(UTC) if completed_at else None
            run.error_category = values.get("error_category")
            run.error_message = values.get("error_message")
            run.duration_seconds = values.get("duration_seconds")
            run.consecutive_failures = (
                0
                if status == "succeeded"
                else (prior_completed.consecutive_failures + 1 if prior_completed else 1)
            )
            if result is not None:
                _copy_result_to_run(run, result)
                previous_success = session.scalar(
                    select(NesoCollectorRun)
                    .where(
                        NesoCollectorRun.region_id == run.region_id,
                        NesoCollectorRun.status == "succeeded",
                        NesoCollectorRun.id < run.id,
                    )
                    .order_by(NesoCollectorRun.id.desc())
                    .limit(1)
                )
                if (
                    previous_success is not None
                    and previous_success.interval_start is not None
                    and previous_success.interval_end is not None
                ):
                    count, gap_start, gap_end = count_missing_intervals(
                        previous_success.interval_start,
                        previous_success.interval_end,
                        result.reading.observed_at,
                    )
                    run.missing_interval_count = count
                    run.gap_start = gap_start
                    run.gap_end = gap_end
                    run.duplicate_interval = (
                        previous_success.interval_start == result.reading.observed_at
                        and previous_success.interval_end == result.reading.valid_until
                        and previous_success.measurement_type == result.reading.measurement_type
                        and previous_success.intensity_gco2_per_kwh
                        == result.reading.intensity_gco2_per_kwh
                    )
                else:
                    run.missing_interval_count = 0
                    run.gap_start = None
                    run.gap_end = None
                    run.duplicate_interval = False

    def status(self, region_id: int) -> dict[str, Any]:
        if region_id <= 0:
            raise ValueError("region_id must be positive")
        with Session(self.engine) as session:
            latest_run = session.scalar(
                select(NesoCollectorRun)
                .where(NesoCollectorRun.region_id == region_id)
                .order_by(NesoCollectorRun.id.desc())
                .limit(1)
            )
            if latest_run is None:
                return _empty_status(region_id)
            last_success = session.scalar(
                select(NesoCollectorRun)
                .where(
                    NesoCollectorRun.region_id == region_id,
                    NesoCollectorRun.status == "succeeded",
                )
                .order_by(NesoCollectorRun.id.desc())
                .limit(1)
            )
            latest_attempt = session.scalar(
                select(NesoCollectorAttempt)
                .where(NesoCollectorAttempt.run_id == latest_run.id)
                .order_by(NesoCollectorAttempt.attempt_number.desc())
                .limit(1)
            )
            attempts = session.scalars(
                select(NesoCollectorAttempt)
                .where(NesoCollectorAttempt.run_id == latest_run.id)
                .order_by(NesoCollectorAttempt.attempt_number)
            ).all()
            return {
                "region_id": region_id,
                "last_run_status": latest_run.status,
                "last_run_started_at": _iso(latest_run.started_at),
                "last_run_completed_at": _iso(latest_run.completed_at),
                "last_attempted_at": _iso(
                    latest_attempt.started_at if latest_attempt else latest_run.started_at
                ),
                "last_attempt_outcome": latest_attempt.outcome if latest_attempt else None,
                "last_attempt_error_category": latest_attempt.error_category if latest_attempt else None,
                "last_attempt_http_status": latest_attempt.http_status if latest_attempt else None,
                "last_attempt_retryable": latest_attempt.retryable if latest_attempt else None,
                "last_attempt_retry_after": latest_attempt.retry_after if latest_attempt else None,
                "last_attempt_retry_delay_seconds": latest_attempt.retry_delay_seconds if latest_attempt else None,
                "attempt_count": latest_run.attempt_count,
                "attempts": [_attempt_status(attempt) for attempt in attempts],
                "last_successful_at": _iso(last_success.completed_at) if last_success else None,
                "source_retrieved_at": _iso(last_success.source_retrieved_at) if last_success else None,
                "interval_start": _iso(last_success.interval_start) if last_success else None,
                "interval_end": _iso(last_success.interval_end) if last_success else None,
                "measurement_type": last_success.measurement_type if last_success else None,
                "source_fresh": last_success.source_fresh if last_success else None,
                "payload_sha256": last_success.payload_sha256 if last_success else None,
                "raw_evidence_inserted": last_success.raw_evidence_inserted if last_success else None,
                "observation_inserted": last_success.observation_inserted if last_success else None,
                "duplicate_interval": last_success.duplicate_interval if last_success else None,
                "missing_interval_count": last_success.missing_interval_count if last_success else 0,
                "gap_start": _iso(last_success.gap_start) if last_success else None,
                "gap_end": _iso(last_success.gap_end) if last_success else None,
                "duration_seconds": latest_run.duration_seconds,
                "consecutive_failures": latest_run.consecutive_failures,
                "last_error_category": latest_run.error_category,
                "last_error": latest_run.error_message,
            }


def _copy_result_to_attempt(attempt: NesoCollectorAttempt, result: IngestionResult) -> None:
    reading = result.reading
    attempt.payload_sha256 = result.payload_sha256
    attempt.source_retrieved_at = reading.retrieved_at
    attempt.interval_start = reading.observed_at
    attempt.interval_end = reading.valid_until
    attempt.measurement_type = reading.measurement_type
    attempt.source_fresh = reading.is_fresh
    attempt.raw_evidence_inserted = result.raw_payload_inserted
    attempt.observation_inserted = result.inserted


def _copy_result_to_run(run: NesoCollectorRun, result: IngestionResult) -> None:
    reading = result.reading
    run.payload_sha256 = result.payload_sha256
    run.source_retrieved_at = reading.retrieved_at
    run.interval_start = reading.observed_at
    run.interval_end = reading.valid_until
    run.measurement_type = reading.measurement_type
    run.intensity_gco2_per_kwh = reading.intensity_gco2_per_kwh
    run.source_fresh = reading.is_fresh
    run.raw_evidence_inserted = result.raw_payload_inserted
    run.observation_inserted = result.inserted


def _attempt_status(attempt: NesoCollectorAttempt) -> dict[str, Any]:
    return {
        "attempt_number": attempt.attempt_number,
        "started_at": _iso(attempt.started_at),
        "completed_at": _iso(attempt.completed_at),
        "outcome": attempt.outcome,
        "error_category": attempt.error_category,
        "http_status": attempt.http_status,
        "retryable": attempt.retryable,
        "retry_after": attempt.retry_after,
        "retry_delay_seconds": attempt.retry_delay_seconds,
        "payload_sha256": attempt.payload_sha256,
        "source_retrieved_at": _iso(attempt.source_retrieved_at),
    }


def _empty_status(region_id: int) -> dict[str, Any]:
    return {
        "region_id": region_id,
        "last_run_status": None,
        "last_run_started_at": None,
        "last_run_completed_at": None,
        "last_attempted_at": None,
        "last_attempt_outcome": None,
        "last_attempt_error_category": None,
        "last_attempt_http_status": None,
        "last_attempt_retryable": None,
        "last_attempt_retry_after": None,
        "last_attempt_retry_delay_seconds": None,
        "attempt_count": 0,
        "attempts": [],
        "last_successful_at": None,
        "source_retrieved_at": None,
        "interval_start": None,
        "interval_end": None,
        "measurement_type": None,
        "source_fresh": None,
        "payload_sha256": None,
        "raw_evidence_inserted": None,
        "observation_inserted": None,
        "duplicate_interval": None,
        "missing_interval_count": 0,
        "gap_start": None,
        "gap_end": None,
        "duration_seconds": None,
        "consecutive_failures": 0,
        "last_error_category": None,
        "last_error": None,
    }


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
