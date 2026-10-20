from __future__ import annotations

from dataclasses import dataclass
from itertools import product

from app.schemas.ev_depot import CarbonAwareDepotScenario

_MAX_INTERVALS = 4
_MAX_VEHICLES = 3
_MAX_POWER_QUANTA = 3
_MAX_ENERGY_QUANTA_PER_VEHICLE = 8
_MAX_EXPLORED_STATES = 10_000


@dataclass(frozen=True)
class DiscreteReferenceResult:
    grid_feasible: bool
    minimum_grid_carbon_proxy: int | None
    proxy_unit: str
    explored_state_count: int
    scope: str = "discretised_test_grid_only"


def check_discrete_reference(
    scenario: CarbonAwareDepotScenario,
) -> DiscreteReferenceResult:
    """Exhaustively check a deliberately tiny one-hour integer grid.

    Each slot is one hour; charging power and required energy use integer 1 kW
    and 1 kWh quanta; carbon intensities are non-negative integers. Availability
    boundaries must align to slot boundaries. This checker is test support only
    and makes no claim about continuous-time feasibility or optimality.
    """
    if scenario.interval_minutes != 60 or scenario.intervals > _MAX_INTERVALS:
        raise ValueError("reference checker is limited to 4 hourly intervals")
    if len(scenario.vehicles) > _MAX_VEHICLES:
        raise ValueError("reference checker is limited to 3 vehicles")
    if not _is_integer(scenario.depot_power_kw) or scenario.depot_power_kw > _MAX_POWER_QUANTA:
        raise ValueError("reference depot power must be an integer from 1 to 3 kW")
    if any(
        not _is_integer(vehicle.charging_power_kw)
        or vehicle.charging_power_kw > _MAX_POWER_QUANTA
        or not _is_integer(vehicle.required_energy_kwh)
        or vehicle.required_energy_kwh > _MAX_ENERGY_QUANTA_PER_VEHICLE
        for vehicle in scenario.vehicles
    ):
        raise ValueError("reference vehicles must use bounded integer power and energy quanta")
    if any(not _is_integer(row.intensity_value) for row in scenario.carbon_intensity_profile):
        raise ValueError("reference carbon intensities must be non-negative integers")

    slot_start_times = [
        scenario.start_at + scenario.interval * index
        for index in range(scenario.intervals + 1)
    ]
    allowed_boundaries = set(slot_start_times)
    if any(
        vehicle.availability_start not in allowed_boundaries
        or vehicle.availability_end not in allowed_boundaries
        for vehicle in scenario.vehicles
    ):
        raise ValueError("reference vehicle windows must align to hourly slot boundaries")

    power_limits = [int(vehicle.charging_power_kw) for vehicle in scenario.vehicles]
    depot_limit = int(scenario.depot_power_kw)
    initial_remaining = tuple(
        int(vehicle.required_energy_kwh)
        for vehicle in scenario.vehicles
    )
    states: dict[tuple[int, ...], int] = {initial_remaining: 0}
    explored = 1

    # Keep completed states in the DP: zero-charge transitions carry them through
    # later slots so a cheaper future intensity can still determine the optimum.
    for slot_index in range(scenario.intervals):
        start = slot_start_times[slot_index]
        end = slot_start_times[slot_index + 1]
        active_indices = [
            index
            for index, vehicle in enumerate(scenario.vehicles)
            if vehicle.availability_start <= start and vehicle.availability_end >= end
        ]
        intensity = int(scenario.carbon_intensity_profile[slot_index].intensity_value)
        next_states: dict[tuple[int, ...], int] = {}
        for remaining, proxy in states.items():
            options = [
                range(min(power_limits[index], remaining[index]) + 1)
                for index in active_indices
            ]
            for allocation in product(*options):
                if sum(allocation) > depot_limit:
                    continue
                next_remaining = list(remaining)
                for vehicle_index, power_kw in zip(active_indices, allocation, strict=True):
                    next_remaining[vehicle_index] -= power_kw
                key = tuple(next_remaining)
                next_proxy = proxy + intensity * sum(allocation)
                if next_proxy < next_states.get(key, next_proxy + 1):
                    next_states[key] = next_proxy
        explored += len(next_states)
        if explored > _MAX_EXPLORED_STATES:
            raise ValueError("reference checker exceeded its 10,000-state execution bound")
        states = next_states

    complete_proxies = [
        proxy
        for remaining, proxy in states.items()
        if not any(remaining)
    ]
    grid_feasible = bool(complete_proxies)
    return DiscreteReferenceResult(
        grid_feasible=grid_feasible,
        minimum_grid_carbon_proxy=(min(complete_proxies) if grid_feasible else None),
        proxy_unit=scenario.carbon_intensity_profile[0].intensity_unit.removesuffix("/kWh"),
        explored_state_count=explored,
    )


def _is_integer(value: float) -> bool:
    return value.is_integer()
