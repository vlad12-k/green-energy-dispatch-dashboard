from datetime import UTC, datetime, timedelta
from threading import Event

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.collect_neso import _collector_lock
from app.persistence.collector_repository import NesoCollectorStateRepository
from app.persistence.ingestion_repository import SqlAlchemyNesoRepository
from app.persistence.models import CarbonObservation, RawPayload
from app.services.neso_carbon_intensity import SOURCE_API, CarbonIntensityReading
from app.services.neso_carbon_intensity import NesoCarbonIntensityClient
from app.services.neso_collector import CollectorConfig, NesoCollector
from app.services.neso_ingestion import (
    IngestionError,
    IngestionErrorCategory,
    IngestionResult,
    ingest_neso,
)


def reading(start: datetime, *, kind: str = "actual", fresh: bool = True) -> CarbonIntensityReading:
    return CarbonIntensityReading(
        intensity_gco2_per_kwh=46.0,
        measurement_type=kind,
        region_id=13,
        region_name="London",
        intensity_index="low",
        observed_at=start,
        valid_until=start + timedelta(minutes=30),
        retrieved_at=start + timedelta(minutes=5),
        interval_status="completed" if kind == "forecast" else "in_progress",
        interval_lag_seconds=0.0,
        is_fresh=fresh,
        age_seconds=300.0,
        source_api=SOURCE_API,
        source_url="https://api.carbonintensity.org.uk/regional/regionid/13",
    )


def result(start: datetime, *, kind: str = "actual", fresh: bool = True) -> IngestionResult:
    return IngestionResult(reading(start, kind=kind, fresh=fresh), "b" * 64, True, True)


def finish_success(store, run_id: int, cycle_start: datetime, source_reading: IngestionResult) -> None:
    attempt_id = store.begin_attempt(run_id, 1, cycle_start)
    store.finish_attempt(
        attempt_id,
        completed_at=cycle_start + timedelta(seconds=1),
        outcome="success",
        result=source_reading,
    )
    store.finish_run(
        run_id,
        completed_at=cycle_start + timedelta(seconds=2),
        status="succeeded",
        attempt_count=1,
        error_category=None,
        error_message=None,
        duration_seconds=2.0,
        result=source_reading,
    )


def test_collector_status_persists_attempts_freshness_gaps_and_provenance(postgres_engine) -> None:
    store = NesoCollectorStateRepository(postgres_engine)
    base = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)

    first_run = store.begin_run(13, base)
    finish_success(store, first_run, base, result(base, kind="forecast", fresh=False))
    first_status = store.status(13)
    assert first_status["measurement_type"] == "forecast"
    assert first_status["source_fresh"] is False

    second_start = base + timedelta(hours=1, minutes=30)
    second_run = store.begin_run(13, second_start)
    first_attempt = store.begin_attempt(second_run, 1, second_start)
    store.finish_attempt(
        first_attempt,
        completed_at=second_start + timedelta(seconds=1),
        outcome="source_failure",
        error_category="source",
        error_message="NESO returned HTTP 429",
        retryable=True,
        http_status=429,
        retry_after="12",
        retry_delay_seconds=12.0,
    )
    second_attempt = store.begin_attempt(second_run, 2, second_start + timedelta(seconds=13))
    successful = result(second_start, kind="actual", fresh=True)
    store.finish_attempt(
        second_attempt,
        completed_at=second_start + timedelta(seconds=14),
        outcome="success",
        result=successful,
    )
    store.finish_run(
        second_run,
        completed_at=second_start + timedelta(seconds=15),
        status="succeeded",
        attempt_count=2,
        error_category=None,
        error_message=None,
        duration_seconds=15.0,
        result=successful,
    )

    status = store.status(13)

    assert status["last_run_status"] == "succeeded"
    assert status["attempt_count"] == 2
    assert status["last_attempt_outcome"] == "success"
    assert status["last_attempted_at"] == (second_start + timedelta(seconds=13)).isoformat()
    assert status["last_successful_at"] == (second_start + timedelta(seconds=15)).isoformat()
    assert status["source_retrieved_at"] == successful.reading.retrieved_at.isoformat()
    assert status["interval_start"] == successful.reading.observed_at.isoformat()
    assert status["interval_end"] == successful.reading.valid_until.isoformat()
    assert status["measurement_type"] == "actual"
    assert status["source_fresh"] is True
    assert status["consecutive_failures"] == 0
    assert status["missing_interval_count"] == 2
    assert status["raw_evidence_inserted"] is True
    assert status["observation_inserted"] is True
    assert status["duplicate_interval"] is False


def test_collector_status_counts_consecutive_failed_cycles_and_keeps_last_success(postgres_engine) -> None:
    store = NesoCollectorStateRepository(postgres_engine)
    base = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    success_run = store.begin_run(13, base)
    finish_success(store, success_run, base, result(base))

    for offset in (1, 2):
        started_at = base + timedelta(hours=offset)
        run_id = store.begin_run(13, started_at)
        attempt_id = store.begin_attempt(run_id, 1, started_at)
        store.finish_attempt(
            attempt_id,
            completed_at=started_at + timedelta(seconds=1),
            outcome="persistence_failure",
            error_category="persistence",
            error_message="NESO reading persistence failed",
            retryable=False,
        )
        store.finish_run(
            run_id,
            completed_at=started_at + timedelta(seconds=2),
            status="failed",
            attempt_count=1,
            error_category="persistence",
            error_message="NESO reading persistence failed",
            duration_seconds=2.0,
        )

    status = store.status(13)

    assert status["last_run_status"] == "failed"
    assert status["consecutive_failures"] == 2
    assert status["last_error_category"] == "persistence"
    assert status["last_successful_at"] == (base + timedelta(seconds=2)).isoformat()
    assert status["measurement_type"] == "actual"
    assert status["source_fresh"] is True


def test_interrupted_run_does_not_reset_consecutive_failure_count(postgres_engine) -> None:
    store = NesoCollectorStateRepository(postgres_engine)
    base = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)

    failed_run = store.begin_run(13, base)
    failed_attempt = store.begin_attempt(failed_run, 1, base)
    store.finish_attempt(
        failed_attempt,
        completed_at=base + timedelta(seconds=1),
        outcome="source_failure",
        error_category="source",
        error_message="NESO request timed out",
        retryable=True,
    )
    store.finish_run(
        failed_run,
        completed_at=base + timedelta(seconds=2),
        status="failed",
        attempt_count=1,
        error_category="source",
        error_message="NESO request timed out",
        duration_seconds=2.0,
    )

    interrupted_run = store.begin_run(13, base + timedelta(hours=1))
    assert store.status(13)["consecutive_failures"] == 1

    recovery_run = store.begin_run(13, base + timedelta(hours=2))
    recovery_attempt = store.begin_attempt(recovery_run, 1, base + timedelta(hours=2))
    store.finish_attempt(
        recovery_attempt,
        completed_at=base + timedelta(hours=2, seconds=1),
        outcome="source_failure",
        error_category="source",
        error_message="NESO request timed out",
        retryable=True,
    )
    store.finish_run(
        recovery_run,
        completed_at=base + timedelta(hours=2, seconds=2),
        status="failed",
        attempt_count=1,
        error_category="source",
        error_message="NESO request timed out",
        duration_seconds=2.0,
    )

    assert interrupted_run < recovery_run
    assert store.status(13)["consecutive_failures"] == 2


def test_graceful_shutdown_during_backoff_keeps_postgres_run_incomplete(postgres_engine) -> None:
    stop_event = Event()
    state_store = NesoCollectorStateRepository(postgres_engine)

    def fail_transiently():
        raise IngestionError(
            IngestionErrorCategory.SOURCE,
            "NESO request timed out",
            retryable=True,
        )

    def stop_during_wait(event: Event, _delay: float) -> bool:
        event.set()
        return True

    collector = NesoCollector(
        config=CollectorConfig(region_id=13),
        ingest_once=fail_transiently,
        state_store=state_store,
        wait=stop_during_wait,
    )

    result = collector.poll_once(stop_event)
    status = state_store.status(13)

    assert result.status == "interrupted"
    assert status["last_run_status"] == "running"
    assert status["last_attempt_outcome"] == "source_failure"
    assert status["consecutive_failures"] == 0


def test_retry_after_deadline_survives_worker_restart_in_postgres(postgres_engine) -> None:
    start = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    initial_store = NesoCollectorStateRepository(postgres_engine)
    failed_run = initial_store.begin_run(13, start)
    attempt_id = initial_store.begin_attempt(failed_run, 1, start)
    initial_store.finish_attempt(
        attempt_id,
        completed_at=start,
        outcome="source_failure",
        error_category="source",
        error_message="NESO returned HTTP 429",
        retryable=True,
        http_status=429,
        retry_after="3600",
    )
    initial_store.finish_run(
        failed_run,
        completed_at=start + timedelta(seconds=1),
        status="failed",
        attempt_count=1,
        error_category="source",
        error_message="NESO returned HTTP 429",
        duration_seconds=1.0,
    )

    class ManualClock:
        current = datetime(2026, 1, 1, 12, 30, tzinfo=UTC)

        def wait(self, event: Event, seconds: float) -> bool:
            waits.append(seconds)
            self.current += timedelta(seconds=seconds)
            return event.is_set()

    clock = ManualClock()
    waits: list[float] = []
    request_times: list[datetime] = []

    def ingest_once() -> IngestionResult:
        request_times.append(clock.current)
        return result(clock.current - timedelta(minutes=5))

    restarted_store = NesoCollectorStateRepository(postgres_engine)
    collector = NesoCollector(
        config=CollectorConfig(
            region_id=13,
            poll_interval_seconds=1800,
            max_attempts=3,
            retry_base_seconds=5,
            retry_max_seconds=60,
        ),
        ingest_once=ingest_once,
        state_store=restarted_store,
        now=lambda: clock.current,
        wait=clock.wait,
    )

    assert initial_store.latest_retry_after_record(13) == ("3600", start)
    next_run = collector.poll_once()

    assert next_run.status == "succeeded"
    assert initial_store.latest_retry_after_record(13) is None
    assert request_times == [datetime(2026, 1, 1, 13, 0, tzinfo=UTC)]
    assert waits == [1800]


def test_collector_state_schema_rejects_invalid_attempt_outcomes(postgres_engine) -> None:
    store = NesoCollectorStateRepository(postgres_engine)
    started_at = datetime(2026, 1, 1, tzinfo=UTC)
    run_id = store.begin_run(13, started_at)
    attempt_id = store.begin_attempt(run_id, 1, started_at)

    with pytest.raises(ValueError, match="outcome"):
        store.finish_attempt(
            attempt_id,
            completed_at=started_at + timedelta(seconds=1),
            outcome="fabricated_success",
        )


def test_postgres_advisory_lock_prevents_a_second_collector(postgres_engine) -> None:
    with _collector_lock(postgres_engine, 13):
        with pytest.raises(SystemExit, match="already holds the lock"):
            with _collector_lock(postgres_engine, 13):
                pytest.fail("a second collector acquired the same region lock")


def test_scheduled_collector_ingests_mocked_neso_data_and_reports_duplicate_interval(
    postgres_engine,
) -> None:
    now = datetime.now(UTC)
    start = now - timedelta(minutes=5)
    payload = {
        "data": [{
            "regionid": 13,
            "shortname": "London",
            "data": [{
                "from": start.isoformat(),
                "to": (start + timedelta(minutes=30)).isoformat(),
                "intensity": {"actual": 46, "forecast": 49, "index": "low"},
            }],
        }],
    }
    request_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, json=payload, request=request)

    client = NesoCarbonIntensityClient(transport=httpx.MockTransport(handler))

    def ingest_once():
        with Session(postgres_engine) as session:
            return ingest_neso(client, SqlAlchemyNesoRepository(session))

    state_store = NesoCollectorStateRepository(postgres_engine)
    collector = NesoCollector(
        config=CollectorConfig(region_id=13),
        ingest_once=ingest_once,
        state_store=state_store,
    )

    assert collector.poll_once().status == "succeeded"
    assert collector.poll_once().status == "succeeded"
    status = state_store.status(13)

    with Session(postgres_engine) as session:
        raw_count = session.scalar(select(func.count()).select_from(RawPayload))
        observation_count = session.scalar(select(func.count()).select_from(CarbonObservation))

    assert request_count == 2
    assert raw_count == 2
    assert observation_count == 2
    assert status["measurement_type"] == "actual"
    assert status["source_fresh"] is True
    assert status["raw_evidence_inserted"] is True
    assert status["observation_inserted"] is True
    assert status["duplicate_interval"] is True
