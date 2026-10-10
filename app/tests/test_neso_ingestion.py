import asyncio
from datetime import UTC, datetime, timedelta
import threading
import time

import httpx
import pytest

from app.services.neso_carbon_intensity import NesoCarbonIntensityClient
from app.services.neso_ingestion import (
    IngestionError,
    IngestionErrorCategory,
    ingest_neso,
    ingest_neso_async,
    replay_neso,
)


def payload(now):
    start = now - timedelta(minutes=4)
    return {"data": [{"regionid": 13, "shortname": "London", "data": [{"from": start.isoformat(), "to": (start + timedelta(minutes=30)).isoformat(), "intensity": {"actual": 45, "forecast": 51, "index": "low"}}]}]}


class FakeClient:
    region_id = 13
    max_age_seconds = 3600

    def __init__(self, result):
        self.result = result
        self.calls = 0

    async def fetch_raw(self):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeRepository:
    def __init__(self, *, fail=False):
        self.rows = {}
        self.fail = fail
        self.rollbacks = 0

    def transaction(self):
        return self

    def __enter__(self):
        self.before = self.rows.copy()
        return self

    def __exit__(self, exc_type, *_):
        if exc_type or self.fail:
            self.rows = self.before
            self.rollbacks += 1
        return False

    def insert_reading(self, record):
        if self.fail:
            raise RuntimeError("write failed")
        key = (record["region_id"], record["observed_at"])
        if key in self.rows:
            return False
        self.rows[key] = record
        return True

    def insert_invalid_payload(self, record):
        if self.fail:
            raise RuntimeError("write failed")
        key = (record["region_id"], record["payload_sha256"])
        self.rows.setdefault(key, record)


def test_ingestion_fetches_once_persists_raw_and_is_idempotent():
    now = datetime.now(UTC)
    raw = payload(now)
    client = FakeClient((raw, now, "https://example.test/region"))
    repo = FakeRepository()

    first = ingest_neso(client, repo)
    second = ingest_neso(client, repo)

    assert first.inserted is True
    assert second.inserted is False
    assert client.calls == 2
    assert len(repo.rows) == 1
    assert next(iter(repo.rows.values()))["raw_payload"] == raw
    assert replay_neso(next(iter(repo.rows.values()))).intensity_gco2_per_kwh == 45


def test_source_validation_and_persistence_errors_are_categorized():
    now = datetime.now(UTC)
    repo = FakeRepository()
    with pytest.raises(IngestionError) as source:
        ingest_neso(FakeClient(RuntimeError("offline")), repo)
    assert source.value.category is IngestionErrorCategory.SOURCE

    client = FakeClient(({}, now, "https://example.test"))
    with pytest.raises(IngestionError) as validation:
        ingest_neso(client, repo)
    assert validation.value.category is IngestionErrorCategory.VALIDATION
    assert client.calls == 1
    assert len(repo.rows) == 1
    rejected = next(iter(repo.rows.values()))
    assert rejected["validation_error"]
    assert rejected["raw_payload"] == {}

    with pytest.raises(IngestionError) as persistence:
        ingest_neso(FakeClient((payload(now), now, "https://example.test")), FakeRepository(fail=True))
    assert persistence.value.category is IngestionErrorCategory.PERSISTENCE


def test_real_neso_client_fetches_exactly_once_for_one_ingestion():
    now = datetime.now(UTC)
    request_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, json=payload(now), request=request)

    client = NesoCarbonIntensityClient(
        transport=httpx.MockTransport(handler),
        region_id=13,
        max_age_seconds=3600,
    )
    repository = FakeRepository()

    result = ingest_neso(client, repository)

    assert request_count == 1
    assert result.inserted is True
    assert result.reading.region_id == 13


@pytest.mark.asyncio
async def test_async_ingestion_does_not_block_event_loop_during_persistence():
    now = datetime.now(UTC)
    completed = threading.Event()

    class SlowRepository(FakeRepository):
        def insert_reading(self, record):
            time.sleep(0.1)
            inserted = super().insert_reading(record)
            completed.set()
            return inserted

    async def loop_probe():
        await asyncio.sleep(0.01)
        assert not completed.is_set()

    result, _ = await asyncio.gather(
        ingest_neso_async(
            FakeClient((payload(now), now, "https://example.test/region")),
            SlowRepository(),
        ),
        loop_probe(),
    )

    assert result.inserted is True
    assert completed.is_set()
