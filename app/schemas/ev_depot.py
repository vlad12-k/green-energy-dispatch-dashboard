from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")


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


def simulate_baseline(scenario: DepotScenario) -> SimulationResult:
    """Greedy baseline: vehicles by ID, charging at earliest available time."""
    interval = scenario.interval
    remaining = {vehicle.vehicle_id: vehicle.required_energy_kwh for vehicle in scenario.vehicles}
    delivered = {vehicle.vehicle_id: 0.0 for vehicle in scenario.vehicles}
    powers = [0.0] * scenario.intervals
    ordered = sorted(scenario.vehicles, key=lambda vehicle: vehicle.vehicle_id)

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
                    powers[index] += energy * 60 / scenario.interval_minutes
                time_remaining_hours -= charged_hours

    unmet = {key: round(max(value, 0.0), 9) for key, value in remaining.items()}
    baseline_feasible = all(value <= 1e-9 for value in remaining.values())
    return SimulationResult(
        strategy="immediate_charge_in_vehicle_id_order",
        feasible=baseline_feasible,
        baseline_feasible=baseline_feasible,
        feasible_alternative_schedule="unknown",
        delivered_energy_kwh={key: round(value, 9) for key, value in delivered.items()},
        unmet_energy_kwh=unmet,
        charging_kw=[round(value, 9) for value in powers],
    )
