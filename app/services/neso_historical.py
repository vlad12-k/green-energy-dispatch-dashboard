"""Controlled acquisition of the official NESO regional forecast archive."""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from app.services.intensity_units import HISTORICAL_NESO_SOURCE_API as DATASET_API

DATASET_ID = "bf3d723b-b4ae-4ec7-9fc9-e584c651a9da"
RESOURCE_ID = "c16b0e19-c02a-44a8-ba05-4db2c0545a2a"
DATASTORE_SQL_URL = "https://api.neso.energy/api/3/action/datastore_search_sql"
MAX_WINDOW = timedelta(days=14)
INTERVAL = timedelta(minutes=30)
REGION_COLUMNS = {
    1: "North Scotland",
    2: "South Scotland",
    3: "North West England",
    4: "North East England",
    5: "Yorkshire",
    6: "North Wales and Merseyside",
    7: "South Wales",
    8: "West Midlands",
    9: "East Midlands",
    10: "East England",
    11: "South West England",
    12: "South England",
    13: "London",
    14: "South East England",
}


class HistoricalSourceError(RuntimeError):
    """An upstream source or response failed the acquisition contract."""


@dataclass(frozen=True)
class HistoricalForecastObservation:
    source_record_id: int
    region_id: int
    region_name: str
    observed_at: datetime
    valid_until: datetime
    retrieved_at: datetime
    intensity_gco2_per_kwh: float
    measurement_type: str = "forecast"
    original_forecast_issue_at: datetime | None = None


@dataclass(frozen=True)
class RejectedHistoricalRecord:
    record_index: int
    source_record_id: int | None
    reason: str


@dataclass(frozen=True)
class HistoricalAcquisitionReport:
    source_api: str
    source_dataset_id: str
    source_resource_id: str
    source_dataset_version: str | None
    source_url: str
    units: str
    region_id: int
    region_name: str
    requested_start: datetime
    requested_end: datetime
    retrieved_start: datetime | None
    retrieved_end: datetime | None
    acquired_at: datetime
    payload_sha256: str
    response_sha256: str
    actual_count: int
    forecast_count: int
    missing_intervals: tuple[datetime, ...]
    coverage_fraction: float
    coverage_complete: bool
    duplicate_count: int
    conflicting_count: int
    rejected_records: tuple[RejectedHistoricalRecord, ...]
    observations: tuple[HistoricalForecastObservation, ...]
    temporal_evaluation_supported: bool
    temporal_evaluation_reason: str


@dataclass(frozen=True)
class HistoricalAcquisition:
    report: HistoricalAcquisitionReport
    payload: dict[str, Any]


def parse_historical_response(
    payload: Any,
    *,
    region_id: int,
    requested_start: datetime,
    requested_end: datetime,
    acquired_at: datetime,
    source_url: str,
    response_sha256: str | None = None,
) -> HistoricalAcquisitionReport:
    """Parse one bounded CKAN response; archive rows remain forecasts, never actuals."""
    _validate_window(region_id, requested_start, requested_end)
    acquired_utc = _aware_utc(acquired_at, "acquired_at")
    start_utc = _aware_utc(requested_start, "requested_start")
    end_utc = _aware_utc(requested_end, "requested_end")
    if not isinstance(source_url, str) or not source_url.startswith("https://"):
        raise ValueError("source_url must use HTTPS")
    if not isinstance(payload, Mapping) or payload.get("success") is not True:
        raise HistoricalSourceError("NESO Data Portal response did not report success")
    result = payload.get("result")
    if not isinstance(result, Mapping):
        raise HistoricalSourceError("NESO response has an unexpected result envelope")
    returned_resource = result.get("resource_id")
    if returned_resource is not None and returned_resource != RESOURCE_ID:
        raise HistoricalSourceError("NESO response has an unexpected dataset resource")
    echoed_window = requested_window_from_response(payload, region_id=region_id)
    if returned_resource is None and echoed_window != (start_utc, end_utc):
        raise HistoricalSourceError(
            "NESO response does not identify the requested dataset query"
        )
    if echoed_window is not None and echoed_window != (start_utc, end_utc):
        raise HistoricalSourceError(
            "NESO response query bounds do not match the requested UTC window"
        )
    records = result.get("records")
    if not isinstance(records, list):
        raise HistoricalSourceError("NESO response records must be a list")
    fields = result.get("fields")
    if not isinstance(fields, list) or any(
        not isinstance(field, Mapping) or not isinstance(field.get("id"), str)
        for field in fields
    ):
        raise HistoricalSourceError(
            "NESO response fields must describe a valid table schema"
        )

    region_name = REGION_COLUMNS[region_id]
    field_names = {field["id"] for field in fields}
    if not {"_id", "datetime", region_name} <= field_names:
        raise HistoricalSourceError(
            "NESO response does not contain the requested region schema"
        )
    by_time: dict[datetime, list[tuple[int, float, int]]] = defaultdict(list)
    rejected: list[RejectedHistoricalRecord] = []
    for index, item in enumerate(records):
        record_id: int | None = None
        try:
            if not isinstance(item, Mapping):
                raise ValueError("record must be an object")
            record_id = _record_id(item.get("_id"))
            observed_at = _source_timestamp(item.get("datetime"))
            if not start_utc <= observed_at < end_utc:
                raise ValueError("record timestamp is outside the requested UTC window")
            if (
                observed_at.second
                or observed_at.microsecond
                or observed_at.minute not in (0, 30)
            ):
                raise ValueError(
                    "record timestamp is not aligned to the UTC half-hour grid"
                )
            value = _intensity(item.get(region_name))
            by_time[observed_at].append((record_id, value, index))
        except (InvalidOperation, TypeError, ValueError) as exc:
            rejected.append(RejectedHistoricalRecord(index, record_id, str(exc)))

    duplicate_count = 0
    conflicting_count = 0
    accepted: list[HistoricalForecastObservation] = []
    for observed_at, revisions in sorted(by_time.items()):
        values = {value for _, value, _ in revisions}
        if len(revisions) > 1 and len(values) == 1:
            duplicate_count += len(revisions) - 1
            source_id, value, _ = max(revisions, key=lambda row: row[0])
        elif len(values) > 1:
            conflicting_count += len(revisions) - 1
            for source_id, _, index in revisions:
                rejected.append(
                    RejectedHistoricalRecord(
                        index,
                        source_id,
                        "conflicting forecast values share one observation timestamp",
                    )
                )
            continue
        else:
            source_id, value, _ = revisions[0]
        accepted.append(
            HistoricalForecastObservation(
                source_record_id=source_id,
                region_id=region_id,
                region_name=region_name,
                observed_at=observed_at,
                valid_until=observed_at + INTERVAL,
                retrieved_at=acquired_utc,
                intensity_gco2_per_kwh=value,
            )
        )

    expected = _interval_starts(start_utc, end_utc)
    accepted_starts = {row.observed_at for row in accepted}
    payload_digest = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    max_observed_at = max(accepted_starts, default=None)
    return HistoricalAcquisitionReport(
        source_api=DATASET_API,
        source_dataset_id=DATASET_ID,
        source_resource_id=RESOURCE_ID,
        source_dataset_version=None,
        source_url=source_url,
        units="gCO2/kWh",
        region_id=region_id,
        region_name=region_name,
        requested_start=start_utc,
        requested_end=end_utc,
        retrieved_start=min(accepted_starts, default=None),
        retrieved_end=max_observed_at + INTERVAL
        if max_observed_at is not None
        else None,
        acquired_at=acquired_utc,
        payload_sha256=payload_digest,
        response_sha256=response_sha256 or payload_digest,
        actual_count=0,
        forecast_count=len(accepted),
        missing_intervals=tuple(
            point for point in expected if point not in accepted_starts
        ),
        coverage_fraction=len(accepted_starts) / len(expected),
        coverage_complete=accepted_starts == set(expected),
        duplicate_count=duplicate_count,
        conflicting_count=conflicting_count,
        rejected_records=tuple(sorted(rejected, key=lambda row: row.record_index)),
        observations=tuple(accepted),
        temporal_evaluation_supported=False,
        temporal_evaluation_reason=(
            "the selected NESO regional archive contains forecast-only rows and no "
            "original forecast issue timestamps; retrospective forecasts cannot be "
            "used as as-of-time evaluation inputs, and no regional actuals are supplied"
        ),
    )


class DataPortalHistoricalClient:
    """Read one small, UTC-bounded region window from NESO's public CKAN archive."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 10.0,
        max_attempts: int = 3,
        minimum_request_interval_seconds: float = 1.0,
        max_retry_after_seconds: float = 300.0,
        max_response_bytes: int = 1_000_000,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if isinstance(max_attempts, bool) or not 1 <= max_attempts <= 5:
            raise ValueError("max_attempts must be between one and five")
        if (
            minimum_request_interval_seconds < 0
            or max_retry_after_seconds <= 0
            or isinstance(max_response_bytes, bool)
            or not isinstance(max_response_bytes, int)
            or max_response_bytes <= 0
        ):
            raise ValueError(
                "request pacing, Retry-After limit and response size are invalid"
            )
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self.minimum_request_interval_seconds = minimum_request_interval_seconds
        self.max_retry_after_seconds = max_retry_after_seconds
        self.max_response_bytes = max_response_bytes
        self.transport = transport
        self.sleep = sleep
        self.clock = clock

    def acquire(
        self,
        *,
        region_id: int,
        requested_start: datetime,
        requested_end: datetime,
    ) -> HistoricalAcquisition:
        _validate_window(region_id, requested_start, requested_end)
        start_utc = _aware_utc(requested_start, "requested_start")
        end_utc = _aware_utc(requested_end, "requested_end")
        sql = _query_sql(region_id, start_utc, end_utc)
        headers = {
            "Accept": "application/json",
            "User-Agent": "GreenEnergyDispatchDashboard/1.0",
        }
        with httpx.Client(
            timeout=self.timeout_seconds,
            transport=self.transport,
            follow_redirects=False,
        ) as client:
            for attempt in range(1, self.max_attempts + 1):
                if self.minimum_request_interval_seconds:
                    self.sleep(self.minimum_request_interval_seconds)
                try:
                    with client.stream(
                        "GET", DATASTORE_SQL_URL, params={"sql": sql}, headers=headers
                    ) as response:
                        status = response.status_code
                        if status == 429 or 500 <= status <= 599:
                            if attempt == self.max_attempts:
                                raise HistoricalSourceError(
                                    f"NESO Data Portal returned retryable HTTP {status}"
                                )
                            delay = _retry_delay(
                                response.headers.get("Retry-After"),
                                self.clock(),
                                attempt,
                            )
                            if delay > self.max_retry_after_seconds:
                                raise HistoricalSourceError(
                                    f"Retry-After requires {delay:g}s, exceeding the safe wait limit; no retry was made"
                                )
                            self.sleep(delay)
                            continue
                        if not 200 <= status <= 299:
                            raise HistoricalSourceError(
                                f"NESO Data Portal returned HTTP {status}"
                            )
                        response_body = bytearray()
                        for chunk in response.iter_bytes():
                            response_body.extend(chunk)
                            if len(response_body) > self.max_response_bytes:
                                raise HistoricalSourceError(
                                    f"NESO Data Portal response exceeded {self.max_response_bytes} bytes"
                                )
                        try:
                            payload = json.loads(response_body)
                        except (ValueError, UnicodeDecodeError) as exc:
                            raise HistoricalSourceError(
                                "NESO Data Portal returned invalid JSON"
                            ) from exc
                        source_url = str(response.url)
                except (httpx.TimeoutException, httpx.RequestError) as exc:
                    if attempt == self.max_attempts:
                        raise HistoricalSourceError(
                            "NESO Data Portal request failed after bounded retries"
                        ) from exc
                    self.sleep(float(min(2 ** (attempt - 1), 30)))
                    continue
                acquired_at = _aware_utc(self.clock(), "clock")
                response_hash = hashlib.sha256(response_body).hexdigest()
                report = parse_historical_response(
                    payload,
                    region_id=region_id,
                    requested_start=start_utc,
                    requested_end=end_utc,
                    acquired_at=acquired_at,
                    source_url=source_url,
                    response_sha256=response_hash,
                )
                return HistoricalAcquisition(report=report, payload=payload)
        raise HistoricalSourceError("NESO Data Portal request could not be completed")


def _query_sql(region_id: int, start: datetime, end: datetime) -> str:
    region_column = REGION_COLUMNS[region_id].replace('"', '""')
    start_literal = _aware_utc(start, "requested_start").strftime("%Y-%m-%dT%H:%M:%S")
    end_literal = _aware_utc(end, "requested_end").strftime("%Y-%m-%dT%H:%M:%S")
    return (
        f'SELECT _id, datetime, "{region_column}" FROM "{RESOURCE_ID}" '
        f"WHERE datetime >= '{start_literal}' AND datetime < '{end_literal}' "
        "ORDER BY datetime, _id"
    )


def requested_window_from_response(
    payload: Mapping[str, Any], *, region_id: int
) -> tuple[datetime, datetime] | None:
    """Read and validate the exact UTC window echoed by the CKAN SQL endpoint."""
    result = payload.get("result")
    sql = result.get("sql") if isinstance(result, Mapping) else None
    if sql is None:
        return None
    if not isinstance(sql, str):
        raise HistoricalSourceError("NESO response query echo is not text")
    region_column = REGION_COLUMNS.get(region_id)
    if region_column is None:
        raise HistoricalSourceError("NESO response identifies an unsupported region")
    prefix = (
        f'SELECT _id, datetime, "{region_column}" FROM "{RESOURCE_ID}" '
        "WHERE datetime >= "
    )
    suffix = " ORDER BY datetime, _id"
    if not sql.startswith(prefix) or not sql.endswith(suffix):
        raise HistoricalSourceError("NESO response query echo has an unexpected shape")
    bounds = sql[len(prefix) : -len(suffix)]
    parts = bounds.split("' AND datetime < '")
    if len(parts) != 2 or not parts[0].startswith("'") or not parts[1].endswith("'"):
        raise HistoricalSourceError("NESO response query bounds are malformed")
    try:
        start = datetime.strptime(parts[0][1:], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)
        end = datetime.strptime(parts[1][:-1], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)
    except ValueError as exc:
        raise HistoricalSourceError("NESO response query bounds are malformed") from exc
    _validate_window(region_id, start, end)
    if sql != _query_sql(region_id, start, end):
        raise HistoricalSourceError("NESO response query echo is not canonical")
    return start, end


def _validate_window(region_id: int, start: datetime, end: datetime) -> None:
    if isinstance(region_id, bool) or region_id not in REGION_COLUMNS:
        raise ValueError("region_id must identify a regional archive column (1-14)")
    start_utc = _aware_utc(start, "requested_start")
    end_utc = _aware_utc(end, "requested_end")
    if end_utc <= start_utc:
        raise ValueError("requested_end must be after requested_start")
    if end_utc - start_utc > MAX_WINDOW:
        raise ValueError("one acquisition window may not exceed 14 days")
    for point in (start_utc, end_utc):
        if point.second or point.microsecond or point.minute not in (0, 30):
            raise ValueError("window boundaries must align to the UTC half-hour grid")


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value.astimezone(UTC)


def _source_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("datetime must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("datetime must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        # NESO's DataStore schema documents this field as UTC but serialises it without Z.
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _record_id(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("_id must be a positive integer")
    try:
        record_id = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("_id must be a positive integer") from exc
    if record_id <= 0 or str(record_id) != str(value):
        raise ValueError("_id must be a positive integer")
    return record_id


def _intensity(value: Any) -> float:
    if isinstance(value, bool) or value is None:
        raise ValueError("regional forecast value must be numeric")
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("regional forecast value must be numeric") from exc
    if not decimal_value.is_finite() or decimal_value < 0:
        raise ValueError("regional forecast value must be finite and non-negative")
    number = float(decimal_value)
    if not math.isfinite(number):
        raise ValueError("regional forecast value must be finite and non-negative")
    return number


def _interval_starts(start: datetime, end: datetime) -> tuple[datetime, ...]:
    count = int((end - start) / INTERVAL)
    return tuple(start + INTERVAL * index for index in range(count))


def _canonical_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _retry_delay(retry_after: str | None, now: datetime, attempt: int) -> float:
    if retry_after:
        if retry_after.isascii() and retry_after.isdigit():
            return float(int(retry_after))
        try:
            retry_at = parsedate_to_datetime(retry_after)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=UTC)
            return max(
                0.0,
                (retry_at.astimezone(UTC) - _aware_utc(now, "clock")).total_seconds(),
            )
        except (TypeError, ValueError, OverflowError):
            pass
    return float(min(2 ** (attempt - 1), 30))
