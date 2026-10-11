"""Scheduling and bounded retry policy for the standalone NESO collector."""
from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from threading import Event, Lock
from typing import Any, Protocol

import structlog

from app.services.neso_ingestion import (
    IngestionError,
    IngestionErrorCategory,
    IngestionResult,
)


@dataclass(frozen=True)
class CollectorConfig:
    region_id: int
    poll_interval_seconds: int = 1800
    max_attempts: int = 3
    retry_base_seconds: float = 5.0
    retry_max_seconds: float = 60.0

    def __post_init__(self) -> None:
        if self.region_id <= 0:
            raise ValueError("region_id must be positive")
        if self.poll_interval_seconds < 60:
            raise ValueError("poll_interval_seconds must be at least 60")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if self.retry_base_seconds <= 0 or self.retry_max_seconds < self.retry_base_seconds:
            raise ValueError("retry delays must be positive and bounded")


@dataclass(frozen=True)
class CollectorRunResult:
    status: str
    run_id: int | None
    attempt_count: int
    error_category: str | None = None


class CollectorStateStore(Protocol):
    """Persistence contract for observable collection cycles and HTTP attempts."""

    def latest_retry_after_record(self, region_id: int) -> tuple[str, datetime] | None: ...

    def begin_run(self, region_id: int, started_at: datetime) -> int: ...

    def begin_attempt(self, run_id: int, attempt_number: int, started_at: datetime) -> int: ...

    def finish_attempt(self, attempt_id: int, **values: Any) -> None: ...

    def finish_run(self, run_id: int, **values: Any) -> None: ...


class NesoCollector:
    """Serial, opt-in polling loop with one durable record per HTTP attempt."""

    def __init__(
        self,
        *,
        config: CollectorConfig,
        ingest_once: Callable[[], IngestionResult],
        state_store: CollectorStateStore,
        now: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
        wait: Callable[[Event, float], bool] | None = None,
        logger: Any | None = None,
    ) -> None:
        self.config = config
        self.ingest_once = ingest_once
        self.state_store = state_store
        self.now = now or (lambda: datetime.now(UTC))
        self.monotonic = monotonic or time.monotonic
        self.wait = wait or (lambda stop_event, seconds: stop_event.wait(seconds))
        self.logger = logger or structlog.get_logger("neso_collector")
        self._poll_lock = Lock()

    def poll_once(self, stop_event: Event | None = None) -> CollectorRunResult:
        """Run at most one cycle; overlapping in-process invocations are skipped."""
        if not self._poll_lock.acquire(blocking=False):
            self.logger.warning(
                "collector_poll_skipped_concurrent",
                region_id=self.config.region_id,
            )
            return CollectorRunResult("skipped_concurrent", None, 0)
        try:
            return self._poll_once_locked(stop_event or Event())
        finally:
            self._poll_lock.release()

    def run(self, stop_event: Event) -> None:
        """Poll immediately, then wait a fixed interval after each completed cycle."""
        self.logger.info(
            "collector_started",
            region_id=self.config.region_id,
            poll_interval_seconds=self.config.poll_interval_seconds,
            max_attempts=self.config.max_attempts,
        )
        while not stop_event.is_set():
            self.poll_once(stop_event)
            if not stop_event.is_set():
                self.wait(stop_event, self.config.poll_interval_seconds)
        self.logger.info("collector_stopped", region_id=self.config.region_id)

    def _poll_once_locked(self, stop_event: Event) -> CollectorRunResult:
        try:
            retry_after_record = self.state_store.latest_retry_after_record(self.config.region_id)
            retry_after_deadline = (
                calculate_retry_after_deadline(*retry_after_record)
                if retry_after_record is not None
                else None
            )
        except Exception as exc:  # noqa: BLE001 - never fetch without cooldown state
            self.logger.error(
                "collector_retry_after_check_failed",
                region_id=self.config.region_id,
                error_category=IngestionErrorCategory.PERSISTENCE.value,
                error_type=type(exc).__name__,
            )
            return CollectorRunResult(
                "failed", None, 0, IngestionErrorCategory.PERSISTENCE.value
            )

        now = self.now().astimezone(UTC)
        if retry_after_deadline is not None and retry_after_deadline > now:
            cooldown_seconds = (retry_after_deadline - now).total_seconds()
            self.logger.info(
                "collector_retry_after_cooldown_started",
                region_id=self.config.region_id,
                cooldown_until=retry_after_deadline.isoformat(),
                delay_seconds=cooldown_seconds,
            )
            while True:
                remaining_seconds = (
                    retry_after_deadline - self.now().astimezone(UTC)
                ).total_seconds()
                if remaining_seconds <= 0:
                    break
                if self.wait(
                    stop_event,
                    min(remaining_seconds, float(self.config.poll_interval_seconds)),
                ) or stop_event.is_set():
                    self.logger.info(
                        "collector_retry_after_cooldown_interrupted",
                        region_id=self.config.region_id,
                        cooldown_until=retry_after_deadline.isoformat(),
                    )
                    return CollectorRunResult("interrupted", None, 0, "source")
            self.logger.info(
                "collector_retry_after_cooldown_elapsed",
                region_id=self.config.region_id,
                cooldown_until=retry_after_deadline.isoformat(),
            )

        started_at = self.now().astimezone(UTC)
        started_monotonic = self.monotonic()
        try:
            run_id = self.state_store.begin_run(self.config.region_id, started_at)
        except Exception as exc:  # noqa: BLE001 - an untracked cycle must never issue a request
            self.logger.error(
                "collector_run_tracking_failed",
                region_id=self.config.region_id,
                error_category=IngestionErrorCategory.PERSISTENCE.value,
                error_type=type(exc).__name__,
            )
            return CollectorRunResult("failed", None, 0, IngestionErrorCategory.PERSISTENCE.value)

        self.logger.info(
            "collector_run_started",
            run_id=run_id,
            region_id=self.config.region_id,
            started_at=started_at.isoformat(),
        )
        attempt_count = 0
        last_error_category: str | None = None
        last_error_message: str | None = None
        interrupted_by_shutdown = False

        for attempt_number in range(1, self.config.max_attempts + 1):
            if stop_event.is_set():
                interrupted_by_shutdown = True
                break
            attempt_started_at = self.now().astimezone(UTC)
            try:
                attempt_id = self.state_store.begin_attempt(
                    run_id, attempt_number, attempt_started_at
                )
            except Exception as exc:  # noqa: BLE001 - tracking failure blocks this HTTP attempt
                self.logger.error(
                    "collector_attempt_tracking_failed",
                    run_id=run_id,
                    attempt_number=attempt_number,
                    error_category=IngestionErrorCategory.PERSISTENCE.value,
                    error_type=type(exc).__name__,
                )
                self._finish_run(
                    run_id,
                    status="failed",
                    attempt_count=attempt_count,
                    error_category=IngestionErrorCategory.PERSISTENCE.value,
                    error_message="HTTP attempt was not started because tracking persistence failed",
                    started_monotonic=started_monotonic,
                )
                return CollectorRunResult(
                    "failed", run_id, attempt_count, IngestionErrorCategory.PERSISTENCE.value
                )

            attempt_count += 1
            self.logger.info(
                "collector_http_attempt_started",
                run_id=run_id,
                attempt_id=attempt_id,
                attempt_number=attempt_number,
                max_attempts=self.config.max_attempts,
                region_id=self.config.region_id,
            )
            try:
                result = self.ingest_once()
            except IngestionError as error:
                category = error.category.value
                retry_delay = None
                if error.category is IngestionErrorCategory.SOURCE:
                    retry_delay = calculate_retry_delay(
                        error,
                        attempt_number,
                        self.config.retry_base_seconds,
                        self.config.retry_max_seconds,
                        self.now(),
                    )
                retry_scheduled = (
                    retry_delay is not None
                    and attempt_number < self.config.max_attempts
                    and not stop_event.is_set()
                )
                if (
                    retry_delay is not None
                    and attempt_number < self.config.max_attempts
                    and stop_event.is_set()
                ):
                    interrupted_by_shutdown = True
                attempt_completed_at = self.now().astimezone(UTC)
                if not self._finish_attempt(
                    attempt_id,
                    completed_at=attempt_completed_at,
                    outcome=f"{category}_failure",
                    error_category=category,
                    error_message=str(error),
                    retryable=error.retryable,
                    http_status=error.http_status,
                    retry_after=error.retry_after,
                    source_retrieved_at=error.source_retrieved_at,
                    payload_sha256=error.payload_sha256,
                    retry_delay_seconds=retry_delay if retry_scheduled else None,
                ):
                    self._finish_run(
                        run_id,
                        status="failed",
                        attempt_count=attempt_count,
                        error_category=IngestionErrorCategory.PERSISTENCE.value,
                        error_message="HTTP attempt result could not be recorded",
                        started_monotonic=started_monotonic,
                    )
                    return CollectorRunResult(
                        "failed", run_id, attempt_count, IngestionErrorCategory.PERSISTENCE.value
                    )

                self.logger.error(
                    "collector_http_attempt_failed",
                    run_id=run_id,
                    attempt_id=attempt_id,
                    attempt_number=attempt_number,
                    error_category=category,
                    error_message=str(error),
                    retryable=error.retryable,
                    http_status=error.http_status,
                    retry_delay_seconds=retry_delay if retry_scheduled else None,
                )
                last_error_category = category
                last_error_message = str(error)
                if retry_scheduled:
                    assert retry_delay is not None
                    self.logger.warning(
                        "collector_retry_scheduled",
                        run_id=run_id,
                        failed_attempt=attempt_number,
                        next_attempt=attempt_number + 1,
                        delay_seconds=retry_delay,
                    )
                    if self.wait(stop_event, retry_delay):
                        interrupted_by_shutdown = True
                        break
                    continue
                break
            except Exception as error:
                category = "internal"
                message = "Unexpected collector failure"
                if not self._finish_attempt(
                    attempt_id,
                    completed_at=self.now().astimezone(UTC),
                    outcome="internal_failure",
                    error_category=category,
                    error_message=message,
                    retryable=False,
                ):
                    category = IngestionErrorCategory.PERSISTENCE.value
                    message = "HTTP attempt result could not be recorded"
                self.logger.exception(
                    "collector_http_attempt_failed",
                    run_id=run_id,
                    attempt_id=attempt_id,
                    attempt_number=attempt_number,
                    error_category=category,
                    error_type=type(error).__name__,
                )
                last_error_category = category
                last_error_message = message
                break

            if not self._finish_attempt(
                attempt_id,
                completed_at=self.now().astimezone(UTC),
                outcome="success" if result.inserted else "duplicate",
                error_category=None,
                error_message=None,
                retryable=False,
                result=result,
            ):
                self._finish_run(
                    run_id,
                    status="failed",
                    attempt_count=attempt_count,
                    error_category=IngestionErrorCategory.PERSISTENCE.value,
                    error_message="Successful ingestion outcome could not be recorded",
                    started_monotonic=started_monotonic,
                )
                return CollectorRunResult(
                    "failed", run_id, attempt_count, IngestionErrorCategory.PERSISTENCE.value
                )

            duration_seconds = max(0.0, self.monotonic() - started_monotonic)
            if not self._finish_run(
                run_id,
                status="succeeded",
                attempt_count=attempt_count,
                error_category=None,
                error_message=None,
                started_monotonic=started_monotonic,
                duration_seconds=duration_seconds,
                result=result,
            ):
                return CollectorRunResult(
                    "failed", run_id, attempt_count, IngestionErrorCategory.PERSISTENCE.value
                )
            reading = result.reading
            self.logger.info(
                "collector_run_succeeded",
                run_id=run_id,
                region_id=self.config.region_id,
                duration_seconds=duration_seconds,
                payload_sha256=result.payload_sha256,
                source_retrieved_at=reading.retrieved_at.isoformat(),
                interval_start=reading.observed_at.isoformat(),
                interval_end=reading.valid_until.isoformat(),
                measurement_type=reading.measurement_type,
                source_fresh=reading.is_fresh,
                raw_evidence_inserted=result.raw_payload_inserted,
                observation_inserted=result.inserted,
                database_outcome="inserted" if result.inserted else "duplicate",
            )
            return CollectorRunResult("succeeded", run_id, attempt_count)

        if interrupted_by_shutdown:
            self.logger.info(
                "collector_run_interrupted",
                run_id=run_id,
                region_id=self.config.region_id,
                attempts=attempt_count,
                last_error_category=last_error_category,
            )
            return CollectorRunResult(
                "interrupted", run_id, attempt_count, last_error_category
            )

        if last_error_category is None:
            last_error_category = IngestionErrorCategory.SOURCE.value
            last_error_message = "Collector stopped before an HTTP attempt completed"
        duration_seconds = max(0.0, self.monotonic() - started_monotonic)
        self._finish_run(
            run_id,
            status="failed",
            attempt_count=attempt_count,
            error_category=last_error_category,
            error_message=last_error_message,
            started_monotonic=started_monotonic,
            duration_seconds=duration_seconds,
        )
        self.logger.error(
            "collector_run_failed",
            run_id=run_id,
            region_id=self.config.region_id,
            duration_seconds=duration_seconds,
            attempts=attempt_count,
            error_category=last_error_category,
            error_message=last_error_message,
        )
        return CollectorRunResult("failed", run_id, attempt_count, last_error_category)

    def _finish_attempt(self, attempt_id: int, **values: Any) -> bool:
        try:
            self.state_store.finish_attempt(attempt_id, **values)
        except Exception as exc:  # noqa: BLE001 - report status persistence failures explicitly
            self.logger.error(
                "collector_attempt_tracking_failed",
                attempt_id=attempt_id,
                error_category=IngestionErrorCategory.PERSISTENCE.value,
                error_type=type(exc).__name__,
            )
            return False
        return True

    def _finish_run(
        self,
        run_id: int,
        *,
        status: str,
        attempt_count: int,
        error_category: str | None,
        error_message: str | None,
        started_monotonic: float,
        duration_seconds: float | None = None,
        result: IngestionResult | None = None,
    ) -> bool:
        try:
            self.state_store.finish_run(
                run_id,
                completed_at=self.now().astimezone(UTC),
                status=status,
                attempt_count=attempt_count,
                error_category=error_category,
                error_message=error_message,
                duration_seconds=(
                    duration_seconds
                    if duration_seconds is not None
                    else max(0.0, self.monotonic() - started_monotonic)
                ),
                result=result,
            )
        except Exception as exc:  # noqa: BLE001 - report status persistence failures explicitly
            self.logger.error(
                "collector_run_tracking_failed",
                run_id=run_id,
                error_category=IngestionErrorCategory.PERSISTENCE.value,
                error_type=type(exc).__name__,
            )
            return False
        return True


def calculate_retry_after_deadline(
    value: str,
    received_at: datetime,
) -> datetime | None:
    """Resolve a stored Retry-After value to its absolute UTC deadline."""
    if received_at.tzinfo is None or received_at.utcoffset() is None:
        raise ValueError("received_at must include a timezone")
    received_at = received_at.astimezone(UTC)
    seconds = _retry_after_seconds(value, received_at)
    if seconds is None:
        return None
    try:
        return received_at + timedelta(seconds=seconds)
    except (OverflowError, ValueError):
        return datetime.max.replace(tzinfo=UTC)


def calculate_retry_delay(
    error: IngestionError,
    attempt_number: int,
    base_delay_seconds: float,
    max_delay_seconds: float,
    now: datetime,
) -> float | None:
    """Return a bounded exponential delay, or ``None`` when retry is unsafe."""
    if not error.retryable:
        return None
    if attempt_number < 1:
        raise ValueError("attempt_number must be positive")
    if base_delay_seconds <= 0 or max_delay_seconds <= 0:
        raise ValueError("retry delays must be positive")
    if max_delay_seconds < base_delay_seconds:
        raise ValueError("maximum retry delay must be at least the base delay")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must include a timezone")

    exponential = min(base_delay_seconds * (2 ** (attempt_number - 1)), max_delay_seconds)
    if not math.isfinite(exponential):
        exponential = max_delay_seconds
    retry_after_seconds = _retry_after_seconds(error.retry_after, now)
    if retry_after_seconds is not None and retry_after_seconds > max_delay_seconds:
        # Never violate a long server Retry-After by retrying early. Leave the
        # next attempt to the next scheduled collection cycle instead.
        return None
    return min(max_delay_seconds, max(exponential, retry_after_seconds or 0.0))


def _retry_after_seconds(value: str | None, now: datetime) -> int | float | None:
    if value is None:
        return None
    value = value.strip()
    if value.isascii() and value.isdigit():
        try:
            return int(value)
        except ValueError:
            # Preserve an unrepresentably large valid delta instead of
            # falling back to a shorter exponential retry.
            return math.inf
    try:
        retry_at = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if retry_at.tzinfo is None or retry_at.utcoffset() is None:
        retry_at = retry_at.replace(tzinfo=UTC)
    seconds = (retry_at.astimezone(UTC) - now.astimezone(UTC)).total_seconds()
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return seconds


def count_missing_intervals(
    previous_start: datetime,
    previous_end: datetime,
    current_start: datetime,
) -> tuple[int, datetime | None, datetime | None]:
    """Count complete source intervals skipped between two successful records.

    The collector reports gaps only; it does not synthesise observations for
    missing periods.
    """
    if any(value.tzinfo is None or value.utcoffset() is None for value in (
        previous_start, previous_end, current_start
    )):
        raise ValueError("interval boundaries must include timezones")
    interval = previous_end - previous_start
    gap = current_start - previous_end
    if interval <= timedelta(0) or gap <= timedelta(0):
        return 0, None, None
    missing_count = int(gap // interval)
    if missing_count == 0:
        return 0, None, None
    return missing_count, previous_end, current_start
