"""Deterministic historical NESO datasets and leakage-safe reference baselines."""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Iterable, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.persistence.models import CarbonObservation, RawPayload

MeasurementType = Literal["actual", "forecast"]


@dataclass(frozen=True)
class DatasetObservation:
    region_id: int
    region_name: str
    observed_at: datetime
    valid_until: datetime
    retrieved_at: datetime
    measurement_type: MeasurementType
    intensity_gco2_per_kwh: float
    source_api: str
    source_url: str
    payload_sha256: str
    raw_payload_id: int
    accepted: bool = True

    def __post_init__(self) -> None:
        if self.valid_until <= self.observed_at:
            raise ValueError("valid_until must be after observed_at")
        if self.measurement_type not in ("actual", "forecast"):
            raise ValueError("measurement_type must be actual or forecast")
        if (
            not math.isfinite(self.intensity_gco2_per_kwh)
            or self.intensity_gco2_per_kwh < 0
        ):
            raise ValueError("intensity must be finite and non-negative")
        if len(self.payload_sha256) != 64:
            raise ValueError("payload_sha256 must be a 64-character digest")

    def utc(self) -> DatasetObservation:
        for name in ("observed_at", "valid_until", "retrieved_at"):
            value = getattr(self, name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must include a timezone")
        return DatasetObservation(
            region_id=self.region_id,
            region_name=self.region_name,
            observed_at=self.observed_at.astimezone(UTC),
            valid_until=self.valid_until.astimezone(UTC),
            retrieved_at=self.retrieved_at.astimezone(UTC),
            measurement_type=self.measurement_type,
            intensity_gco2_per_kwh=self.intensity_gco2_per_kwh,
            source_api=self.source_api,
            source_url=self.source_url,
            payload_sha256=self.payload_sha256,
            raw_payload_id=self.raw_payload_id,
            accepted=self.accepted,
        )


@dataclass(frozen=True)
class HistoricalDataset:
    region_id: int
    observations: tuple[DatasetObservation, ...]
    actual_revision_evidence: tuple[DatasetObservation, ...]
    forecast_observations: tuple[DatasetObservation, ...]
    forecast_count: int
    rejected_observation_count: int
    duplicate_observation_count: int
    conflicting_revision_count: int
    missing_intervals: tuple[datetime, ...]
    interval_seconds: int
    first_observed_at: datetime | None
    last_observed_at: datetime | None
    available_forecast_count: int
    selection_rule: str = "accepted actual records only; latest retrieved_at wins per UTC interval, then greatest payload SHA-256 and raw payload ID"

    @property
    def dataset_sha256(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()

    def to_json(self) -> str:
        body = {
            "region_id": self.region_id,
            "units": "gCO2e/kWh",
            "interval_seconds": self.interval_seconds,
            "first_observed_at": _iso(self.first_observed_at)
            if self.first_observed_at
            else None,
            "last_observed_at": _iso(self.last_observed_at)
            if self.last_observed_at
            else None,
            "available_forecast_count": self.available_forecast_count,
            "actual_revision_evidence": [
                _observation_json(row) for row in self.actual_revision_evidence
            ],
            "forecast_observations": [
                _observation_json(row) for row in self.forecast_observations
            ],
            "selection_rule": self.selection_rule,
            "forecast_observation_count": self.forecast_count,
            "rejected_observation_count": self.rejected_observation_count,
            "duplicate_observation_count": self.duplicate_observation_count,
            "conflicting_revision_count": self.conflicting_revision_count,
            "missing_intervals": [_iso(value) for value in self.missing_intervals],
            "observations": [_observation_json(row) for row in self.observations],
        }
        return json.dumps(
            body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )


@dataclass(frozen=True)
class AvailabilityReport:
    region_id: int
    first_interval_start: datetime | None
    last_interval_end: datetime | None
    total_available_observations: int
    actual_observation_count: int
    forecast_observation_count: int
    actual_coverage_start: datetime | None
    actual_coverage_end: datetime | None
    forecast_coverage_start: datetime | None
    forecast_coverage_end: datetime | None
    rejected_observation_count: int
    duplicate_observation_count: int
    conflicting_revision_count: int
    missing_interval_count: int
    contiguous_actual_intervals: int
    contiguous_actual_segment_count: int
    contiguous_actual_periods: tuple[ContiguousPeriod, ...]
    longest_contiguous_actual_intervals: int
    suitable_for_evaluation: bool
    assessment: str


@dataclass(frozen=True)
class ContiguousPeriod:
    start_at: datetime
    end_at: datetime
    interval_count: int


@dataclass(frozen=True)
class TemporalSplit:
    train: tuple[DatasetObservation, ...]
    test: tuple[DatasetObservation, ...]
    split_at: datetime | None


@dataclass(frozen=True)
class BaselineMetric:
    method: str
    horizon_intervals: int
    sample_count: int
    mae: float
    rmse: float
    first_target_at: datetime
    last_target_at: datetime
    skipped_missing_input_count: int


@dataclass(frozen=True)
class EvaluationReport:
    region_id: int | None
    dataset_origin: str
    sufficient_data: bool
    insufficient_reason: str | None
    train_start: datetime | None
    train_end: datetime | None
    test_start: datetime | None
    test_end: datetime | None
    results: tuple[BaselineMetric, ...]


class HistoricalDatasetRepository:
    """Read normalised observations and accepted-evidence provenance."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def observations(self, region_id: int) -> tuple[DatasetObservation, ...]:
        if region_id <= 0:
            raise ValueError("region_id must be positive")
        statement = (
            select(CarbonObservation, RawPayload)
            .join(RawPayload, CarbonObservation.raw_payload_id == RawPayload.id)
            .where(
                CarbonObservation.region_id == region_id,
                RawPayload.validation_error.is_(None),
            )
            .order_by(
                CarbonObservation.observed_at,
                CarbonObservation.valid_until,
                CarbonObservation.retrieved_at,
                CarbonObservation.id,
            )
        )
        return tuple(
            _from_models(observation, raw)
            for observation, raw in self.session.execute(statement).all()
        )

    def rejected_count(self, region_id: int) -> int:
        if region_id <= 0:
            raise ValueError("region_id must be positive")
        from sqlalchemy import func

        return int(
            self.session.scalar(
                select(func.count(RawPayload.id)).where(
                    RawPayload.region_id == region_id,
                    RawPayload.validation_error.is_not(None),
                )
            )
            or 0
        )


def build_dataset(
    observations: Iterable[DatasetObservation],
    *,
    region_id: int,
    interval: timedelta = timedelta(minutes=30),
    rejected_observation_count: int | None = None,
) -> HistoricalDataset:
    if region_id <= 0:
        raise ValueError("region_id must be positive")
    if interval <= timedelta(0):
        raise ValueError("interval must be positive")
    interval_seconds = int(interval.total_seconds())
    if interval_seconds <= 0 or interval_seconds != interval.total_seconds():
        raise ValueError("interval must be an integral number of seconds")

    all_rows = [row.utc() for row in observations]
    if any(row.region_id != region_id for row in all_rows):
        raise ValueError("all observations must belong to the requested region")
    for row in all_rows:
        if (
            row.valid_until - row.observed_at != interval
            or int(row.observed_at.timestamp()) % interval_seconds != 0
        ):
            raise ValueError(
                "observation intervals must align to the configured UTC interval grid"
            )
    rejected_count = (
        sum(not row.accepted for row in all_rows)
        if rejected_observation_count is None
        else rejected_observation_count
    )
    if rejected_count < 0:
        raise ValueError("rejected_observation_count must be non-negative")
    counted_rows = all_rows

    forecast_count = len(
        {
            (row.observed_at, row.valid_until, row.retrieved_at, row.payload_sha256)
            for row in counted_rows
            if row.accepted and row.measurement_type == "forecast"
        }
    )
    eligible = [
        row for row in all_rows if row.accepted and row.measurement_type == "actual"
    ]
    selected_forecasts = tuple(
        sorted(
            {
                (
                    row.observed_at,
                    row.valid_until,
                    row.retrieved_at,
                    row.payload_sha256,
                ): row
                for row in counted_rows
                if row.accepted and row.measurement_type == "forecast"
            }.values(),
            key=lambda row: (
                row.observed_at,
                row.valid_until,
                row.retrieved_at,
                row.payload_sha256,
            ),
        )
    )
    groups: dict[tuple[datetime, datetime], list[DatasetObservation]] = defaultdict(
        list
    )
    for row in eligible:
        groups[(row.observed_at, row.valid_until)].append(row)

    selected: list[DatasetObservation] = []
    duplicate_count = 0
    revision_count = 0
    for key in sorted(groups):
        revisions = groups[key]
        distinct_versions = {
            (row.intensity_gco2_per_kwh, row.payload_sha256) for row in revisions
        }
        if len(revisions) > 1:
            duplicate_count += len(revisions) - 1
        if len(distinct_versions) > 1:
            revision_count += len(distinct_versions) - 1
        selected.append(
            max(
                revisions,
                key=lambda row: (
                    row.retrieved_at,
                    row.payload_sha256,
                    row.raw_payload_id,
                ),
            )
        )

    selected.sort(
        key=lambda row: (
            row.observed_at,
            row.valid_until,
            row.retrieved_at,
            row.payload_sha256,
        )
    )
    actual_revision_evidence = tuple(
        sorted(
            eligible,
            key=lambda row: (
                row.observed_at,
                row.valid_until,
                row.retrieved_at,
                row.payload_sha256,
                row.raw_payload_id,
            ),
        )
    )
    starts = {row.observed_at for row in selected}
    missing: list[datetime] = []
    if selected:
        ordered_starts = sorted(starts)
        for left, right in zip(ordered_starts, ordered_starts[1:]):
            delta = right - left
            if delta.total_seconds() > interval_seconds:
                count = int(delta.total_seconds() // interval_seconds) - 1
                missing.extend(
                    left + interval * offset for offset in range(1, count + 1)
                )

    return HistoricalDataset(
        region_id=region_id,
        observations=tuple(selected),
        actual_revision_evidence=actual_revision_evidence,
        forecast_observations=selected_forecasts,
        forecast_count=forecast_count,
        rejected_observation_count=rejected_count,
        duplicate_observation_count=duplicate_count,
        conflicting_revision_count=revision_count,
        missing_intervals=tuple(missing),
        interval_seconds=interval_seconds,
        first_observed_at=min(
            (row.observed_at for row in selected + list(selected_forecasts)),
            default=None,
        ),
        last_observed_at=max(
            (row.valid_until for row in selected + list(selected_forecasts)),
            default=None,
        ),
        available_forecast_count=len(selected_forecasts),
    )


def load_dataset(session: Session, region_id: int) -> HistoricalDataset:
    repository = HistoricalDatasetRepository(session)
    return build_dataset(
        repository.observations(region_id),
        region_id=region_id,
        rejected_observation_count=repository.rejected_count(region_id),
    )


def assess_availability(
    dataset: HistoricalDataset,
    *,
    minimum_contiguous_intervals: int = 96,
) -> AvailabilityReport:
    if minimum_contiguous_intervals < 2:
        raise ValueError("minimum_contiguous_intervals must be at least two")
    if dataset.interval_seconds != 1800:
        raise ValueError("availability assessment requires a 30-minute dataset cadence")
    actual = dataset.observations
    forecast_rows = dataset.forecast_observations
    if any(
        (row.valid_until - row.observed_at).total_seconds() != dataset.interval_seconds
        for row in (*actual, *forecast_rows)
    ):
        raise ValueError(
            "observation interval duration does not match the dataset cadence"
        )
    if any(
        int(row.observed_at.timestamp()) % dataset.interval_seconds != 0
        for row in (*actual, *forecast_rows)
    ):
        raise ValueError("observation start does not align to the UTC interval grid")
    forecast_points = [row.observed_at for row in forecast_rows]
    longest = current = 0
    segment_count = 0
    previous: datetime | None = None
    current_start: datetime | None = None
    periods: list[ContiguousPeriod] = []
    for row in actual:
        point = row.observed_at
        if (
            previous is not None
            and (point - previous).total_seconds() == dataset.interval_seconds
        ):
            current += 1
        else:
            if previous is not None and current_start is not None:
                periods.append(
                    ContiguousPeriod(
                        start_at=current_start,
                        end_at=previous + timedelta(seconds=dataset.interval_seconds),
                        interval_count=current,
                    )
                )
            current = 1
            current_start = point
            segment_count += 1
        longest = max(longest, current)
        previous = point
    if previous is not None and current_start is not None:
        periods.append(
            ContiguousPeriod(
                start_at=current_start,
                end_at=previous + timedelta(seconds=dataset.interval_seconds),
                interval_count=current,
            )
        )
    suitable = (
        len(actual) >= minimum_contiguous_intervals
        and longest >= minimum_contiguous_intervals
    )
    if suitable:
        assessment = "sufficient contiguous accepted actual observations for configured baseline evaluation"
    elif not actual:
        assessment = "no accepted actual observations are available; baseline evaluation is not possible"
    else:
        assessment = "insufficient contiguous accepted actual observations for configured baseline evaluation"
    return AvailabilityReport(
        region_id=dataset.region_id,
        first_interval_start=actual[0].observed_at if actual else None,
        last_interval_end=actual[-1].valid_until if actual else None,
        total_available_observations=len(actual) + dataset.available_forecast_count,
        actual_observation_count=len(actual),
        forecast_observation_count=dataset.available_forecast_count,
        actual_coverage_start=actual[0].observed_at if actual else None,
        actual_coverage_end=actual[-1].valid_until if actual else None,
        forecast_coverage_start=min(forecast_points, default=None),
        forecast_coverage_end=max(
            (row.valid_until for row in forecast_rows), default=None
        ),
        rejected_observation_count=dataset.rejected_observation_count,
        duplicate_observation_count=dataset.duplicate_observation_count,
        conflicting_revision_count=dataset.conflicting_revision_count,
        missing_interval_count=len(dataset.missing_intervals),
        contiguous_actual_intervals=len(actual),
        contiguous_actual_segment_count=segment_count,
        contiguous_actual_periods=tuple(periods),
        longest_contiguous_actual_intervals=longest,
        suitable_for_evaluation=suitable,
        assessment=assessment,
    )


def chronological_split(
    observations: Iterable[DatasetObservation],
    *,
    train_fraction: float = 0.8,
    cutoff: datetime | None = None,
) -> TemporalSplit:
    if not 0 < train_fraction < 1:
        raise ValueError("train_fraction must be between zero and one")
    input_rows = _accepted_actual_rows(observations)
    rows = _canonical_actuals(input_rows)
    if cutoff is not None:
        if cutoff.tzinfo is None or cutoff.utcoffset() is None:
            raise ValueError("cutoff must include a timezone")
        boundary = cutoff.astimezone(UTC)
        train = tuple(row for row in input_rows if row.observed_at < boundary)
        test = tuple(row for row in input_rows if row.observed_at >= boundary)
    else:
        split_index = int(len(rows) * train_fraction)
        if len(rows) >= 2:
            split_index = min(max(split_index, 1), len(rows) - 1)
        boundary = rows[split_index].observed_at if split_index < len(rows) else None
        train = tuple(
            row
            for row in input_rows
            if boundary is not None and row.observed_at < boundary
        )
        test = tuple(
            row
            for row in input_rows
            if boundary is not None and row.observed_at >= boundary
        )
    canonical_test = _canonical_actuals(test)
    return TemporalSplit(
        train,
        test,
        canonical_test[0].observed_at if train and canonical_test else None,
    )


def evaluate_baselines(
    train: Iterable[DatasetObservation],
    test: Iterable[DatasetObservation],
    *,
    horizons: tuple[int, ...] = (1,),
    seasonal_periods: int = 48,
    minimum_training_observations: int = 48,
    dataset_origin: str = "provided observations",
) -> EvaluationReport:
    train_input = tuple(train)
    test_input = tuple(test)
    train_rows = _validated_actuals(train_input)
    test_rows = _validated_actuals(test_input)
    if not horizons or any(
        isinstance(value, bool) or not isinstance(value, int) or value <= 0
        for value in horizons
    ):
        raise ValueError("horizons must contain positive interval counts")
    if seasonal_periods <= 0 or minimum_training_observations <= 0:
        raise ValueError(
            "seasonal_periods and minimum_training_observations must be positive"
        )
    if train_rows and test_rows and train_rows[-1].region_id != test_rows[0].region_id:
        raise ValueError("train and test observations must belong to the same region")
    if (
        train_rows
        and test_rows
        and train_rows[-1].valid_until > test_rows[0].observed_at
    ):
        raise ValueError("training and test intervals must not overlap")
    if (
        train_rows
        and test_rows
        and train_rows[-1].observed_at >= test_rows[0].observed_at
    ):
        raise ValueError(
            "test observations must occur strictly after the training period"
        )
    region_id = (
        train_rows[0].region_id
        if train_rows
        else (test_rows[0].region_id if test_rows else None)
    )
    interval_seconds = 1800
    available_actuals = _accepted_actual_rows(train_input + test_input)
    if any(
        (row.valid_until - row.observed_at).total_seconds() != interval_seconds
        or int(row.observed_at.timestamp()) % interval_seconds != 0
        for row in available_actuals
    ):
        return EvaluationReport(
            region_id=region_id,
            dataset_origin=dataset_origin,
            sufficient_data=False,
            insufficient_reason="baseline evaluation requires UTC-aligned half-hourly intervals",
            train_start=train_rows[0].observed_at if train_rows else None,
            train_end=train_rows[-1].valid_until if train_rows else None,
            test_start=test_rows[0].observed_at if test_rows else None,
            test_end=test_rows[-1].valid_until if test_rows else None,
            results=(),
        )
    if (
        any(
            (right.observed_at - left.observed_at).total_seconds() != interval_seconds
            for left, right in zip(train_rows, train_rows[1:])
        )
        or any(
            (right.observed_at - left.observed_at).total_seconds() != interval_seconds
            for left, right in zip(test_rows, test_rows[1:])
        )
        or (
            train_rows
            and test_rows
            and (test_rows[0].observed_at - train_rows[-1].observed_at).total_seconds()
            != interval_seconds
        )
    ):
        return EvaluationReport(
            region_id=region_id,
            dataset_origin=dataset_origin,
            sufficient_data=False,
            insufficient_reason="baseline evaluation requires contiguous half-hourly actuals; gaps are not interpolated",
            train_start=train_rows[0].observed_at if train_rows else None,
            train_end=train_rows[-1].valid_until if train_rows else None,
            test_start=test_rows[0].observed_at if test_rows else None,
            test_end=test_rows[-1].valid_until if test_rows else None,
            results=(),
        )
    results: list[BaselineMetric] = []
    for horizon in sorted(set(horizons)):
        for method in ("persistence", "seasonal_naive"):
            if method == "seasonal_naive" and len(train_rows) < max(
                seasonal_periods, minimum_training_observations
            ):
                continue
            errors: list[tuple[datetime, float]] = []
            skipped = 0
            for target in test_rows:
                origin = target.observed_at - timedelta(
                    seconds=horizon * interval_seconds
                )
                if origin < test_rows[0].observed_at:
                    continue
                if method == "persistence":
                    source_candidates = [
                        row
                        for row in available_actuals
                        if row.valid_until <= origin and row.retrieved_at <= origin
                    ]
                    source = (
                        max(
                            source_candidates,
                            key=lambda row: (
                                row.observed_at,
                                row.retrieved_at,
                                row.payload_sha256,
                                row.raw_payload_id,
                            ),
                        )
                        if source_candidates
                        else None
                    )
                else:
                    seasonal_source_time = target.observed_at - timedelta(
                        seconds=seasonal_periods * interval_seconds
                    )
                    source_candidates = [
                        row
                        for row in available_actuals
                        if row.observed_at == seasonal_source_time
                        and row.valid_until <= origin
                        and row.retrieved_at <= origin
                    ]
                    source = (
                        max(source_candidates, key=lambda row: row.retrieved_at)
                        if source_candidates
                        else None
                    )
                if source is None or source.retrieved_at > origin:
                    skipped += 1
                    continue
                errors.append(
                    (
                        target.observed_at,
                        source.intensity_gco2_per_kwh - target.intensity_gco2_per_kwh,
                    )
                )
            if errors:
                values = [error for _, error in errors]
                results.append(
                    BaselineMetric(
                        method=method,
                        horizon_intervals=horizon,
                        sample_count=len(values),
                        mae=sum(abs(value) for value in values) / len(values),
                        rmse=math.sqrt(
                            sum(value * value for value in values) / len(values)
                        ),
                        first_target_at=errors[0][0],
                        last_target_at=errors[-1][0],
                        skipped_missing_input_count=skipped,
                    )
                )
    sufficient = len(train_rows) >= minimum_training_observations and bool(test_rows)
    sufficient = sufficient and bool(results)
    reason = (
        None
        if sufficient
        else (
            f"requires at least {minimum_training_observations} accepted training actuals, one test actual, and a usable baseline sample"
        )
    )
    return EvaluationReport(
        region_id=region_id,
        dataset_origin=dataset_origin,
        sufficient_data=sufficient,
        insufficient_reason=reason,
        train_start=train_rows[0].observed_at if train_rows else None,
        train_end=train_rows[-1].valid_until if train_rows else None,
        test_start=test_rows[0].observed_at if test_rows else None,
        test_end=test_rows[-1].valid_until if test_rows else None,
        results=tuple(results) if sufficient else (),
    )


def _validated_actuals(
    rows: Iterable[DatasetObservation],
) -> tuple[DatasetObservation, ...]:
    return _canonical_actuals(_accepted_actual_rows(rows))


def _accepted_actual_rows(
    rows: Iterable[DatasetObservation],
) -> tuple[DatasetObservation, ...]:
    materialised = tuple(
        row.utc() for row in rows if row.accepted and row.measurement_type == "actual"
    )
    if materialised and any(
        row.region_id != materialised[0].region_id for row in materialised[1:]
    ):
        raise ValueError("observations must belong to one region")
    return tuple(
        sorted(
            materialised,
            key=lambda row: (
                row.observed_at,
                row.valid_until,
                row.retrieved_at,
                row.payload_sha256,
                row.raw_payload_id,
            ),
        )
    )


def _canonical_actuals(
    materialised: Iterable[DatasetObservation],
) -> tuple[DatasetObservation, ...]:
    revisions: dict[tuple[datetime, datetime], list[DatasetObservation]] = defaultdict(
        list
    )
    for row in materialised:
        revisions[(row.observed_at, row.valid_until)].append(row)
    selected = (
        max(
            group,
            key=lambda row: (
                row.retrieved_at,
                row.payload_sha256,
                row.raw_payload_id,
            ),
        )
        for group in revisions.values()
    )
    return tuple(
        sorted(
            selected,
            key=lambda row: (
                row.observed_at,
                row.valid_until,
                row.retrieved_at,
                row.payload_sha256,
            ),
        )
    )


def _from_models(observation: CarbonObservation, raw: RawPayload) -> DatasetObservation:
    return DatasetObservation(
        region_id=observation.region_id,
        region_name=observation.region_name,
        observed_at=observation.observed_at,
        valid_until=observation.valid_until,
        retrieved_at=observation.retrieved_at,
        measurement_type=observation.measurement_type,
        intensity_gco2_per_kwh=observation.intensity_gco2_per_kwh,
        source_api=observation.source_api,
        source_url=observation.source_url,
        payload_sha256=raw.payload_sha256,
        raw_payload_id=raw.id,
        accepted=True,
    )


def _observation_json(row: DatasetObservation) -> dict[str, Any]:
    return {
        "region_id": row.region_id,
        "region_name": row.region_name,
        "observed_at": _iso(row.observed_at),
        "valid_until": _iso(row.valid_until),
        "retrieved_at": _iso(row.retrieved_at),
        "measurement_type": row.measurement_type,
        "intensity_gco2_per_kwh": row.intensity_gco2_per_kwh,
        "units": "gCO2e/kWh",
        "source_api": row.source_api,
        "source_url": row.source_url,
        "payload_sha256": row.payload_sha256,
        "raw_payload_id": row.raw_payload_id,
        "accepted": row.accepted,
    }


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
