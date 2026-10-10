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
        depot_remaining = scenario.depot_power_kw
        for vehicle in ordered:
            if not (
                vehicle.availability_start < end
                and vehicle.availability_end > start
                and remaining[vehicle.vehicle_id] > 0
            ):
                continue
            # A partially overlapping time bucket is available only for its overlap duration.
            overlap_start = max(start, vehicle.availability_start)
            overlap_end = min(end, vehicle.availability_end)
            slot_hours = (overlap_end - overlap_start).total_seconds() / 3600
            vehicle_kw = min(vehicle.charging_power_kw, depot_remaining)
            energy = min(remaining[vehicle.vehicle_id], vehicle_kw * slot_hours)
            if energy <= 0:
                continue
            average_interval_kw = energy * 60 / scenario.interval_minutes
            powers[index] += average_interval_kw
            depot_remaining -= average_interval_kw
            remaining[vehicle.vehicle_id] -= energy
            delivered[vehicle.vehicle_id] += energy

    unmet = {key: round(value, 9) for key, value in remaining.items()}
    return SimulationResult(
        strategy="immediate_charge_in_vehicle_id_order",
        feasible=all(value <= 1e-9 for value in remaining.values()),
        delivered_energy_kwh={key: round(value, 9) for key, value in delivered.items()},
        unmet_energy_kwh=unmet,
        charging_kw=[round(value, 9) for value in powers],
    )
