"""Validate source, geography, time and provenance for candidate targets."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import cast
from urllib.parse import urlsplit

from app.services.intensity_units import (
    HISTORICAL_NESO_SOURCE_API,
    LIVE_NESO_SOURCE_API,
)


@dataclass(frozen=True)
class SourceContract:
    source_id: str
    target_variable: str
    geography_codes: tuple[str, ...]
    region_id: int | None
    region_name: str | None
    units: str | None
    measurement_classes: tuple[str, ...]
    eligible_for_region_13_actual: bool
    eligibility_reasons: tuple[str, ...] = ()
    publication_time_verified: bool = False
    interval_seconds: int = 1800
    official_hosts: tuple[str, ...] = ()


@dataclass(frozen=True)
class CandidateTarget:
    source_id: str
    target_variable: str
    measurement_class: str
    geography_code: str
    region_id: int | None
    region_name: str | None
    interval_start: datetime
    interval_end: datetime
    value: float
    units: str
    source_record_id: str | int | None
    source_url: str | None
    retrieved_at: datetime | None
    published_at: datetime | None = None


@dataclass(frozen=True)
class CandidateRecordDecision:
    source_id: str
    source_record_id: str | int | None
    eligible_for_region_13_actual: bool
    publication_provenance_verified: bool
    available_at_forecast_origin: bool | None
    rejection_reasons: tuple[str, ...]
    readiness_reasons: tuple[str, ...]


@dataclass(frozen=True)
class CandidateTargetReport:
    record_decisions: tuple[CandidateRecordDecision, ...]
    accepted_records: tuple[CandidateTarget, ...]
    duplicate_observation_count: int
    conflicting_observation_count: int
    revision_count: int
    missing_intervals: tuple[datetime, ...]
    target_window_complete: bool
    as_of_feature_availability_at_origin: bool | None


SOURCE_CONTRACTS: Mapping[str | tuple[str, str], SourceContract] = cast(
    Mapping[str | tuple[str, str], SourceContract],
    MappingProxyType(
    {
        (LIVE_NESO_SOURCE_API, "regional_carbon_intensity"): SourceContract(
            source_id=LIVE_NESO_SOURCE_API,
            target_variable="regional_carbon_intensity",
            geography_codes=("NESO-13",),
            region_id=13,
            region_name="London",
            units="gCO2e/kWh",
            measurement_classes=("forecast",),
            eligible_for_region_13_actual=False,
            eligibility_reasons=("regional_actual_not_evidenced",),
            official_hosts=("api.carbonintensity.org.uk",),
        ),
        (HISTORICAL_NESO_SOURCE_API, "regional_carbon_intensity"): SourceContract(
            source_id=HISTORICAL_NESO_SOURCE_API,
            target_variable="regional_carbon_intensity",
            geography_codes=("NESO-13",),
            region_id=13,
            region_name="London",
            units="gCO2/kWh",
            measurement_classes=("forecast",),
            eligible_for_region_13_actual=False,
            eligibility_reasons=("forecast_not_actual", "forecast_vintage_missing"),
            official_hosts=("api.neso.energy",),
        ),
        (LIVE_NESO_SOURCE_API, "national_carbon_intensity"): SourceContract(
            source_id=LIVE_NESO_SOURCE_API,
            target_variable="national_carbon_intensity",
            geography_codes=("GB",),
            region_id=None,
            region_name=None,
            units="gCO2/kWh",
            measurement_classes=("estimated_actual", "forecast"),
            eligible_for_region_13_actual=False,
            eligibility_reasons=("geographic_scope_mismatch_national_only",),
            official_hosts=("api.carbonintensity.org.uk",),
        ),
        ("neso-national-carbon-intensity-forecast", "national_carbon_intensity"): SourceContract(
            source_id="neso-national-carbon-intensity-forecast",
            target_variable="national_carbon_intensity",
            geography_codes=("GB",),
            region_id=None,
            region_name=None,
            units="gCO2/kWh",
            measurement_classes=("forecast",),
            eligible_for_region_13_actual=False,
            eligibility_reasons=("forecast_not_actual", "geographic_scope_mismatch_national_only"),
            official_hosts=("www.neso.energy", "api.neso.energy"),
        ),
        ("neso-historic-generation-mix", "national_generation_mix"): SourceContract(
            source_id="neso-historic-generation-mix",
            target_variable="national_generation_mix",
            geography_codes=("GB",),
            region_id=None,
            region_name=None,
            units=None,
            measurement_classes=("national_estimated_series",),
            eligible_for_region_13_actual=False,
            eligibility_reasons=("geographic_scope_mismatch_national_only",),
            official_hosts=("www.neso.energy", "api.neso.energy"),
        ),
        ("ukpn-carbon-intensity", "regional_carbon_intensity"): SourceContract(
            source_id="ukpn-carbon-intensity",
            target_variable="regional_carbon_intensity",
            geography_codes=("EPN", "SPN"),
            region_id=None,
            region_name=None,
            units="gCO2e/kWh",
            measurement_classes=("derived_estimate",),
            eligible_for_region_13_actual=False,
            eligibility_reasons=(
                "london_area_not_covered",
                "derived_estimate_uses_forecast_input",
            ),
            official_hosts=("ukpowernetworks.opendatasoft.com",),
        ),
        ("elexon-bmrs-b1610", "metered_generation_energy"): SourceContract(
            source_id="elexon-bmrs-b1610",
            target_variable="metered_generation_energy",
            geography_codes=("BMU",),
            region_id=None,
            region_name=None,
            units="MWh",
            measurement_classes=("metered_energy",),
            eligible_for_region_13_actual=False,
            eligibility_reasons=(
                "target_variable_is_energy_not_carbon_intensity",
                "geography_is_bmu_not_london_region_13",
            ),
            official_hosts=("bmrs.elexon.co.uk",),
        ),
    }
    ),
)


def validate_candidate_targets(
    candidates: Iterable[CandidateTarget],
    *,
    requested_start: datetime,
    requested_end: datetime,
    source_contracts: Mapping[str | tuple[str, str], SourceContract] = SOURCE_CONTRACTS,
    expected_region_id: int = 13,
    expected_region_name: str = "London",
    forecast_origin: datetime | None = None,
) -> CandidateTargetReport:
    """Validate candidate rows and their one-source, half-hour series semantics.

    Rows from a source not present in ``source_contracts`` fail closed. A source
    contract is necessary but not sufficient: record evidence and the explicit
    contract eligibility decision must also pass.
    """
    start_utc = _window_timestamp(requested_start, "requested_start")
    end_utc = _window_timestamp(requested_end, "requested_end")
    origin_utc = (
        _origin_timestamp(forecast_origin) if forecast_origin is not None else None
    )
    if end_utc <= start_utc:
        raise ValueError("requested_end must be after requested_start")
    if (end_utc - start_utc).total_seconds() % 1800:
        raise ValueError("requested window must contain whole UTC half-hour intervals")

    supplied_rows = tuple(candidates)
    if any(not isinstance(row, CandidateTarget) for row in supplied_rows):
        raise TypeError("candidates must contain CandidateTarget records")
    rows = tuple(sorted(supplied_rows, key=_candidate_sort_key))

    decisions: list[CandidateRecordDecision] = []
    utc_bounds: list[tuple[datetime | None, datetime | None]] = []
    for row in rows:
        decision, interval_start, interval_end = _validate_record(
            row,
            source_contracts=source_contracts,
            requested_start=start_utc,
            requested_end=end_utc,
            expected_region_id=expected_region_id,
            expected_region_name=expected_region_name,
            forecast_origin=origin_utc,
        )
        decisions.append(decision)
        utc_bounds.append((interval_start, interval_end))

    eligible_source_ids = {
        rows[index].source_id
        for index, decision in enumerate(decisions)
        if decision.eligible_for_region_13_actual
    }
    if len(eligible_source_ids) > 1:
        for index, decision in enumerate(decisions):
            if decision.eligible_for_region_13_actual:
                decisions[index] = _reject(decision, "mixed_source_series")

    groups: dict[tuple[str, int | None, str, datetime], list[int]] = defaultdict(list)
    for index, decision in enumerate(decisions):
        start = utc_bounds[index][0]
        if decision.eligible_for_region_13_actual and start is not None:
            row = rows[index]
            groups[(row.source_id, row.region_id, row.geography_code, start)].append(index)

    duplicate_count = 0
    conflict_count = 0
    revision_count = 0
    accepted_indices: set[int] = set()
    for indices in groups.values():
        distinct_by_observation: dict[tuple[str, float, str, datetime | None], int] = {}
        unique_indices: list[int] = []
        for index in indices:
            row = rows[index]
            publication_time = (
                _to_utc(row.published_at, "published_at")
                if row.published_at is not None
                else None
            )
            signature = (
                row.measurement_class,
                float(row.value),
                row.units,
                publication_time,
            )
            if signature in distinct_by_observation:
                duplicate_count += 1
                decisions[index] = _reject(decisions[index], "duplicate_observation")
            else:
                distinct_by_observation[signature] = index
                unique_indices.append(index)

        distinct_values = {float(rows[index].value) for index in unique_indices}
        ordered_publications = all(
            decisions[index].publication_provenance_verified
            and rows[index].published_at is not None
            for index in unique_indices
        )
        if ordered_publications:
            publication_times = [
                _to_utc(rows[index].published_at, "published_at")
                for index in unique_indices
            ]
            ordered_publications = len(set(publication_times)) == len(
                publication_times
            )

        measurement_classes = {
            rows[index].measurement_class for index in unique_indices
        }
        if len(measurement_classes) > 1:
            conflict_count += 1
            for index in unique_indices:
                decisions[index] = _reject(
                    decisions[index], "measurement_semantics_conflict"
                )
        elif ordered_publications and len(unique_indices) > 1:
            revision_count += len(unique_indices) - 1
            latest_index = max(
                unique_indices,
                key=lambda index: _to_utc(rows[index].published_at, "published_at"),
            )
            accepted_indices.add(latest_index)
            for index in unique_indices:
                if index != latest_index:
                    decisions[index] = _reject(decisions[index], "superseded_revision")
        elif len(distinct_values) <= 1:
            canonical_index = max(
                unique_indices,
                key=lambda index: (
                    decisions[index].publication_provenance_verified,
                    _sortable(rows[index].published_at),
                    str(rows[index].source_record_id),
                    _sortable(rows[index].retrieved_at),
                ),
            )
            accepted_indices.add(canonical_index)
            for index in unique_indices:
                if index != canonical_index:
                    duplicate_count += 1
                    decisions[index] = _reject(decisions[index], "duplicate_observation")
        else:
            conflict_count += 1
            for index in unique_indices:
                decisions[index] = _reject(
                    decisions[index], "unresolved_revision_conflict"
                )

    accepted_records = tuple(
        sorted(
            (rows[index] for index in accepted_indices),
            key=lambda row: (
                row.interval_start.astimezone(UTC),
                row.source_id,
                row.source_record_id or "",
            ),
        )
    )
    accepted_starts = {
        utc_bounds[index][0]
        for index in accepted_indices
        if utc_bounds[index][0] is not None
    }
    missing_intervals: list[datetime] = []
    point = start_utc
    while point < end_utc:
        if point not in accepted_starts:
            missing_intervals.append(point)
        point += timedelta(minutes=30)

    target_window_complete = bool(accepted_records) and not missing_intervals
    availability_states = [
        decisions[index].available_at_forecast_origin for index in accepted_indices
    ]
    if origin_utc is None or not availability_states:
        as_of_availability = None
    elif False in availability_states:
        as_of_availability = False
    elif all(state is True for state in availability_states):
        as_of_availability = True
    else:
        as_of_availability = None
    return CandidateTargetReport(
        record_decisions=tuple(decisions),
        accepted_records=accepted_records,
        duplicate_observation_count=duplicate_count,
        conflicting_observation_count=conflict_count,
        revision_count=revision_count,
        missing_intervals=tuple(missing_intervals),
        target_window_complete=target_window_complete,
        as_of_feature_availability_at_origin=as_of_availability,
    )


def _validate_record(
    row: CandidateTarget,
    *,
    source_contracts: Mapping[str | tuple[str, str], SourceContract],
    requested_start: datetime,
    requested_end: datetime,
    expected_region_id: int,
    expected_region_name: str,
    forecast_origin: datetime | None,
) -> tuple[CandidateRecordDecision, datetime | None, datetime | None]:
    reasons: list[str] = []
    readiness: list[str] = []
    contract = source_contracts.get((row.source_id, row.target_variable))
    if contract is None:
        contract = source_contracts.get(row.source_id)
    if contract is None:
        return (
            CandidateRecordDecision(
                source_id=row.source_id,
                source_record_id=row.source_record_id,
                eligible_for_region_13_actual=False,
                publication_provenance_verified=False,
                available_at_forecast_origin=None,
                rejection_reasons=("unsupported_source",),
                readiness_reasons=(),
            ),
            None,
            None,
        )

    if contract.source_id != row.source_id:
        reasons.append("source_contract_identity_mismatch")
    if not contract.eligible_for_region_13_actual:
        reasons.append("source_not_eligible")
        reasons.extend(contract.eligibility_reasons)
    if row.target_variable != "regional_carbon_intensity" or (
        row.target_variable != contract.target_variable
    ):
        reasons.append("target_variable_mismatch")
    if row.measurement_class not in contract.measurement_classes:
        reasons.append("measurement_semantics_mismatch")
    if row.measurement_class == "forecast":
        reasons.append("forecast_not_actual")
    if row.geography_code not in contract.geography_codes:
        reasons.append("geographic_scope_mismatch")
    if (
        row.region_id != expected_region_id
        or contract.region_id != expected_region_id
        or row.region_id != contract.region_id
    ):
        reasons.append("region_identity_mismatch")
    if (
        row.region_name is None
        or row.region_name.casefold() != expected_region_name.casefold()
        or contract.region_name is None
        or contract.region_name.casefold() != expected_region_name.casefold()
    ):
        reasons.append("region_identity_mismatch")

    if contract.units is None:
        reasons.append("unit_contract_unknown")
    elif row.units != contract.units:
        reasons.append("unit_contract_mismatch")
    if (
        isinstance(row.value, bool)
        or not isinstance(row.value, (int, float))
        or not math.isfinite(row.value)
        or row.value < 0
    ):
        reasons.append("value_not_finite_nonnegative")

    interval_start = _record_timestamp(row.interval_start, "interval", reasons)
    interval_end = _record_timestamp(row.interval_end, "interval", reasons)
    if interval_start is not None and interval_end is not None:
        if not _on_half_hour_grid(interval_start) or not _on_half_hour_grid(interval_end):
            reasons.append("interval_not_utc_grid")
        if interval_end - interval_start != timedelta(seconds=contract.interval_seconds):
            reasons.append("interval_duration_mismatch")
        if interval_start < requested_start or interval_end > requested_end:
            reasons.append("interval_outside_requested_window")

    retrieved_at: datetime | None = None
    if row.retrieved_at is None:
        reasons.append("retrieval_timestamp_missing")
    else:
        retrieved_at = _record_timestamp(row.retrieved_at, "retrieval", reasons)
        if (
            retrieved_at is not None
            and interval_end is not None
            and row.measurement_class in ("direct_actual", "estimated_actual", "derived_estimate")
            and retrieved_at < interval_end
        ):
            reasons.append("retrieved_before_interval_end")

    publication_provenance_verified = False
    published_at: datetime | None = None
    if row.published_at is None:
        readiness.append("publication_time_unverified")
    else:
        published_at = _record_timestamp(row.published_at, "publication", reasons)
        if published_at is None:
            if not contract.publication_time_verified:
                readiness.append("publication_time_unverified")
        elif interval_end is not None and (
            published_at < interval_end
            or (retrieved_at is not None and published_at > retrieved_at)
        ):
            reasons.append("publication_timestamp_inconsistent")
        elif contract.publication_time_verified:
            publication_provenance_verified = True
        else:
            readiness.append("publication_time_unverified")

    available_at_forecast_origin = _available_at_origin(
        forecast_origin=forecast_origin,
        interval_end=interval_end,
        retrieved_at=retrieved_at,
        published_at=published_at,
        publication_provenance_verified=publication_provenance_verified,
    )

    if row.source_record_id is None or (
        isinstance(row.source_record_id, str) and not row.source_record_id.strip()
    ):
        reasons.append("source_record_id_missing")
    elif isinstance(row.source_record_id, bool) or not isinstance(
        row.source_record_id, (str, int)
    ):
        reasons.append("source_record_id_invalid")
    if not _trusted_source_url(row.source_url, contract.official_hosts):
        reasons.append("source_url_invalid")

    return (
        CandidateRecordDecision(
            source_id=row.source_id,
            source_record_id=row.source_record_id,
            eligible_for_region_13_actual=not reasons,
            publication_provenance_verified=publication_provenance_verified,
            available_at_forecast_origin=available_at_forecast_origin,
            rejection_reasons=tuple(dict.fromkeys(reasons)),
            readiness_reasons=tuple(dict.fromkeys(readiness)),
        ),
        interval_start,
        interval_end,
    )


def _reject(decision: CandidateRecordDecision, reason: str) -> CandidateRecordDecision:
    return replace(
        decision,
        eligible_for_region_13_actual=False,
        rejection_reasons=tuple(dict.fromkeys((*decision.rejection_reasons, reason))),
    )


def _record_timestamp(
    value: datetime, field: str, reasons: list[str]
) -> datetime | None:
    if not isinstance(value, datetime):
        reasons.append(f"{field}_timestamp_invalid")
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        reasons.append(f"{field}_timezone_missing")
        if field == "interval":
            reasons.append("interval_timezone_missing")
        return None
    return value.astimezone(UTC)


def _window_timestamp(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    result = value.astimezone(UTC)
    if not _on_half_hour_grid(result):
        raise ValueError(f"{field} must align to the UTC half-hour grid")
    return result


def _origin_timestamp(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("forecast_origin must include a timezone")
    return value.astimezone(UTC)


def _available_at_origin(
    *,
    forecast_origin: datetime | None,
    interval_end: datetime | None,
    retrieved_at: datetime | None,
    published_at: datetime | None,
    publication_provenance_verified: bool,
) -> bool | None:
    if forecast_origin is None:
        return None
    if interval_end is not None and interval_end > forecast_origin:
        return False
    if (
        publication_provenance_verified
        and published_at is not None
        and published_at > forecast_origin
    ):
        return False
    if retrieved_at is not None and retrieved_at > forecast_origin:
        return False
    if (
        interval_end is None
        or retrieved_at is None
        or not publication_provenance_verified
        or published_at is None
    ):
        return None
    return True


def _on_half_hour_grid(value: datetime) -> bool:
    return value.minute in (0, 30) and value.second == 0 and value.microsecond == 0


def _to_utc(value: datetime | None, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return value.astimezone(UTC)


def _trusted_source_url(value: str | None, official_hosts: tuple[str, ...]) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme == "https"
            and parsed.hostname is not None
            and parsed.hostname.casefold() in {host.casefold() for host in official_hosts}
            and parsed.username is None
            and parsed.password is None
        )
    except ValueError:
        return False


def _candidate_sort_key(row: CandidateTarget) -> tuple[str, ...]:
    """Make decisions and duplicate resolution independent of input ordering."""
    return (
        str(row.source_id),
        str(row.geography_code),
        "" if row.region_id is None else str(row.region_id),
        _sortable(row.interval_start),
        _sortable(row.interval_end),
        str(row.measurement_class),
        repr(row.value),
        str(row.units),
        _sortable(row.published_at),
        str(row.source_record_id),
        str(row.source_url),
        _sortable(row.retrieved_at),
    )


def _sortable(value: object) -> str:
    return value.isoformat() if isinstance(value, datetime) else repr(value)
