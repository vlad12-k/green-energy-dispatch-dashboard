from collections.abc import Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from threading import Event, Thread
from typing import Any, Iterator

import pytest

from app.services.neso_carbon_intensity import SOURCE_API, CarbonIntensityReading
from app.services.neso_collector import (
    CollectorConfig,
    NesoCollector,
    calculate_retry_after_deadline,
    calculate_retry_delay,
    count_missing_intervals,
)
from app.services.neso_ingestion import (
    IngestionError,
    IngestionErrorCategory,
    IngestionResult,
    ingest_neso,
)


def test_retry_delay_is_exponential_and_bounded() -> None:
    error = IngestionError(IngestionErrorCategory.SOURCE, "temporary outage", retryable=True)

    assert [
        calculate_retry_delay(error, attempt, 5.0, 30.0, datetime(2026, 1, 1, tzinfo=UTC))
        for attempt in range(1, 6)
    ] == [5.0, 10.0, 20.0, 30.0, 30.0]


def test_retry_after_seconds_and_http_date_are_respected_within_limit() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    seconds = IngestionError(
        IngestionErrorCategory.SOURCE,
        "rate limited",
        retryable=True,
        retry_after="12",
    )
    date = IngestionError(
        IngestionErrorCategory.SOURCE,
        "rate limited",
        retryable=True,
        retry_after="Thu, 01 Jan 2026 00:00:20 GMT",
    )

    assert calculate_retry_delay(seconds, 1, 5.0, 30.0, now) == 12.0
    assert calculate_retry_delay(date, 1, 5.0, 30.0, now) == 20.0


@pytest.mark.parametrize("retry_after", ["15.5", "1e1", "+12"])
def test_non_integer_retry_after_values_use_bounded_exponential_backoff(retry_after) -> None:
    error = IngestionError(
        IngestionErrorCategory.SOURCE,
        "temporary rate limit",
        retryable=True,
        http_status=429,
        retry_after=retry_after,
    )

    assert calculate_retry_delay(
        error, 1, 5.0, 30.0, datetime(2026, 1, 1, tzinfo=UTC)
    ) == 5.0


def test_retry_after_beyond_integer_conversion_limit_never_retries_early() -> None:
    retry_after = "9" * 5000
    now = datetime(2026, 1, 1, tzinfo=UTC)
    error = IngestionError(
        IngestionErrorCategory.SOURCE,
        "temporary rate limit",
        retryable=True,
        http_status=429,
        retry_after=retry_after,
    )

    assert calculate_retry_delay(error, 1, 5.0, 30.0, now) is None
    assert calculate_retry_after_deadline(retry_after, now) == datetime.max.replace(tzinfo=UTC)


def test_retry_after_beyond_cap_and_non_retryable_failure_do_not_retry() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    too_long = IngestionError(
        IngestionErrorCategory.SOURCE,
        "rate limited",
        retryable=True,
        retry_after="90",
    )
    permanent = IngestionError(IngestionErrorCategory.VALIDATION, "invalid data")

    assert calculate_retry_delay(too_long, 1, 5.0, 30.0, now) is None
    assert calculate_retry_delay(permanent, 1, 5.0, 30.0, now) is None


@pytest.mark.parametrize(
    "retry_after",
    ["3600", "Thu, 01 Jan 2026 13:00:00 GMT"],
)
def test_retry_after_deadline_is_enforced_across_cycles_and_worker_restart(retry_after) -> None:
    class ManualClock:
        def __init__(self) -> None:
            self.current = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
            self.elapsed = 0.0

        def now(self) -> datetime:
            return self.current

        def monotonic(self) -> float:
            return self.elapsed

        def wait(self, event: Event, seconds: float) -> bool:
            waits.append(seconds)
            self.current += timedelta(seconds=seconds)
            self.elapsed += seconds
            return event.is_set()

    store = FakeStateStore()
    clock = ManualClock()
    waits: list[float] = []
    request_times: list[datetime] = []

    def ingest_once():
        request_times.append(clock.now())
        if len(request_times) == 1:
            raise IngestionError(
                IngestionErrorCategory.SOURCE,
                "NESO returned HTTP 429",
                retryable=True,
                http_status=429,
                retry_after=retry_after,
            )
        return successful_result()

    config = CollectorConfig(
        region_id=13,
        poll_interval_seconds=1800,
        max_attempts=3,
        retry_base_seconds=5,
        retry_max_seconds=60,
    )
    first_worker = NesoCollector(
        config=config,
        ingest_once=ingest_once,
        state_store=store,
        now=clock.now,
        monotonic=clock.monotonic,
        wait=clock.wait,
    )

    first_run = first_worker.poll_once()
    clock.current = datetime(2026, 1, 1, 12, 30, tzinfo=UTC)
    restarted_worker = NesoCollector(
        config=config,
        ingest_once=ingest_once,
        state_store=store,
        now=clock.now,
        monotonic=clock.monotonic,
        wait=clock.wait,
    )
    next_run = restarted_worker.poll_once()

    assert first_run.status == "failed"
    assert next_run.status == "succeeded"
    assert store.latest_retry_after_record(13) is None
    assert request_times == [
        datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        datetime(2026, 1, 1, 13, 0, tzinfo=UTC),
    ]
    assert waits == [1800]


def test_shutdown_interrupts_persisted_retry_after_cooldown() -> None:
    class ManualClock:
        def __init__(self) -> None:
            self.current = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

        def now(self) -> datetime:
            return self.current

    store = FakeStateStore()
    clock = ManualClock()
    request_times: list[datetime] = []
    waits: list[float] = []
    stop = Event()

    def ingest_once():
        request_times.append(clock.now())
        raise IngestionError(
            IngestionErrorCategory.SOURCE,
            "NESO returned HTTP 429",
            retryable=True,
            http_status=429,
            retry_after="3600",
        )

    def stop_during_wait(event: Event, seconds: float) -> bool:
        waits.append(seconds)
        event.set()
        return True

    config = CollectorConfig(region_id=13, poll_interval_seconds=1800)
    first_worker = NesoCollector(
        config=config,
        ingest_once=ingest_once,
        state_store=store,
        now=clock.now,
        wait=lambda _event, seconds: False,
    )
    first_worker.poll_once()
    clock.current = datetime(2026, 1, 1, 12, 30, tzinfo=UTC)
    restarted_worker = NesoCollector(
        config=config,
        ingest_once=ingest_once,
        state_store=store,
        now=clock.now,
        wait=stop_during_wait,
    )

    result = restarted_worker.poll_once(stop)

    assert result.status == "interrupted"
    assert request_times == [datetime(2026, 1, 1, 12, 0, tzinfo=UTC)]
    assert waits == [1800]
    assert len(store.runs) == 1
    assert len(store.attempts) == 1


def test_missing_intervals_are_detected_without_fabricating_data() -> None:
    interval = timedelta(minutes=30)
    previous_start = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    previous_end = previous_start + interval

    assert count_missing_intervals(previous_start, previous_end, previous_end) == (0, None, None)
    assert count_missing_intervals(previous_start, previous_end, previous_start + 3 * interval) == (
        2,
        previous_end,
        previous_start + 3 * interval,
    )


def test_overlapping_or_invalid_previous_interval_is_not_a_gap() -> None:
    start = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    end = start + timedelta(minutes=30)

    assert count_missing_intervals(start, end, start + timedelta(minutes=15)) == (0, None, None)
    assert count_missing_intervals(end, start, start + timedelta(hours=2)) == (0, None, None)


class FakeStateStore:
    def __init__(self) -> None:
        self.runs: list[dict[str, object]] = []
        self.attempts: list[dict[str, object]] = []

    def begin_run(self, region_id: int, started_at: datetime) -> int:
        self.runs.append({"region_id": region_id, "started_at": started_at})
        return len(self.runs)

    def begin_attempt(self, run_id: int, attempt_number: int, started_at: datetime) -> int:
        self.attempts.append({
            "run_id": run_id,
            "attempt_number": attempt_number,
            "started_at": started_at,
        })
        return len(self.attempts)

    def finish_attempt(self, attempt_id: int, **values: object) -> None:
        self.attempts[attempt_id - 1].update(values)

    def finish_run(self, run_id: int, **values: object) -> None:
        self.runs[run_id - 1].update(values)

    def latest_retry_after_record(self, region_id: int) -> tuple[str, datetime] | None:
        latest_record: tuple[datetime, int, dict[str, object]] | None = None
        for index, attempt in enumerate(self.attempts):
            completed_at = attempt.get("completed_at")
            if not isinstance(completed_at, datetime):
                continue
            run = self.runs[int(attempt["run_id"]) - 1]
            candidate = (completed_at, index, attempt)
            if run["region_id"] == region_id and (
                latest_record is None or candidate[:2] > latest_record[:2]
            ):
                latest_record = candidate
        if latest_record is None:
            return None
        retry_after = latest_record[2].get("retry_after")
        if not isinstance(retry_after, str):
            return None
        return retry_after, latest_record[0]


def successful_result() -> IngestionResult:
    reading = CarbonIntensityReading(
        intensity_gco2_per_kwh=42.0,
        measurement_type="actual",
        region_id=13,
        region_name="London",
        intensity_index="low",
        observed_at=datetime(2026, 1, 1, 9, 30, tzinfo=UTC),
        valid_until=datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
        retrieved_at=datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
        interval_status="in_progress",
        interval_lag_seconds=0,
        is_fresh=True,
        age_seconds=1800,
        source_api=SOURCE_API,
        source_url="https://api.carbonintensity.org.uk/regional/regionid/13",
    )
    return IngestionResult(reading, "a" * 64, True, True)


def make_collector(ingest_once, state=None, **kwargs):
    return NesoCollector(
        config=CollectorConfig(
            region_id=13,
            poll_interval_seconds=1800,
            max_attempts=3,
            retry_base_seconds=5,
            retry_max_seconds=30,
        ),
        ingest_once=ingest_once,
        state_store=state or FakeStateStore(),
        **kwargs,
    )


def test_collector_tracks_each_transient_http_attempt_and_backoff() -> None:
    store = FakeStateStore()
    calls = 0
    delays: list[float] = []

    def ingest_once():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise IngestionError(
                IngestionErrorCategory.SOURCE,
                "service unavailable",
                retryable=True,
                http_status=503,
            )
        return successful_result()

    collector = make_collector(ingest_once, store, wait=lambda _stop, delay: delays.append(delay) or False)
    result = collector.poll_once()

    assert result.status == "succeeded"
    assert calls == 2
    assert [attempt["attempt_number"] for attempt in store.attempts] == [1, 2]
    assert [attempt["outcome"] for attempt in store.attempts] == ["source_failure", "success"]
    assert delays == [5]
    assert store.runs[0]["attempt_count"] == 2


def test_collector_respects_429_retry_after_and_stops_at_attempt_limit() -> None:
    store = FakeStateStore()
    calls = 0
    delays: list[float] = []

    def ingest_once():
        nonlocal calls
        calls += 1
        raise IngestionError(
            IngestionErrorCategory.SOURCE,
            "rate limited",
            retryable=True,
            http_status=429,
            retry_after="12",
        )

    collector = make_collector(ingest_once, store, wait=lambda _stop, delay: delays.append(delay) or False)
    result = collector.poll_once()

    assert result.status == "failed"
    assert calls == 3
    assert len(store.attempts) == 3
    assert delays == [12, 12]
    assert store.runs[0]["error_category"] == "source"


@pytest.mark.parametrize(
    ("category", "retryable"),
    [
        (IngestionErrorCategory.VALIDATION, False),
        (IngestionErrorCategory.PERSISTENCE, False),
    ],
)
def test_collector_does_not_retry_validation_or_database_failures(category, retryable) -> None:
    store = FakeStateStore()
    calls = 0

    def ingest_once():
        nonlocal calls
        calls += 1
        raise IngestionError(category, "permanent failure", retryable=retryable)

    result = make_collector(ingest_once, store).poll_once()

    assert result.status == "failed"
    assert calls == 1
    assert len(store.attempts) == 1
    assert store.attempts[0]["outcome"] == category.value + "_failure"


def test_collector_scheduling_is_sequential_and_stops_gracefully() -> None:
    store = FakeStateStore()
    calls = 0
    waits: list[float] = []
    stop = Event()

    def ingest_once():
        nonlocal calls
        calls += 1
        return successful_result()

    def wait(event, seconds):
        waits.append(seconds)
        if calls == 3:
            event.set()
            return True
        return False

    collector = make_collector(ingest_once, store, wait=wait)
    collector.run(stop)

    assert calls == 3
    assert len(store.runs) == 3
    assert waits == [1800, 1800, 1800]


def test_collector_skips_concurrent_poll_without_a_second_http_request() -> None:
    store = FakeStateStore()
    entered = Event()
    release = Event()
    calls = 0

    def ingest_once():
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(timeout=2)
        return successful_result()

    collector = make_collector(ingest_once, store)
    thread = Thread(target=collector.poll_once)
    thread.start()
    assert entered.wait(timeout=2)
    skipped = collector.poll_once()
    release.set()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert skipped.status == "skipped_concurrent"
    assert calls == 1
    assert len(store.runs) == 1


def test_graceful_shutdown_during_backoff_prevents_another_http_attempt() -> None:
    store = FakeStateStore()
    stop = Event()
    calls = 0

    def ingest_once():
        nonlocal calls
        calls += 1
        raise IngestionError(
            IngestionErrorCategory.SOURCE,
            "temporary outage",
            retryable=True,
        )

    def stop_during_wait(event, _seconds):
        event.set()
        return True

    collector = make_collector(ingest_once, store, wait=stop_during_wait)
    result = collector.poll_once(stop)

    assert result.status == "interrupted"
    assert calls == 1
    assert store.attempts[0]["outcome"] == "source_failure"
    assert store.runs[0].get("status") is None
    assert len(store.attempts) == 1


def test_shutdown_before_first_attempt_leaves_collection_run_incomplete() -> None:
    store = FakeStateStore()
    stop = Event()
    stop.set()
    calls = 0

    def ingest_once():
        nonlocal calls
        calls += 1
        return successful_result()

    result = make_collector(ingest_once, store).poll_once(stop)

    assert result.status == "interrupted"
    assert calls == 0
    assert store.attempts == []
    assert store.runs[0].get("status") is None


def test_state_persistence_failure_prevents_an_untracked_source_request() -> None:
    class BrokenStore(FakeStateStore):
        def begin_run(self, region_id, started_at):
            raise RuntimeError("database unavailable")

    calls = 0

    def ingest_once():
        nonlocal calls
        calls += 1
        return successful_result()

    result = make_collector(ingest_once, BrokenStore()).poll_once()

    assert result.status == "failed"
    assert result.error_category == "persistence"
    assert calls == 0


def test_retry_after_state_read_failure_prevents_an_unprotected_request() -> None:
    class UnavailableStateStore(FakeStateStore):
        def latest_retry_after_record(self, region_id: int):
            raise RuntimeError("database unavailable")

    calls = 0

    def ingest_once():
        nonlocal calls
        calls += 1
        return successful_result()

    store = UnavailableStateStore()
    result = make_collector(ingest_once, store).poll_once()

    assert result.status == "failed"
    assert result.error_category == "persistence"
    assert calls == 0
    assert store.runs == []


def test_stale_source_is_preserved_as_rejected_and_never_retried() -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    stale_start = now - timedelta(hours=2)
    raw_payload = {
        "data": [{
            "regionid": 13,
            "shortname": "London",
            "data": [{
                "from": stale_start.isoformat(),
                "to": (stale_start + timedelta(minutes=30)).isoformat(),
                "intensity": {"actual": 46, "forecast": 49, "index": "low"},
            }],
        }],
    }

    class StaleClient:
        region_id = 13
        max_age_seconds = 3600

        def __init__(self):
            self.calls = 0

        async def fetch_raw(self):
            self.calls += 1
            return raw_payload, now, "https://example.test/region"

    class RejectedPayloadStore:
        def __init__(self):
            self.records = []

        @contextmanager
        def transaction(self) -> Iterator[None]:
            yield

        def insert_invalid_payload(self, record: Mapping[str, Any]) -> None:
            self.records.append(record)

        def insert_reading(self, _record: Mapping[str, Any]) -> bool:
            pytest.fail("stale source data must not be normalised")

    client = StaleClient()
    repository = RejectedPayloadStore()
    state = FakeStateStore()
    collector = make_collector(
        lambda: ingest_neso(client, repository),
        state,
        now=lambda: now,
    )

    run = collector.poll_once()

    assert run.status == "failed"
    assert run.error_category == "validation"
    assert client.calls == 1
    assert len(repository.records) == 1
    assert "stale" in repository.records[0]["validation_error"]
    assert state.attempts[0]["outcome"] == "validation_failure"
