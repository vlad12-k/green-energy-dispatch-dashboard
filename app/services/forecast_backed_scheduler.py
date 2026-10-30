from __future__ import annotations

from datetime import datetime, timedelta
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

from app.schemas.forecast_backed_ev_depot import (
    ForecastBackedDepotScenario,
    ForecastInputUnavailableReason,
    ForecastUnavailableDetail,
)
from app.services.carbon_aware_scheduler import (
    CarbonAwareSchedule,
    SchedulerCarbonIntensityInterval,
    schedule_with_carbon_profile,
)
from app.services.neso_carbon_intensity import (
    DEFAULT_BASE_URL,
    SOURCE_API,
    CarbonIntensityForecastInterval,
    CarbonIntensityForecastTimeline,
)

_MAX_FORECAST_INTERVALS = 48
_MAX_FORECAST_SCENARIO_VEHICLES = 50
_MAX_SCHEDULING_NUMERIC_VALUE = 1_000_000_000.0
_FORECAST_INTERVAL = timedelta(minutes=30)
_SUPPORTED_FORECAST_UNIT = "gCO2/kWh"


class ForecastBackedInputProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_classification: Literal["regional_forecast"]
    source_api: Literal["neso-carbon-intensity-api"]
    source_url: str = Field(min_length=1)
    region_id: int = Field(ge=1, le=17)
    region_name: str = Field(min_length=1)
    measurement_type: Literal["forecast"] = "forecast"
    forecast_retrieved_at: datetime
    prediction_horizon_start: datetime
    prediction_horizon_end: datetime
    scheduling_horizon_start: datetime
    scheduling_horizon_end: datetime
    decision_time: datetime
    forecast_age_seconds: FiniteFloat = Field(ge=0)
    intensity_unit: Literal["gCO2/kWh"]
    interval_count: int = Field(ge=1, le=_MAX_FORECAST_INTERVALS)


class ForecastBackedSchedulingResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: str
    status: Literal["available", "unavailable"]
    unavailable_reason: ForecastInputUnavailableReason | None
    unavailable_detail: ForecastUnavailableDetail | None = None
    input_provenance: ForecastBackedInputProvenance | None
    immediate_baseline: CarbonAwareSchedule | None
    forecast_aware: CarbonAwareSchedule | None
    comparison_eligible: bool
    absolute_carbon_proxy_reduction: FiniteFloat | None
    percentage_carbon_proxy_reduction: FiniteFloat | None
    comparison_unavailable_reason: str | None
    percentage_difference_unavailable_reason: str | None = None
    carbon_proxy_unit: Literal["gCO2"] = "gCO2"
    advisory_only: Literal[True] = True
    forecast_based: Literal[True] = True
    measured: Literal[False] = False
    real_world_emissions_reduction_claimed: Literal[False] = False

    @model_validator(mode="after")
    def validate_result_state(self) -> ForecastBackedSchedulingResult:
        if self.status == "unavailable":
            if (
                self.unavailable_reason is None
                or self.input_provenance is not None
                or self.immediate_baseline is not None
                or self.forecast_aware is not None
                or self.comparison_eligible
                or self.absolute_carbon_proxy_reduction is not None
                or self.percentage_carbon_proxy_reduction is not None
                or not self.comparison_unavailable_reason
            ):
                raise ValueError("unavailable forecast results must fail closed")
            return self

        if (
            self.unavailable_reason is not None
            or self.unavailable_detail is not None
            or self.input_provenance is None
            or self.immediate_baseline is None
            or self.forecast_aware is None
        ):
            raise ValueError("available forecast results require validated inputs and schedules")
        if self.comparison_eligible:
            if (
                not self.immediate_baseline.feasible
                or not self.forecast_aware.feasible
                or self.absolute_carbon_proxy_reduction is None
                or self.comparison_unavailable_reason is not None
            ):
                raise ValueError("eligible comparisons require two complete schedules")
        elif (
            self.absolute_carbon_proxy_reduction is not None
            or self.percentage_carbon_proxy_reduction is not None
            or not self.comparison_unavailable_reason
        ):
            raise ValueError("ineligible comparisons require an explicit reason")
        return self


def schedule_forecast_backed(
    request: ForecastBackedDepotScenario,
) -> ForecastBackedSchedulingResult:
    """Schedule against one validated point-in-time forecast; never falls back.

    The immediate baseline and bounded carbon-shift heuristic receive the same
    depot requirements and the same validated forecast profile. The heuristic
    is not a global optimizer, and all carbon values remain forecast-derived
    advisory proxies rather than measured emissions.
    """
    reason, profile = _validated_scheduler_profile(request)
    if reason is not None or profile is None:
        return _unavailable_result(request, reason or "invalid_forecast_timeline")

    scenario = request.scenario.model_copy(deep=True)
    baseline, forecast_aware = schedule_with_carbon_profile(scenario, profile)
    comparison_eligible = baseline.feasible and forecast_aware.feasible
    if comparison_eligible:
        absolute_reduction = (
            baseline.carbon_intensity_weighted_energy_proxy
            - forecast_aware.carbon_intensity_weighted_energy_proxy
        )
        percentage_reduction = (
            absolute_reduction / baseline.carbon_intensity_weighted_energy_proxy * 100
            if baseline.carbon_intensity_weighted_energy_proxy > 0
            else None
        )
        comparison_reason = None
        percentage_reason = (
            "Percentage difference is undefined because the baseline proxy is zero."
            if baseline.carbon_intensity_weighted_energy_proxy == 0
            else None
        )
    else:
        absolute_reduction = None
        percentage_reduction = None
        comparison_reason = (
            "Equivalent-service comparison is unavailable because at least one "
            "strategy did not complete every vehicle requirement. Failure of the "
            "bounded heuristic does not prove scenario infeasibility."
        )
        percentage_reason = None

    timeline = request.forecast
    assert timeline is not None
    age_seconds = (request.decision_time - timeline.retrieved_at).total_seconds()
    provenance = ForecastBackedInputProvenance(
        source_classification="regional_forecast",
        source_api=SOURCE_API,
        source_url=timeline.source_url,
        region_id=timeline.region_id,
        region_name=timeline.region_name,
        forecast_retrieved_at=timeline.retrieved_at,
        prediction_horizon_start=timeline.requested_from,
        prediction_horizon_end=timeline.valid_until,
        scheduling_horizon_start=scenario.start_at,
        scheduling_horizon_end=scenario.horizon_end,
        decision_time=request.decision_time,
        forecast_age_seconds=age_seconds,
        intensity_unit=_SUPPORTED_FORECAST_UNIT,
        interval_count=len(timeline.intervals),
    )
    return ForecastBackedSchedulingResult(
        scenario_id=scenario.scenario_id,
        status="available",
        unavailable_reason=None,
        input_provenance=provenance,
        immediate_baseline=baseline,
        forecast_aware=forecast_aware,
        comparison_eligible=comparison_eligible,
        absolute_carbon_proxy_reduction=absolute_reduction,
        percentage_carbon_proxy_reduction=percentage_reduction,
        comparison_unavailable_reason=comparison_reason,
        percentage_difference_unavailable_reason=percentage_reason,
    )


def _validated_scheduler_profile(
    request: ForecastBackedDepotScenario,
) -> tuple[
    ForecastInputUnavailableReason | None,
    tuple[SchedulerCarbonIntensityInterval, ...] | None,
]:
    scenario = request.scenario
    if request.region_mapping.scenario_id != scenario.scenario_id:
        return "scenario_region_mapping_mismatch", None
    if request.forecast is None:
        return (
            "forecast_unavailable"
            if request.forecast_unavailable_detail is not None
            else "missing_forecast",
            None,
        )
    if request.forecast_unavailable_detail is not None:
        return "forecast_unavailable", None

    timeline = request.forecast
    if not isinstance(timeline, CarbonIntensityForecastTimeline):
        return "invalid_forecast_timeline", None
    if type(timeline.region_id) is not int or not 1 <= timeline.region_id <= 17:
        return "forecast_region_mismatch", None
    if timeline.region_id != request.region_mapping.neso_region_id:
        return "forecast_region_mismatch", None
    if not isinstance(timeline.region_name, str) or not timeline.region_name.strip():
        return "invalid_forecast_source", None
    if not _is_utc(request.decision_time):
        return "invalid_decision_time", None
    if request.decision_time > scenario.start_at:
        return "decision_after_scenario_start", None
    if not isinstance(timeline.retrieved_at, datetime) or not _is_utc(timeline.retrieved_at):
        return "invalid_forecast_timeline", None
    if timeline.retrieved_at > request.decision_time:
        return "forecast_retrieved_after_decision_time", None
    age_seconds = (request.decision_time - timeline.retrieved_at).total_seconds()
    if age_seconds > request.max_forecast_age_seconds:
        return "stale_forecast", None
    if (
        scenario.intervals > _MAX_FORECAST_INTERVALS
        or len(scenario.vehicles) > _MAX_FORECAST_SCENARIO_VEHICLES
        or scenario.depot_power_kw > _MAX_SCHEDULING_NUMERIC_VALUE
        or any(
            vehicle.required_energy_kwh > _MAX_SCHEDULING_NUMERIC_VALUE
            or vehicle.charging_power_kw > _MAX_SCHEDULING_NUMERIC_VALUE
            for vehicle in scenario.vehicles
        )
    ):
        return "invalid_depot_scenario", None
    if timeline.source_api != SOURCE_API or not isinstance(timeline.source_url, str):
        return "invalid_forecast_source", None
    if request.intensity_unit != _SUPPORTED_FORECAST_UNIT or timeline.units != request.intensity_unit:
        return "intensity_unit_mismatch", None
    if (
        not isinstance(timeline.requested_from, datetime)
        or not _is_utc(timeline.requested_from)
        or not _is_half_hour_aligned(timeline.requested_from)
    ):
        return "misaligned_forecast_intervals", None
    if not isinstance(timeline.valid_until, datetime) or not _is_utc(timeline.valid_until):
        return "invalid_forecast_timeline", None
    expected_source_url = (
        f"{DEFAULT_BASE_URL}/regional/intensity/"
        f"{timeline.requested_from.strftime('%Y-%m-%dT%H:%MZ')}"
        f"/fw24h/regionid/{timeline.region_id}"
    )
    if timeline.source_url != expected_source_url:
        return "invalid_forecast_source", None
    if scenario.interval_minutes != 30 or not _is_half_hour_aligned(scenario.start_at):
        return "scenario_interval_misaligned", None
    if not isinstance(timeline.intervals, tuple):
        return "invalid_forecast_timeline", None
    if len(timeline.intervals) < _MAX_FORECAST_INTERVALS:
        return "missing_interval_coverage", None
    if len(timeline.intervals) > _MAX_FORECAST_INTERVALS:
        return "invalid_forecast_timeline", None
    if timeline.valid_until != timeline.requested_from + _FORECAST_INTERVAL * _MAX_FORECAST_INTERVALS:
        return "missing_interval_coverage", None

    previous_end: datetime | None = None
    for index, interval in enumerate(timeline.intervals):
        if not isinstance(interval, CarbonIntensityForecastInterval):
            return "invalid_forecast_timeline", None
        intensity = interval.intensity_gco2_per_kwh
        if (
            interval.measurement_type != "forecast"
            or not isinstance(interval.observed_at, datetime)
            or not _is_utc(interval.observed_at)
            or not isinstance(interval.valid_until, datetime)
            or not _is_utc(interval.valid_until)
            or interval.valid_until != interval.observed_at + _FORECAST_INTERVAL
            or isinstance(intensity, bool)
            or not isinstance(intensity, (int, float))
            or intensity < 0
            or intensity > _MAX_SCHEDULING_NUMERIC_VALUE
            or not math.isfinite(intensity)
        ):
            return "invalid_forecast_timeline", None
        if not _is_half_hour_aligned(interval.observed_at):
            return "misaligned_forecast_intervals", None
        if previous_end is not None and interval.observed_at < previous_end:
            return "overlapping_forecast_intervals", None
        expected_start = timeline.requested_from + _FORECAST_INTERVAL * index
        if interval.observed_at != expected_start:
            return "missing_interval_coverage", None
        previous_end = interval.valid_until
    if previous_end != timeline.valid_until:
        return "missing_interval_coverage", None

    profile: list[SchedulerCarbonIntensityInterval] = []
    for index in range(scenario.intervals):
        interval_start = scenario.start_at + _FORECAST_INTERVAL * index
        interval_end = interval_start + _FORECAST_INTERVAL
        offset = interval_start - timeline.requested_from
        if offset.total_seconds() % _FORECAST_INTERVAL.total_seconds() != 0:
            return "missing_interval_coverage", None
        forecast_index = int(offset.total_seconds() // _FORECAST_INTERVAL.total_seconds())
        if forecast_index < 0 or forecast_index >= len(timeline.intervals):
            return "missing_interval_coverage", None
        interval = timeline.intervals[forecast_index]
        if interval.observed_at != interval_start or interval.valid_until != interval_end:
            return "missing_interval_coverage", None
        profile.append(
            SchedulerCarbonIntensityInterval(
                interval_start=interval_start,
                interval_end=interval_end,
                intensity_value=interval.intensity_gco2_per_kwh,
                intensity_unit=_SUPPORTED_FORECAST_UNIT,
            )
        )
    return None, tuple(profile)


def _is_utc(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() == timedelta(0)


def _is_half_hour_aligned(value: datetime) -> bool:
    return (
        value.minute in (0, 30)
        and value.second == 0
        and value.microsecond == 0
    )


def _unavailable_result(
    request: ForecastBackedDepotScenario,
    reason: ForecastInputUnavailableReason,
) -> ForecastBackedSchedulingResult:
    detail = request.forecast_unavailable_detail if reason == "forecast_unavailable" else None
    explanation = f"Forecast-backed scheduling is unavailable: {reason}."
    return ForecastBackedSchedulingResult(
        scenario_id=request.scenario.scenario_id,
        status="unavailable",
        unavailable_reason=reason,
        unavailable_detail=detail,
        input_provenance=None,
        immediate_baseline=None,
        forecast_aware=None,
        comparison_eligible=False,
        absolute_carbon_proxy_reduction=None,
        percentage_carbon_proxy_reduction=None,
        comparison_unavailable_reason=explanation,
    )
