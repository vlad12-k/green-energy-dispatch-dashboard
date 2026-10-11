"""Opt-in persistence of NESO regional forecast archive pages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.persistence.models import CarbonObservation, RawPayload
from app.persistence.repository import CarbonRepository
from app.services.neso_carbon_intensity import CarbonIntensityReading
from app.services.neso_historical import (
    DATASET_API,
    HistoricalAcquisitionReport,
    HistoricalForecastObservation,
    REGION_COLUMNS,
    parse_historical_response,
    requested_window_from_response,
)


@dataclass(frozen=True)
class HistoricalPersistenceResult:
    raw_payload_id: int
    raw_payload_inserted: bool
    inserted_observation_count: int
    duplicate_observation_count: int


def persist_historical_acquisition(
    session: Session,
    report: HistoricalAcquisitionReport,
    payload: dict[str, Any],
) -> HistoricalPersistenceResult:
    """Persist immutable source JSON and normalized forecast rows atomically.

    The caller owns the transaction. The HTTP response was explicitly validated
    by the historical source adapter; ``validation_fresh=False`` records that its
    target periods are retrospective and does not mark the payload rejected.
    """
    try:
        canonical_payload = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ValueError("historical source payload is not canonical JSON") from exc
    if hashlib.sha256(canonical_payload).hexdigest() != report.payload_sha256:
        raise ValueError(
            "historical source payload does not match its validated report"
        )
    if any(row.region_id != report.region_id for row in report.observations):
        raise ValueError("historical forecast rows do not match the report region")

    repository = CarbonRepository(session)
    raw, raw_inserted = repository.store_payload_with_status(
        payload,
        region_id=report.region_id,
        source_url=report.source_url,
        retrieved_at=report.acquired_at,
        validation_fresh=False,
        validation_error=None,
        source_api=DATASET_API,
    )
    inserted_count = 0
    for row in report.observations:
        statement = (
            insert(CarbonObservation)
            .values(**_observation_values(row, raw.id, report.source_url))
            .on_conflict_do_nothing(constraint="uq_carbon_observation_identity")
            .returning(CarbonObservation.id)
        )
        inserted_count += session.execute(statement).scalar_one_or_none() is not None
    return HistoricalPersistenceResult(
        raw_payload_id=raw.id,
        raw_payload_inserted=raw_inserted,
        inserted_observation_count=inserted_count,
        duplicate_observation_count=len(report.observations) - inserted_count,
    )


def replay_historical_forecast_record(
    raw: RawPayload,
    *,
    source_record_id: int,
) -> CarbonIntensityReading:
    """Verify stored archive evidence and reproduce one identified forecast row."""
    if raw.source_api != DATASET_API:
        raise ValueError("raw payload is not from the NESO historical forecast archive")
    canonical = json.dumps(
        raw.payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    if hashlib.sha256(canonical).hexdigest() != raw.payload_sha256:
        raise ValueError("stored historical forecast payload SHA-256 mismatch")
    if canonical != raw.payload_bytes:
        raise ValueError(
            "stored historical forecast JSON does not match preserved bytes"
        )
    result = raw.payload.get("result")
    records = result.get("records") if isinstance(result, dict) else None
    if not isinstance(records, list):
        raise ValueError("stored historical forecast payload has invalid records")
    matches = [
        record
        for record in records
        if isinstance(record, dict) and str(record.get("_id")) == str(source_record_id)
    ]
    if not matches:
        raise ValueError("source record ID is not present in the stored payload")
    try:
        observed_at = datetime.fromisoformat(
            str(matches[0].get("datetime")).replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise ValueError("stored source record has an invalid timestamp") from exc
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=UTC)
    observed_at = observed_at.astimezone(UTC)
    query_window = requested_window_from_response(raw.payload, region_id=raw.region_id)
    if query_window is None:
        query_window = (observed_at, observed_at + timedelta(minutes=30))
    report = parse_historical_response(
        raw.payload,
        region_id=raw.region_id,
        requested_start=query_window[0],
        requested_end=query_window[1],
        acquired_at=raw.retrieved_at,
        source_url=raw.source_url,
    )
    selected = next(
        (
            item
            for item in report.observations
            if item.source_record_id == source_record_id
        ),
        None,
    )
    if selected is None:
        raise ValueError(
            "source record is rejected or conflicted in the stored payload"
        )
    return _reading(selected, raw.source_url)


def _observation_values(
    row: HistoricalForecastObservation, raw_payload_id: int, source_url: str
) -> dict[str, Any]:
    age_seconds = max(0.0, (row.retrieved_at - row.observed_at).total_seconds())
    lag_seconds = max(0.0, (row.retrieved_at - row.valid_until).total_seconds())
    if row.retrieved_at < row.observed_at:
        interval_status = "upcoming"
    elif row.retrieved_at >= row.valid_until:
        interval_status = "completed"
    else:
        interval_status = "in_progress"
    return {
        "raw_payload_id": raw_payload_id,
        "region_id": row.region_id,
        "region_name": row.region_name,
        "observed_at": row.observed_at,
        "valid_until": row.valid_until,
        "retrieved_at": row.retrieved_at,
        "measurement_type": "forecast",
        "intensity_gco2_per_kwh": row.intensity_gco2_per_kwh,
        "intensity_index": "not_provided",
        "interval_status": interval_status,
        "interval_lag_seconds": lag_seconds,
        "is_fresh": False,
        "age_seconds": age_seconds,
        "source_api": DATASET_API,
        "source_url": source_url,
        "schema_version": 1,
    }


def _reading(
    row: HistoricalForecastObservation, source_url: str
) -> CarbonIntensityReading:
    age_seconds = max(0.0, (row.retrieved_at - row.observed_at).total_seconds())
    lag_seconds = max(0.0, (row.retrieved_at - row.valid_until).total_seconds())
    if row.retrieved_at < row.observed_at:
        interval_status = "upcoming"
    elif row.retrieved_at >= row.valid_until:
        interval_status = "completed"
    else:
        interval_status = "in_progress"
    return CarbonIntensityReading(
        intensity_gco2_per_kwh=row.intensity_gco2_per_kwh,
        measurement_type="forecast",
        region_id=row.region_id,
        region_name=REGION_COLUMNS[row.region_id],
        intensity_index="not_provided",
        observed_at=row.observed_at,
        valid_until=row.valid_until,
        retrieved_at=row.retrieved_at,
        interval_status=interval_status,
        interval_lag_seconds=lag_seconds,
        is_fresh=False,
        age_seconds=age_seconds,
        source_api=DATASET_API,
        source_url=source_url,
    )
