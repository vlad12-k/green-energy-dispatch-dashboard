from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")


_MAX_CARBON_AWARE_NUMERIC_VALUE = 1_000_000_000.0

CarbonIntensityUnit = Literal["gCO2/kWh", "gCO2e/kWh"]
CarbonSourceClassification = Literal[
    "synthetic_scenario",
    "regional_forecast",
    "national_forecast",
    "observed_actual",
    "estimated_actual",
    "derived_estimate",
]


class VehicleAvailability(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    vehicle_id: str = Field(min_length=1)
    availability_start: datetime
    availability_end: datetime
    required_energy_kwh: FiniteFloat = Field(gt=0)
    charging_power_kw: FiniteFloat = Field(gt=0)

    @model_validator(mode="after")
    def validate_window(self) -> VehicleAvailability:
        _require_aware(self.availability_start, "availability_start")
        _require_aware(self.availability_end, "availability_end")
        if self.availability_start.utcoffset() != timedelta(0) or self.availability_end.utcoffset() != timedelta(0):
            raise ValueError("availability timestamps must be UTC")
        if self.availability_end <= self.availability_start:
            raise ValueError("availability_end must be after availability_start")
        return self


class DepotScenario(BaseModel):
    """Price-free scenario. Power is kW, energy is kWh, and intervals are UTC-aligned."""

    model_config = ConfigDict(extra="forbid", strict=True)

    scenario_id: str = Field(min_length=1)
    start_at: datetime
    interval_minutes: int = Field(gt=0, le=1440)
    intervals: int = Field(gt=0, le=10000)
    depot_power_kw: FiniteFloat = Field(gt=0)
    vehicles: list[VehicleAvailability] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_scenario(self) -> DepotScenario:
        _require_aware(self.start_at, "start_at")
        if self.start_at.utcoffset() != timedelta(0):
            raise ValueError("start_at must be UTC")
        if 60 % self.interval_minutes != 0 and self.interval_minutes % 60 != 0:
            raise ValueError("interval_minutes must divide evenly into an hour or be a whole number of hours")
        if len({vehicle.vehicle_id for vehicle in self.vehicles}) != len(self.vehicles):
            raise ValueError("vehicle_id values must be unique")
        horizon_end = self.horizon_end
        for vehicle in self.vehicles:
            if vehicle.availability_start < self.start_at or vehicle.availability_end > horizon_end:
                raise ValueError("availability window must be within the scenario horizon")
            if vehicle.charging_power_kw > self.depot_power_kw:
                raise ValueError("vehicle charging power cannot exceed depot power limit")
        return self

    @property
    def interval(self) -> timedelta:
        return timedelta(minutes=self.interval_minutes)

    @property
    def horizon_end(self) -> datetime:
        return self.start_at + self.interval * self.intervals


class SimulationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy: Literal["immediate_charge_in_vehicle_id_order"]
    feasible: bool
    baseline_feasible: bool
    feasible_alternative_schedule: Literal["unknown"]
    delivered_energy_kwh: dict[str, float]
    unmet_energy_kwh: dict[str, float]
    charging_kw: list[float]


@dataclass(frozen=True)
class _GreedySegment:
    interval_index: int
    start_at: datetime
    end_at: datetime
    energy_by_vehicle_kwh: tuple[tuple[str, float], ...]


@dataclass(frozen=True)
class _GreedyScheduleTrace:
    feasible: bool
    delivered_energy_kwh: dict[str, float]
    unmet_energy_kwh: dict[str, float]
    charging_kw: tuple[float, ...]
    segments: tuple[_GreedySegment, ...]


def _run_greedy_schedule(
    scenario: DepotScenario,
    *,
    earliest_deadline_first: bool,
) -> _GreedyScheduleTrace:
    interval = scenario.interval
    if earliest_deadline_first:
        ordered = sorted(
            scenario.vehicles,
            key=lambda vehicle: (vehicle.availability_end, vehicle.vehicle_id),
        )
    else:
        ordered = sorted(scenario.vehicles, key=lambda vehicle: vehicle.vehicle_id)
    remaining = {vehicle.vehicle_id: vehicle.required_energy_kwh for vehicle in scenario.vehicles}
    delivered = {vehicle.vehicle_id: 0.0 for vehicle in scenario.vehicles}
    powers = [0.0] * scenario.intervals
    segments: list[_GreedySegment] = []

    for index in range(scenario.intervals):
        start = scenario.start_at + interval * index
        end = start + interval
        boundaries = sorted(
            {
                boundary
                for vehicle in ordered
                if vehicle.availability_start < end and vehicle.availability_end > start
                for boundary in (max(start, vehicle.availability_start), min(end, vehicle.availability_end))
            }
        )
        for segment_start, segment_end in zip(boundaries, boundaries[1:]):
            segment_hours = (segment_end - segment_start).total_seconds() / 3600
            if segment_hours <= 0:
                continue
            segment_energy: dict[str, float] = {}
            active = [
                vehicle
                for vehicle in ordered
                if vehicle.availability_start <= segment_start
                and vehicle.availability_end >= segment_end
            ]
            time_remaining_hours = segment_hours
            while time_remaining_hours > 1e-12:
                rates: dict[str, float] = {}
                depot_remaining_kw = scenario.depot_power_kw
                for vehicle in active:
                    vehicle_id = vehicle.vehicle_id
                    if remaining[vehicle_id] <= 1e-12:
                        continue
                    rate_kw = min(vehicle.charging_power_kw, depot_remaining_kw)
                    if rate_kw > 0:
                        rates[vehicle_id] = rate_kw
                        depot_remaining_kw -= rate_kw
                if not rates:
                    break

                time_to_completion = min(
                    remaining[vehicle_id] / rate_kw
                    for vehicle_id, rate_kw in rates.items()
                )
                charged_hours = min(time_remaining_hours, time_to_completion)
                for vehicle_id, rate_kw in rates.items():
                    energy = min(remaining[vehicle_id], rate_kw * charged_hours)
                    remaining[vehicle_id] -= energy
                    delivered[vehicle_id] += energy
                    segment_energy[vehicle_id] = segment_energy.get(vehicle_id, 0.0) + energy
                    powers[index] += energy * 60 / scenario.interval_minutes
                time_remaining_hours -= charged_hours
            if segment_energy:
                segments.append(
                    _GreedySegment(
                        interval_index=index,
                        start_at=segment_start,
                        end_at=segment_end,
                        energy_by_vehicle_kwh=tuple(sorted(segment_energy.items())),
                    )
                )

    schedule_feasible = all(value <= 1e-9 for value in remaining.values())
    unmet = {key: round(max(value, 0.0), 9) for key, value in remaining.items()}
    return _GreedyScheduleTrace(
        feasible=schedule_feasible,
        delivered_energy_kwh={key: round(value, 9) for key, value in delivered.items()},
        unmet_energy_kwh=unmet,
        charging_kw=tuple(round(value, 9) for value in powers),
        segments=tuple(segments),
    )


def simulate_baseline(scenario: DepotScenario) -> SimulationResult:
    """Greedy baseline: vehicles by ID, charging at earliest available time."""
    trace = _run_greedy_schedule(scenario, earliest_deadline_first=False)
    return SimulationResult(
        strategy="immediate_charge_in_vehicle_id_order",
        feasible=trace.feasible,
        baseline_feasible=trace.feasible,
        feasible_alternative_schedule="unknown",
        delivered_energy_kwh=trace.delivered_energy_kwh,
        unmet_energy_kwh=trace.unmet_energy_kwh,
        charging_kw=list(trace.charging_kw),
    )


class CarbonIntensityInterval(BaseModel):
    """One explicitly labelled carbon-intensity interval for a local simulation."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    interval_start: datetime
    interval_end: datetime
    intensity_value: FiniteFloat = Field(
        ge=0,
        le=_MAX_CARBON_AWARE_NUMERIC_VALUE,
    )
    intensity_unit: CarbonIntensityUnit
    source_classification: CarbonSourceClassification
    source_id: str = Field(min_length=1, max_length=120)
    geography_code: Literal["simulation-only"]
    provenance_label: str = Field(min_length=1, max_length=240)

    @model_validator(mode="after")
    def validate_interval(self) -> CarbonIntensityInterval:
        _require_aware(self.interval_start, "interval_start")
        _require_aware(self.interval_end, "interval_end")
        if (
            self.interval_start.utcoffset() != timedelta(0)
            or self.interval_end.utcoffset() != timedelta(0)
        ):
            raise ValueError("carbon-intensity timestamps must be UTC")
        if self.interval_end <= self.interval_start:
            raise ValueError("interval_end must be after interval_start")
        return self


class CarbonAwareDepotScenario(DepotScenario):
    """Depot scenario extended with a complete synthetic carbon profile.

    The first implementation intentionally accepts synthetic inputs only; it
    does not attest or infer historical availability for external forecasts.
    """

    geography_code: Literal["simulation-only"]
    carbon_intensity_profile: list[CarbonIntensityInterval] = Field(
        min_length=1,
        max_length=96,
    )

    @model_validator(mode="after")
    def validate_carbon_profile(self) -> CarbonAwareDepotScenario:
        if self.intervals > 96:
            raise ValueError("carbon-aware scenarios are limited to 96 intervals")
        if len(self.vehicles) > 50:
            raise ValueError("carbon-aware scenarios are limited to 50 vehicles")
        if self.depot_power_kw > _MAX_CARBON_AWARE_NUMERIC_VALUE or any(
            vehicle.required_energy_kwh > _MAX_CARBON_AWARE_NUMERIC_VALUE
            or vehicle.charging_power_kw > _MAX_CARBON_AWARE_NUMERIC_VALUE
            for vehicle in self.vehicles
        ):
            raise ValueError(
                "carbon-aware energy and power inputs must not exceed "
                "1,000,000,000"
            )
        if len(self.carbon_intensity_profile) != self.intervals:
            raise ValueError("carbon-intensity profile must cover every scenario interval")

        interval = self.interval
        expected = [
            (self.start_at + interval * index, self.start_at + interval * (index + 1))
            for index in range(self.intervals)
        ]
        observed = [
            (row.interval_start, row.interval_end)
            for row in self.carbon_intensity_profile
        ]
        if len(set(observed)) != len(observed):
            raise ValueError("carbon-intensity profile contains duplicate intervals")
        if observed != expected:
            raise ValueError(
                "carbon-intensity profile intervals must align exactly with the scenario"
            )

        first = self.carbon_intensity_profile[0]
        if first.geography_code != self.geography_code:
            raise ValueError("carbon-intensity geography must match the scenario")
        if first.source_classification != "synthetic_scenario":
            raise ValueError(
                "only synthetic_scenario carbon inputs are supported; external sources "
                "require a separately verified point-in-time adapter"
            )
        if first.source_id != "synthetic-scenario":
            raise ValueError("synthetic carbon inputs must use source_id synthetic-scenario")
        if not first.provenance_label.startswith("synthetic:"):
            raise ValueError("synthetic carbon inputs require a synthetic: provenance label")

        for row in self.carbon_intensity_profile[1:]:
            if row.intensity_unit != first.intensity_unit:
                raise ValueError("carbon-intensity units must be consistent across intervals")
            if (
                row.source_classification != first.source_classification
                or row.source_id != first.source_id
                or row.geography_code != first.geography_code
                or row.provenance_label != first.provenance_label
            ):
                raise ValueError(
                    "carbon-intensity source and provenance must be consistent across intervals"
                )
        return self
