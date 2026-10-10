"""Atomic write repository for NESO raw evidence and parsed observations."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.services.neso_carbon_intensity import (
    CarbonIntensityReading,
    SOURCE_API,
    parse_regional_response,
)
from app.persistence.models import CarbonObservation, RawPayload


class CarbonRepository:
    """Persist immutable source documents and idempotent normalized values.

    The caller owns the SQLAlchemy transaction; raw evidence and observations
    should be written within the same ``Session.begin()`` context.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def store_payload(
        self,
        payload: dict[str, Any],
        *,
        region_id: int,
        source_url: str,
        retrieved_at: datetime,
        validation_fresh: bool,
        validation_error: str | None = None,
        source_api: str = SOURCE_API,
        schema_version: int = 1,
    ) -> RawPayload:
        if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
            raise ValueError("retrieved_at must include a timezone")
        retrieved_at = retrieved_at.astimezone(UTC)
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        statement = insert(RawPayload).values(
            region_id=region_id,
            source_api=source_api,
            source_url=source_url,
            retrieved_at=retrieved_at,
            payload=payload,
            payload_bytes=encoded,
            payload_sha256=digest,
            validation_fresh=validation_fresh,
            validation_error=validation_error,
            schema_version=schema_version,
        ).on_conflict_do_nothing(constraint="uq_raw_payload_identity")
        self.session.execute(statement)
        row = self.session.scalar(
            select(RawPayload).where(
                RawPayload.region_id == region_id,
                RawPayload.source_api == source_api,
                RawPayload.payload_sha256 == digest,
                RawPayload.retrieved_at == retrieved_at,
            )
        )
        assert row is not None
        return row

    def store_observation(
        self, raw: RawPayload, *, region_id: int
    ) -> CarbonObservation:
        observation, _inserted = self.store_observation_with_status(
            raw, region_id=region_id
        )
        return observation

    def store_observation_with_status(
        self, raw: RawPayload, *, region_id: int
    ) -> tuple[CarbonObservation, bool]:
        """Insert idempotently and report whether this transaction created a row."""
        parsed = replay_raw_payload(raw, region_id=region_id)
        values = dict(
            raw_payload_id=raw.id,
            region_id=parsed.region_id,
            region_name=parsed.region_name,
            observed_at=parsed.observed_at,
            valid_until=parsed.valid_until,
            retrieved_at=parsed.retrieved_at,
            measurement_type=parsed.measurement_type,
            intensity_gco2_per_kwh=parsed.intensity_gco2_per_kwh,
            intensity_index=parsed.intensity_index,
            interval_status=parsed.interval_status,
            interval_lag_seconds=parsed.interval_lag_seconds,
            is_fresh=parsed.is_fresh,
            age_seconds=parsed.age_seconds,
            source_api=parsed.source_api,
            source_url=parsed.source_url,
            schema_version=1,
        )
        statement = (
            insert(CarbonObservation)
            .values(**values)
            .on_conflict_do_nothing(constraint="uq_carbon_observation_identity")
            .returning(CarbonObservation.id)
        )
        result = self.session.execute(statement)
        inserted_id = result.scalar_one_or_none()
        row = self.session.scalar(
            select(CarbonObservation).where(
                CarbonObservation.raw_payload_id == raw.id,
                CarbonObservation.region_id == parsed.region_id,
                CarbonObservation.observed_at == parsed.observed_at,
                CarbonObservation.valid_until == parsed.valid_until,
                CarbonObservation.measurement_type == parsed.measurement_type,
            )
        )
        assert row is not None
        return row, inserted_id is not None


def replay_raw_payload(
    raw: RawPayload, *, region_id: int | None = None
) -> CarbonIntensityReading:
    """Deterministically re-parse stored bytes; never makes a network request."""
    digest = hashlib.sha256(raw.payload_bytes).hexdigest()
    if digest != raw.payload_sha256:
        raise ValueError("stored raw payload SHA-256 mismatch")
    payload = json.loads(raw.payload_bytes)
    if payload != raw.payload:
        raise ValueError("stored JSON payload does not match preserved raw bytes")
    if raw.validation_error is not None:
        raise ValueError(
            "NESO source evidence was rejected during ingestion: "
            f"{raw.validation_error}"
        )
    return parse_regional_response(
        payload,
        region_id=region_id or raw.region_id,
        now=raw.retrieved_at,
        max_age_seconds=2**31 - 1,
        source_url=raw.source_url,
    )
