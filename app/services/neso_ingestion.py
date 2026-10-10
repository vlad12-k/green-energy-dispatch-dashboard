"""Transactional ingestion of raw NESO responses with deterministic replay."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
import hashlib
import json
from typing import Any, Protocol

from app.services.neso_carbon_intensity import (
    CarbonIntensityReading,
    parse_regional_response,
)


class IngestionErrorCategory(str, Enum):
    SOURCE = "source"
    VALIDATION = "validation"
    PERSISTENCE = "persistence"


class IngestionError(RuntimeError):
    """Raised with an explicit source, validation, or persistence category."""

    def __init__(self, category: IngestionErrorCategory, message: str) -> None:
        self.category = category
        super().__init__(message)


class RawNesoClient(Protocol):
    region_id: int
    max_age_seconds: int

    async def fetch_raw(self) -> tuple[Any, datetime, str]: ...


class NesoRepository(Protocol):
    def transaction(self) -> AbstractContextManager[Any]: ...
    def insert_reading(self, record: Mapping[str, Any]) -> bool: ...
    def insert_invalid_payload(self, record: Mapping[str, Any]) -> None: ...


@dataclass(frozen=True)
class IngestionResult:
    reading: CarbonIntensityReading
    payload_sha256: str
    inserted: bool


def _canonical_payload_bytes(payload: Any) -> bytes:
    try:
        return json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ValueError("response cannot be represented as canonical JSON") from exc


def _record(reading: CarbonIntensityReading, payload: Any, digest: str) -> dict[str, Any]:
    return {
        "intensity_gco2_per_kwh": reading.intensity_gco2_per_kwh,
        "measurement_type": reading.measurement_type,
        "region_id": reading.region_id,
        "region_name": reading.region_name,
        "intensity_index": reading.intensity_index,
        "observed_at": reading.observed_at,
        "valid_until": reading.valid_until,
        "retrieved_at": reading.retrieved_at,
        "interval_status": reading.interval_status,
        "interval_lag_seconds": reading.interval_lag_seconds,
        "is_fresh": reading.is_fresh,
        "age_seconds": reading.age_seconds,
        "source_api": reading.source_api,
        "source_url": reading.source_url,
        "raw_payload": payload,
        "payload_sha256": digest,
    }


def ingest_neso(client: RawNesoClient, repository: NesoRepository) -> IngestionResult:
    """Fetch once, validate the exact response, then persist atomically/idempotently.

    The client's fetch_raw seam returns (decoded JSON payload, retrieval timestamp,
    source URL). Repository insert_reading must enforce uniqueness on the natural
    observation key (region_id, observed_at) and return False when it already exists.
    """
    try:
        payload, retrieved_at, source_url = _run_fetch(client)
    except Exception as exc:
        raise IngestionError(IngestionErrorCategory.SOURCE, "NESO response fetch failed") from exc
    try:
        digest = hashlib.sha256(_canonical_payload_bytes(payload)).hexdigest()
    except ValueError as exc:
        raise IngestionError(IngestionErrorCategory.VALIDATION, str(exc)) from exc
    try:
        reading = parse_regional_response(
            payload,
            region_id=client.region_id,
            now=retrieved_at,
            max_age_seconds=client.max_age_seconds,
            source_url=source_url,
        )
    except (ValueError, TypeError) as exc:
        invalid_record = {
            "raw_payload": payload,
            "payload_sha256": digest,
            "region_id": client.region_id,
            "source_api": getattr(client, "source_api", "neso-carbon-intensity-api"),
            "source_url": source_url,
            "retrieved_at": retrieved_at,
            "validation_error": str(exc),
        }
        try:
            with repository.transaction():
                repository.insert_invalid_payload(invalid_record)
        except Exception as persistence_exc:
            raise IngestionError(
                IngestionErrorCategory.PERSISTENCE,
                "Rejected NESO payload could not be preserved",
            ) from persistence_exc
        raise IngestionError(IngestionErrorCategory.VALIDATION, str(exc)) from exc
    try:
        with repository.transaction():
            inserted = repository.insert_reading(_record(reading, payload, digest))
    except Exception as exc:
        raise IngestionError(IngestionErrorCategory.PERSISTENCE, "NESO reading persistence failed") from exc
    return IngestionResult(reading, digest, inserted)


def _run_fetch(client: RawNesoClient) -> tuple[Any, datetime, str]:
    """Run sync or async fetch seams, keeping one upstream call per ingestion."""
    import asyncio
    import inspect

    result = client.fetch_raw()
    if inspect.isawaitable(result):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            result = asyncio.run(result)
        else:
            if inspect.iscoroutine(result):
                result.close()
            raise RuntimeError("ingest_neso cannot run inside an event loop; use ingest_neso_async")
    if not isinstance(result, tuple) or len(result) != 3:
        raise TypeError("fetch_raw must return (payload, retrieved_at, source_url)")
    payload, retrieved_at, source_url = result
    if (
        not isinstance(retrieved_at, datetime)
        or retrieved_at.tzinfo is None
        or retrieved_at.utcoffset() is None
        or not isinstance(source_url, str)
        or not source_url.startswith("https://")
    ):
        raise TypeError("fetch_raw returned invalid retrieval metadata")
    return payload, retrieved_at.astimezone(UTC), source_url


def replay_neso(record: Mapping[str, Any]) -> CarbonIntensityReading:
    """Re-parse stored evidence offline using its original retrieval context."""
    try:
        payload = record["raw_payload"]
        expected = record["payload_sha256"]
        actual = hashlib.sha256(_canonical_payload_bytes(payload)).hexdigest()
        if actual != expected:
            raise ValueError("stored NESO payload hash does not match")
        return parse_regional_response(
            payload,
            region_id=record["region_id"],
            now=record["retrieved_at"],
            max_age_seconds=2**63 - 1,
            source_url=record["source_url"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise IngestionError(IngestionErrorCategory.VALIDATION, str(exc)) from exc


async def ingest_neso_async(client: RawNesoClient, repository: NesoRepository) -> IngestionResult:
    """Run sync DB persistence off the event loop for async callers."""
    import asyncio

    return await asyncio.to_thread(ingest_neso, client, repository)
