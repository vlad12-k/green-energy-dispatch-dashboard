from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.schemas.ev_depot import (
    CarbonAwareDepotScenario,
    SimulationResult,
    _GreedyScheduleTrace,
    _run_greedy_schedule,
    simulate_baseline,
)

_ENERGY_TOLERANCE_KWH = 1e-9
_POWER_TOLERANCE_KW = 1e-9
_MAX_CARBON_SHIFT_PASSES = 2


class VehicleChargingResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    required_energy_kwh: float
    delivered_energy_kwh: float
    unmet_energy_kwh: float


class ChargingAllocation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    interval_index: int
    segment_start: datetime
    segment_end: datetime
    vehicle_id: str
    charging_power_kw: float
    energy_kwh: float
    carbon_intensity_value: float
    intensity_unit: str


class AggregateDepotLoad(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    interval_index: int
    interval_start: datetime
    interval_end: datetime
    average_power_kw: float
    peak_power_kw: float
    delivered_energy_kwh: float


class ConstraintChecks(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_bounds_respected: bool
    availability_windows_respected: bool
    vehicle_power_limits_respected: bool
    depot_power_limit_respected: bool
    energy_conservation_respected: bool


class BindingOperationalConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    depot_capacity_interval_indexes: list[int]
    vehicle_power_limit_segments: dict[str, list[int]]
    availability_window_end_used_segments: dict[str, list[int]]


class CarbonAwareSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy: Literal[
        "immediate_charge_in_vehicle_id_order",
        "carbon_aware_edf_then_bounded_carbon_shift_v1",
    ]
    feasible: bool
    feasibility_classification: Literal[
        "complete_schedule_demonstrated",
        "complete_schedule_not_demonstrated",
    ]
    total_required_energy_kwh: float
    total_delivered_energy_kwh: float
    delivered_energy_kwh: dict[str, float]
    unmet_energy_kwh: dict[str, float]
    vehicle_results: dict[str, VehicleChargingResult]
    interval_charging_kw: list[float]
    allocations: list[ChargingAllocation]
    aggregate_depot_load: list[AggregateDepotLoad]
    peak_depot_power_kw: float
    carbon_intensity_weighted_energy_proxy: float
    carbon_proxy_unit: str
    binding_operational_constraints: BindingOperationalConstraints
    constraint_checks: ConstraintChecks


class CarbonInputProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_classification: Literal["synthetic_scenario"]
    source_id: Literal["synthetic-scenario"]
    geography_code: Literal["simulation-only"]
    provenance_label: str
    intensity_unit: str
    interval_count: int


class CarbonAwareSimulationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: str
    strategy: Literal["carbon_aware_edf_then_bounded_carbon_shift_v1"]
    baseline: SimulationResult
    baseline_feasible: bool
    baseline_requirements_met_for_comparison: bool
    carbon_aware_feasible: bool
    alternative_feasible_schedule_demonstrated: bool
    baseline_delivered_energy_kwh: dict[str, float]
    baseline_unmet_energy_kwh: dict[str, float]
    baseline_allocations: list[ChargingAllocation]
    baseline_aggregate_depot_load: list[AggregateDepotLoad]
    baseline_peak_depot_power_kw: float
    baseline_binding_operational_constraints: BindingOperationalConstraints
    baseline_constraint_checks: ConstraintChecks
    carbon_aware: CarbonAwareSchedule
    baseline_carbon_proxy: float
    carbon_aware_carbon_proxy: float
    carbon_proxy_unit: str
    comparison_eligible: bool
    absolute_carbon_proxy_reduction: float | None
    percentage_carbon_proxy_reduction: float | None
    comparison_unavailable_reason: str | None
    percentage_difference_unavailable_reason: str | None
    input_provenance: CarbonInputProvenance
    real_world_emissions_reduction_claimed: Literal[False] = False


@dataclass
class _MutableSegment:
    interval_index: int
    start_at: datetime
    end_at: datetime
    carbon_intensity: float
    energy_by_vehicle_kwh: dict[str, float] = field(default_factory=dict)

    @property
    def duration_hours(self) -> float:
        return (self.end_at - self.start_at).total_seconds() / 3600

    def power_for(self, vehicle_id: str) -> float:
        return self.energy_by_vehicle_kwh.get(vehicle_id, 0.0) / self.duration_hours

    @property
    def total_power_kw(self) -> float:
        return math.fsum(
            energy / self.duration_hours
            for energy in self.energy_by_vehicle_kwh.values()
        )


def schedule_carbon_aware(
    scenario: CarbonAwareDepotScenario,
) -> CarbonAwareSimulationResult:
    """Simulate service-first EDF charging and bounded carbon-intensity shifts.

    The scheduler uses an earliest-departure-first seed, then makes at most two
    deterministic passes of energy-preserving shifts from higher- to lower-carbon
    segments with spare vehicle and depot capacity. This is a heuristic, not a
    global optimizer; failure to complete demand is reported as not demonstrated.
    """
    baseline_result = simulate_baseline(scenario)
    baseline = baseline_result.model_copy(
        update={
            "delivered_energy_kwh": dict(
                sorted(baseline_result.delivered_energy_kwh.items())
            ),
            "unmet_energy_kwh": dict(
                sorted(baseline_result.unmet_energy_kwh.items())
            ),
        }
    )
    baseline_trace = _run_greedy_schedule(
        scenario,
        earliest_deadline_first=False,
    )
    carbon_trace = _run_greedy_schedule(
        scenario,
        earliest_deadline_first=True,
    )
    segments = _build_profile_segments(scenario, carbon_trace)
    if carbon_trace.feasible:
        _shift_energy_to_lower_carbon_segments(scenario, segments)

    baseline_segments = _build_profile_segments(scenario, baseline_trace)
    baseline_report = _build_schedule_report(
        scenario,
        baseline_segments,
        strategy=baseline.strategy,
    )
    carbon_aware = _build_schedule_report(
        scenario,
        segments,
        strategy="carbon_aware_edf_then_bounded_carbon_shift_v1",
    )

    baseline_proxy = baseline_report.carbon_intensity_weighted_energy_proxy
    carbon_proxy = carbon_aware.carbon_intensity_weighted_energy_proxy
    proxy_unit = _proxy_unit(scenario.carbon_intensity_profile[0].intensity_unit)
    comparison_eligible = baseline_report.feasible and carbon_aware.feasible
    if comparison_eligible:
        absolute_reduction = baseline_proxy - carbon_proxy
        percentage_candidate = (
            absolute_reduction / baseline_proxy * 100
            if baseline_proxy > 0
            else None
        )
        percentage_reduction = (
            percentage_candidate
            if percentage_candidate is not None and math.isfinite(percentage_candidate)
            else None
        )
        comparison_reason = None
        percentage_reason = (
            "Percentage difference is undefined because the baseline proxy is zero."
            if baseline_proxy == 0
            else (
                None
                if percentage_reduction is not None
                else "Percentage difference exceeds the finite numeric range."
            )
        )
    else:
        absolute_reduction = None
        percentage_reduction = None
        comparison_reason = (
            "Equivalent-service comparison is unavailable because at least one strategy "
            "did not complete every vehicle requirement. A failed heuristic does not "
            "prove that the scenario is infeasible."
        )
        percentage_reason = None

    return CarbonAwareSimulationResult(
        scenario_id=scenario.scenario_id,
        strategy="carbon_aware_edf_then_bounded_carbon_shift_v1",
        baseline=baseline,
        baseline_feasible=baseline.baseline_feasible,
        baseline_requirements_met_for_comparison=baseline_report.feasible,
        carbon_aware_feasible=carbon_aware.feasible,
        alternative_feasible_schedule_demonstrated=(
            carbon_aware.feasible
            and (
                not baseline.baseline_feasible
                or _schedules_differ(baseline_segments, segments)
            )
        ),
        baseline_delivered_energy_kwh=baseline_report.delivered_energy_kwh,
        baseline_unmet_energy_kwh=baseline_report.unmet_energy_kwh,
        baseline_allocations=baseline_report.allocations,
        baseline_aggregate_depot_load=baseline_report.aggregate_depot_load,
        baseline_peak_depot_power_kw=baseline_report.peak_depot_power_kw,
        baseline_binding_operational_constraints=(
            baseline_report.binding_operational_constraints
        ),
        baseline_constraint_checks=baseline_report.constraint_checks,
        carbon_aware=carbon_aware,
        baseline_carbon_proxy=baseline_proxy,
        carbon_aware_carbon_proxy=carbon_proxy,
        carbon_proxy_unit=proxy_unit,
        comparison_eligible=comparison_eligible,
        absolute_carbon_proxy_reduction=absolute_reduction,
        percentage_carbon_proxy_reduction=percentage_reduction,
        comparison_unavailable_reason=comparison_reason,
        percentage_difference_unavailable_reason=percentage_reason,
        input_provenance=_input_provenance(scenario),
        real_world_emissions_reduction_claimed=False,
    )


def _build_profile_segments(
    scenario: CarbonAwareDepotScenario,
    trace: _GreedyScheduleTrace,
) -> list[_MutableSegment]:
    trace_by_segment = {
        (segment.interval_index, segment.start_at, segment.end_at): dict(
            segment.energy_by_vehicle_kwh
        )
        for segment in trace.segments
    }
    segments: list[_MutableSegment] = []
    for index, profile in enumerate(scenario.carbon_intensity_profile):
        boundaries = {profile.interval_start, profile.interval_end}
        for vehicle in scenario.vehicles:
            if (
                vehicle.availability_start < profile.interval_end
                and vehicle.availability_end > profile.interval_start
            ):
                boundaries.add(max(profile.interval_start, vehicle.availability_start))
                boundaries.add(min(profile.interval_end, vehicle.availability_end))
        ordered_boundaries = sorted(boundaries)
        for start_at, end_at in zip(ordered_boundaries, ordered_boundaries[1:]):
            key = (index, start_at, end_at)
            segments.append(
                _MutableSegment(
                    interval_index=index,
                    start_at=start_at,
                    end_at=end_at,
                    carbon_intensity=profile.intensity_value,
                    energy_by_vehicle_kwh=trace_by_segment.get(key, {}).copy(),
                )
            )
    return segments


def _shift_energy_to_lower_carbon_segments(
    scenario: CarbonAwareDepotScenario,
    segments: list[_MutableSegment],
) -> None:
    vehicles = {vehicle.vehicle_id: vehicle for vehicle in scenario.vehicles}
    ordered_vehicle_ids = sorted(vehicles)
    for _ in range(_MAX_CARBON_SHIFT_PASSES):
        changed = False
        for vehicle_id in ordered_vehicle_ids:
            vehicle = vehicles[vehicle_id]
            sources = sorted(
                range(len(segments)),
                key=lambda index: (
                    -segments[index].carbon_intensity,
                    segments[index].start_at,
                    segments[index].end_at,
                ),
            )
            targets = sorted(
                (
                    index
                    for index, segment in enumerate(segments)
                    if vehicle.availability_start <= segment.start_at
                    and vehicle.availability_end >= segment.end_at
                ),
                key=lambda index: (
                    segments[index].carbon_intensity,
                    segments[index].start_at,
                    segments[index].end_at,
                ),
            )
            for source_index in sources:
                source = segments[source_index]
                source_energy = source.energy_by_vehicle_kwh.get(vehicle_id, 0.0)
                if source_energy <= _ENERGY_TOLERANCE_KWH:
                    continue
                for target_index in targets:
                    if source_index == target_index:
                        continue
                    target = segments[target_index]
                    if source.carbon_intensity <= target.carbon_intensity:
                        continue
                    target_power = target.power_for(vehicle_id)
                    vehicle_headroom_kwh = max(
                        vehicle.charging_power_kw - target_power,
                        0.0,
                    ) * target.duration_hours
                    depot_headroom_kwh = max(
                        scenario.depot_power_kw - target.total_power_kw,
                        0.0,
                    ) * target.duration_hours
                    moved_energy = min(
                        source_energy,
                        vehicle_headroom_kwh,
                        depot_headroom_kwh,
                    )
                    if moved_energy <= _ENERGY_TOLERANCE_KWH:
                        continue
                    source.energy_by_vehicle_kwh[vehicle_id] -= moved_energy
                    if (
                        source.energy_by_vehicle_kwh[vehicle_id]
                        <= _ENERGY_TOLERANCE_KWH
                    ):
                        source.energy_by_vehicle_kwh.pop(vehicle_id, None)
                    target.energy_by_vehicle_kwh[vehicle_id] = (
                        target.energy_by_vehicle_kwh.get(vehicle_id, 0.0)
                        + moved_energy
                    )
                    source_energy -= moved_energy
                    changed = True
                    if source_energy <= _ENERGY_TOLERANCE_KWH:
                        break
        if not changed:
            break


def _request_is_satisfied(required_energy_kwh: float, delivered_energy_kwh: float) -> bool:
    return math.isclose(
        delivered_energy_kwh,
        required_energy_kwh,
        rel_tol=1e-12,
        abs_tol=2 * math.ulp(required_energy_kwh),
    )


def _build_schedule_report(
    scenario: CarbonAwareDepotScenario,
    segments: list[_MutableSegment],
    *,
    strategy: Literal[
        "immediate_charge_in_vehicle_id_order",
        "carbon_aware_edf_then_bounded_carbon_shift_v1",
    ],
) -> CarbonAwareSchedule:
    vehicle_by_id = {vehicle.vehicle_id: vehicle for vehicle in scenario.vehicles}
    delivered_unrounded = {
        vehicle_id: math.fsum(
            segment.energy_by_vehicle_kwh.get(vehicle_id, 0.0)
            for segment in segments
        )
        for vehicle_id in sorted(vehicle_by_id)
    }
    delivered = delivered_unrounded
    unmet = {
        vehicle_id: max(
            vehicle_by_id[vehicle_id].required_energy_kwh
            - delivered_unrounded[vehicle_id],
            0.0,
        )
        for vehicle_id in sorted(vehicle_by_id)
    }
    total_required = math.fsum(
        vehicle_by_id[vehicle_id].required_energy_kwh
        for vehicle_id in sorted(vehicle_by_id)
    )
    total_delivered = math.fsum(delivered_unrounded.values())
    feasible = all(
        _request_is_satisfied(
            vehicle_by_id[vehicle_id].required_energy_kwh,
            delivered_unrounded[vehicle_id],
        )
        for vehicle_id in sorted(vehicle_by_id)
    )
    interval_hours = scenario.interval.total_seconds() / 3600
    profile = scenario.carbon_intensity_profile
    loads: list[AggregateDepotLoad] = []
    interval_charging_kw: list[float] = []
    for index, profile_row in enumerate(profile):
        interval_segments = [
            segment for segment in segments if segment.interval_index == index
        ]
        energy = math.fsum(
            math.fsum(segment.energy_by_vehicle_kwh.values())
            for segment in interval_segments
        )
        peak_power = max(
            (segment.total_power_kw for segment in interval_segments),
            default=0.0,
        )
        average_power = energy / interval_hours
        interval_charging_kw.append(average_power)
        loads.append(
            AggregateDepotLoad(
                interval_index=index,
                interval_start=profile_row.interval_start,
                interval_end=profile_row.interval_end,
                average_power_kw=average_power,
                peak_power_kw=peak_power,
                delivered_energy_kwh=energy,
            )
        )
    allocations = _allocations_for_segments(scenario, segments)
    checks = _check_constraints(scenario, segments, delivered, unmet)
    carbon_proxy = math.fsum(
        math.fsum(segment.energy_by_vehicle_kwh.values())
        * segment.carbon_intensity
        for segment in segments
    )
    return CarbonAwareSchedule(
        strategy=strategy,
        feasible=feasible,
        feasibility_classification=(
            "complete_schedule_demonstrated"
            if feasible
            else "complete_schedule_not_demonstrated"
        ),
        total_required_energy_kwh=total_required,
        total_delivered_energy_kwh=total_delivered,
        delivered_energy_kwh=delivered,
        unmet_energy_kwh=unmet,
        vehicle_results={
            vehicle_id: VehicleChargingResult(
                required_energy_kwh=vehicle_by_id[vehicle_id].required_energy_kwh,
                delivered_energy_kwh=delivered[vehicle_id],
                unmet_energy_kwh=unmet[vehicle_id],
            )
            for vehicle_id in sorted(vehicle_by_id)
        },
        interval_charging_kw=interval_charging_kw,
        allocations=allocations,
        aggregate_depot_load=loads,
        peak_depot_power_kw=max(
            (load.peak_power_kw for load in loads),
            default=0.0,
        ),
        carbon_intensity_weighted_energy_proxy=carbon_proxy,
        carbon_proxy_unit=_proxy_unit(profile[0].intensity_unit),
        binding_operational_constraints=_binding_constraints(scenario, segments),
        constraint_checks=checks,
    )


def _allocations_for_segments(
    scenario: CarbonAwareDepotScenario,
    segments: list[_MutableSegment],
) -> list[ChargingAllocation]:
    profile = scenario.carbon_intensity_profile
    return [
        ChargingAllocation(
            interval_index=segment.interval_index,
            segment_start=segment.start_at,
            segment_end=segment.end_at,
            vehicle_id=vehicle_id,
            charging_power_kw=energy / segment.duration_hours,
            energy_kwh=energy,
            carbon_intensity_value=profile[segment.interval_index].intensity_value,
            intensity_unit=profile[segment.interval_index].intensity_unit,
        )
        for segment in sorted(
            segments,
            key=lambda row: (row.interval_index, row.start_at, row.end_at),
        )
        for vehicle_id, energy in sorted(segment.energy_by_vehicle_kwh.items())
        if energy > 0.0
    ]


def _check_constraints(
    scenario: CarbonAwareDepotScenario,
    segments: list[_MutableSegment],
    delivered: dict[str, float],
    unmet: dict[str, float],
) -> ConstraintChecks:
    vehicles = {vehicle.vehicle_id: vehicle for vehicle in scenario.vehicles}
    bounds_respected = True
    availability_respected = True
    vehicle_power_respected = True
    depot_power_respected = True
    for segment in segments:
        if (
            segment.start_at < scenario.start_at
            or segment.end_at > scenario.horizon_end
            or segment.end_at <= segment.start_at
        ):
            bounds_respected = False
        total_power = math.fsum(
            energy / segment.duration_hours
            for energy in segment.energy_by_vehicle_kwh.values()
        )
        for vehicle_id, energy in segment.energy_by_vehicle_kwh.items():
            vehicle = vehicles[vehicle_id]
            power = energy / segment.duration_hours
            if not (
                vehicle.availability_start <= segment.start_at
                and vehicle.availability_end >= segment.end_at
            ):
                availability_respected = False
            if power > vehicle.charging_power_kw + _POWER_TOLERANCE_KW:
                vehicle_power_respected = False
        if total_power > scenario.depot_power_kw + _POWER_TOLERANCE_KW:
            depot_power_respected = False
    energy_conserved = all(
        math.isclose(
            delivered[vehicle_id] + unmet[vehicle_id],
            vehicle.required_energy_kwh,
            rel_tol=1e-12,
            abs_tol=2 * math.ulp(vehicle.required_energy_kwh),
        )
        for vehicle_id, vehicle in vehicles.items()
    )
    return ConstraintChecks(
        scenario_bounds_respected=bounds_respected,
        availability_windows_respected=availability_respected,
        vehicle_power_limits_respected=vehicle_power_respected,
        depot_power_limit_respected=depot_power_respected,
        energy_conservation_respected=energy_conserved,
    )


def _binding_constraints(
    scenario: CarbonAwareDepotScenario,
    segments: list[_MutableSegment],
) -> BindingOperationalConstraints:
    vehicles = {vehicle.vehicle_id: vehicle for vehicle in scenario.vehicles}
    depot_indexes: set[int] = set()
    vehicle_segments: dict[str, set[int]] = {}
    window_segments: dict[str, set[int]] = {}
    for segment in segments:
        if math.isclose(
            segment.total_power_kw,
            scenario.depot_power_kw,
            rel_tol=0.0,
            abs_tol=_POWER_TOLERANCE_KW,
        ):
            depot_indexes.add(segment.interval_index)
        for vehicle_id in segment.energy_by_vehicle_kwh:
            vehicle = vehicles[vehicle_id]
            if math.isclose(
                segment.power_for(vehicle_id),
                vehicle.charging_power_kw,
                rel_tol=0.0,
                abs_tol=_POWER_TOLERANCE_KW,
            ):
                vehicle_segments.setdefault(vehicle_id, set()).add(
                    segment.interval_index
                )
            if segment.end_at == vehicle.availability_end:
                window_segments.setdefault(vehicle_id, set()).add(
                    segment.interval_index
                )
    return BindingOperationalConstraints(
        depot_capacity_interval_indexes=sorted(depot_indexes),
        vehicle_power_limit_segments={
            vehicle_id: sorted(indexes)
            for vehicle_id, indexes in sorted(vehicle_segments.items())
        },
        availability_window_end_used_segments={
            vehicle_id: sorted(indexes)
            for vehicle_id, indexes in sorted(window_segments.items())
        },
    )


def _input_provenance(scenario: CarbonAwareDepotScenario) -> CarbonInputProvenance:
    first = scenario.carbon_intensity_profile[0]
    return CarbonInputProvenance(
        source_classification=first.source_classification,
        source_id=first.source_id,
        geography_code=first.geography_code,
        provenance_label=first.provenance_label,
        intensity_unit=first.intensity_unit,
        interval_count=len(scenario.carbon_intensity_profile),
    )


def _proxy_unit(intensity_unit: str) -> str:
    return intensity_unit.removesuffix("/kWh")


def _schedules_differ(
    left: list[_MutableSegment],
    right: list[_MutableSegment],
) -> bool:
    def values(
        segments: list[_MutableSegment],
    ) -> dict[tuple[int, datetime, datetime, str], float]:
        return {
            (segment.interval_index, segment.start_at, segment.end_at, vehicle_id): energy
            for segment in segments
            for vehicle_id, energy in segment.energy_by_vehicle_kwh.items()
        }

    left_values = values(left)
    right_values = values(right)
    return any(
        not math.isclose(
            left_values.get(key, 0.0),
            right_values.get(key, 0.0),
            rel_tol=0.0,
            abs_tol=_ENERGY_TOLERANCE_KWH,
        )
        for key in left_values.keys() | right_values.keys()
    )
