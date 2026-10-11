"""PostgreSQL integration coverage for immutable NESO evidence and replay."""
from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.persistence.models import CarbonObservation, RawPayload
from app.persistence.ingestion_repository import SqlAlchemyNesoRepository
from app.persistence.repository import CarbonRepository
from app.persistence.replay import replay_raw_payload
from app.services.neso_ingestion import (
    IngestionError,
    IngestionErrorCategory,
    ingest_neso,
)

DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="TEST_DATABASE_URL is not set")


def sample_payload(now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC).replace(second=0, microsecond=0)
    return {"data": [{"regionid": 13, "shortname": "London", "data": [{
        "from": (now - timedelta(minutes=5)).isoformat(),
        "to": (now + timedelta(minutes=25)).isoformat(),
        "intensity": {"actual": 79, "forecast": 83, "index": "low"},
    }]}]}


def test_postgres_schema_is_created_by_versioned_migrations(postgres_engine) -> None:
    with postgres_engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0002_neso_collector_operations"


def test_store_preserves_raw_hash_and_idempotently_links_observation(postgres_engine) -> None:
    engine = postgres_engine
    payload = sample_payload()
    with Session(engine) as session, session.begin():
        repo = CarbonRepository(session)
        raw = repo.store_payload(payload, region_id=13,
            source_url="https://api.carbonintensity.org.uk/regional/regionid/13",
            retrieved_at=datetime.now(UTC), validation_fresh=True)
        replay_raw_payload(raw)
        observation = repo.store_observation(raw, region_id=13)
        same_raw = repo.store_payload(payload, region_id=13,
            source_url="https://api.carbonintensity.org.uk/regional/regionid/13",
            retrieved_at=raw.retrieved_at, validation_fresh=True)
        same_observation = repo.store_observation(same_raw, region_id=13)
        assert same_raw.id == raw.id
        assert same_observation.id == observation.id
        assert raw.payload_sha256 == hashlib.sha256(raw.payload_bytes).hexdigest()
        replayed = replay_raw_payload(raw)
        assert replayed.intensity_gco2_per_kwh == 79
        assert replayed.measurement_type == "actual"
        assert replayed.retrieved_at == raw.retrieved_at
        assert observation.measurement_type == "actual"
        assert observation.region_id == 13
    with Session(engine) as session:
        assert len(session.scalars(select(RawPayload)).all()) == 1
        assert len(session.scalars(select(CarbonObservation)).all()) == 1


def test_observation_constraints_reject_invalid_interval_and_type(postgres_engine) -> None:
    with Session(postgres_engine) as session, session.begin():
        repo = CarbonRepository(session)
        raw = repo.store_payload(sample_payload(), region_id=13,
            source_url="https://api.carbonintensity.org.uk/regional/regionid/13",
            retrieved_at=datetime.now(UTC), validation_fresh=True)
        observation = repo.store_observation(raw, region_id=13)
        observation.measurement_type = "guess"
        with pytest.raises(IntegrityError):
            session.flush()


def test_postgres_ingestion_is_idempotent_and_preserves_forecast_type(postgres_engine) -> None:
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    raw_payload = sample_payload(now - timedelta(minutes=45))
    raw_payload["data"][0]["data"][0]["intensity"]["actual"] = None

    class FixedClient:
        region_id = 13
        max_age_seconds = 3600

        async def fetch_raw(self):
            return (
                raw_payload,
                now,
                "https://api.carbonintensity.org.uk/regional/regionid/13",
            )

    with Session(postgres_engine) as session:
        repository = SqlAlchemyNesoRepository(session)
        first = ingest_neso(FixedClient(), repository)
        second = ingest_neso(FixedClient(), repository)
        assert first.inserted is True
        assert second.inserted is False
        assert first.reading.measurement_type == "forecast"
        assert first.reading.interval_status == "completed"
        assert first.reading.is_fresh is False

    with Session(postgres_engine) as session:
        raw = session.scalar(select(RawPayload))
        observation = session.scalar(select(CarbonObservation))
        assert raw is not None and raw.validation_fresh is False
        assert raw.validation_error is None
        assert observation is not None and observation.measurement_type == "forecast"
        assert observation.retrieved_at.tzinfo is not None
        replayed = replay_raw_payload(raw)
        assert replayed.measurement_type == "forecast"
        assert replayed.is_fresh is False


def test_postgres_ingestion_rolls_back_raw_and_observation_together(postgres_engine) -> None:
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    payload = sample_payload(now)

    class FixedClient:
        region_id = 13
        max_age_seconds = 3600

        async def fetch_raw(self):
            return (
                payload,
                now,
                "https://api.carbonintensity.org.uk/regional/regionid/13",
            )

    class FailingRepository(SqlAlchemyNesoRepository):
        def insert_reading(self, record):
            super().insert_reading(record)
            raise RuntimeError("simulated failure after both inserts")

    with Session(postgres_engine) as session:
        with pytest.raises(IngestionError) as error:
            ingest_neso(FixedClient(), FailingRepository(session))
        assert error.value.category is IngestionErrorCategory.PERSISTENCE

    with Session(postgres_engine) as session:
        assert session.scalar(select(RawPayload)) is None
        assert session.scalar(select(CarbonObservation)) is None


def test_postgres_ingestion_preserves_rejected_payload_without_observation(postgres_engine) -> None:
    now = datetime.now(UTC)

    class InvalidClient:
        region_id = 13
        max_age_seconds = 3600

        async def fetch_raw(self):
            return ({"data": []}, now, "https://api.carbonintensity.org.uk/regional/regionid/13")

    with Session(postgres_engine) as session:
        with pytest.raises(IngestionError) as error:
            ingest_neso(InvalidClient(), SqlAlchemyNesoRepository(session))
        assert error.value.category is IngestionErrorCategory.VALIDATION

    with Session(postgres_engine) as session:
        raw = session.scalar(select(RawPayload))
        assert raw is not None
        assert raw.payload == {"data": []}
        assert raw.validation_fresh is False
        assert raw.validation_error
        assert session.scalar(select(CarbonObservation)) is None


def test_rejected_stale_evidence_is_preserved_but_cannot_replay_as_accepted(postgres_engine) -> None:
    retrieved_at = datetime.now(UTC).replace(second=0, microsecond=0)
    stale_payload = sample_payload(retrieved_at - timedelta(hours=2))

    class StaleClient:
        region_id = 13
        max_age_seconds = 3600

        async def fetch_raw(self):
            return (
                stale_payload,
                retrieved_at,
                "https://api.carbonintensity.org.uk/regional/regionid/13",
            )

    with Session(postgres_engine) as session:
        repository = SqlAlchemyNesoRepository(session)
        with pytest.raises(IngestionError) as error:
            ingest_neso(StaleClient(), repository)
        assert error.value.category is IngestionErrorCategory.VALIDATION

    with Session(postgres_engine) as session:
        raw = session.scalar(select(RawPayload))
        assert raw is not None
        assert raw.payload == stale_payload
        assert raw.validation_fresh is False
        assert raw.validation_error is not None and "stale" in raw.validation_error
        with pytest.raises(ValueError, match="rejected during ingestion") as replay_error:
            replay_raw_payload(raw)
        assert raw.validation_error in str(replay_error.value)
        assert session.scalar(select(CarbonObservation)) is None


def test_valid_historical_actual_replays_deterministically(postgres_engine) -> None:
    retrieved_at = datetime.now(UTC).replace(second=0, microsecond=0)
    historical_payload = sample_payload(retrieved_at - timedelta(minutes=45))

    class HistoricalClient:
        region_id = 13
        max_age_seconds = 3600

        async def fetch_raw(self):
            return (
                historical_payload,
                retrieved_at,
                "https://api.carbonintensity.org.uk/regional/regionid/13",
            )

    with Session(postgres_engine) as session:
        result = ingest_neso(HistoricalClient(), SqlAlchemyNesoRepository(session))
        assert result.reading.measurement_type == "actual"
        assert result.reading.interval_status == "completed"
        assert result.reading.is_fresh is True

    with Session(postgres_engine) as session:
        raw = session.scalar(select(RawPayload))
        assert raw is not None and raw.validation_error is None
        first = replay_raw_payload(raw)
        second = replay_raw_payload(raw)
        assert first == second == result.reading
