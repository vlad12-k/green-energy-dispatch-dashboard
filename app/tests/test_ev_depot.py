from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.schemas.ev_depot import DepotScenario, simulate_baseline


def scenario(**overrides) -> dict:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    data = {
        "scenario_id": "local-example",
        "start_at": start,
        "interval_minutes": 60,
        "intervals": 2,
        "depot_power_kw": 10,
        "vehicles": [
            {
                "vehicle_id": "van-1",
                "availability_start": start,
                "availability_end": start + timedelta(hours=2),
                "required_energy_kwh": 8,
                "charging_power_kw": 7,
            }
        ],
    }
    data.update(overrides)
    return data


def test_valid_scenario_simulates_immediate_charge_baseline() -> None:
    result = simulate_baseline(DepotScenario.model_validate(scenario()))

    assert result.strategy == "immediate_charge_in_vehicle_id_order"
    assert result.baseline_feasible is True
    assert result.feasible_alternative_schedule == "unknown"
    assert result.delivered_energy_kwh == {"van-1": 8.0}
    assert result.unmet_energy_kwh == {"van-1": 0.0}
    assert result.charging_kw == [7.0, 1.0]


def test_rejects_naive_timestamps() -> None:
    data = scenario()
    data["start_at"] = datetime(2026, 1, 1)
    with pytest.raises(ValidationError, match="timezone"):
        DepotScenario.model_validate(data)


def test_rejects_vehicle_charger_above_depot_limit() -> None:
    data = scenario(depot_power_kw=6)
    with pytest.raises(ValidationError, match="cannot exceed depot"):
        DepotScenario.model_validate(data)


def test_rejects_non_utc_offsets_and_unsupported_units() -> None:
    data = scenario()
    data["vehicles"][0]["availability_start"] = datetime(
        2026, 1, 1, tzinfo=timezone(timedelta(hours=1))
    )
    with pytest.raises(ValidationError, match="must be UTC"):
        DepotScenario.model_validate(data)

    data = scenario()
    data["vehicles"][0]["required_energy_kwh"] = "8"
    with pytest.raises(ValidationError):
        DepotScenario.model_validate(data)


def test_reports_baseline_infeasible_when_concurrent_demand_exceeds_depot_capacity() -> None:
    data = scenario()
    data["depot_power_kw"] = 5
    data["vehicles"] = [
        {
            "vehicle_id": vehicle_id,
            "availability_start": data["start_at"],
            "availability_end": data["start_at"] + timedelta(hours=1),
            "required_energy_kwh": 4,
            "charging_power_kw": 4,
        }
        for vehicle_id in ("van-1", "van-2")
    ]
    result = simulate_baseline(DepotScenario.model_validate(data))

    assert result.feasible is False
    assert result.baseline_feasible is False
    assert result.feasible_alternative_schedule == "unknown"
    assert result.unmet_energy_kwh["van-2"] == 3.0
    assert result.charging_kw == [5.0, 0.0]


def test_disjoint_partial_windows_can_each_use_full_depot_power() -> None:
    data = scenario(intervals=1)
    data["vehicles"] = [
        {
            "vehicle_id": vehicle_id,
            "availability_start": data["start_at"] + timedelta(minutes=offset),
            "availability_end": data["start_at"] + timedelta(minutes=offset + 30),
            "required_energy_kwh": 5,
            "charging_power_kw": 10,
        }
        for vehicle_id, offset in (("A", 0), ("B", 30))
    ]

    result = simulate_baseline(DepotScenario.model_validate(data))

    assert result.baseline_feasible is True
    assert result.feasible_alternative_schedule == "unknown"
    assert result.delivered_energy_kwh == {"A": 5.0, "B": 5.0}
    assert result.unmet_energy_kwh == {"A": 0.0, "B": 0.0}
    assert result.charging_kw == [10.0]


def test_partial_windows_use_time_segment_specific_depot_capacity() -> None:
    data = scenario(intervals=1, depot_power_kw=10)
    data["vehicles"] = [
        {
            "vehicle_id": vehicle_id,
            "availability_start": data["start_at"] + timedelta(minutes=start),
            "availability_end": data["start_at"] + timedelta(minutes=end),
            "required_energy_kwh": energy,
            "charging_power_kw": power,
        }
        for vehicle_id, start, end, energy, power in (
            ("A", 0, 30, 5, 10),
            ("B", 15, 45, 5, 10),
        )
    ]

    result = simulate_baseline(DepotScenario.model_validate(data))

    assert result.baseline_feasible is False
    assert result.unmet_energy_kwh == {"A": 0.0, "B": 2.5}
    assert result.delivered_energy_kwh == {"A": 5.0, "B": 2.5}
    assert result.charging_kw == [7.5]


def test_unused_capacity_is_reallocated_when_vehicle_finishes_charging() -> None:
    data = scenario(intervals=1)
    data["vehicles"] = [
        {
            "vehicle_id": vehicle_id,
            "availability_start": data["start_at"],
            "availability_end": data["start_at"] + timedelta(minutes=30),
            "required_energy_kwh": energy,
            "charging_power_kw": 10,
        }
        for vehicle_id, energy in (("A", 1.25), ("B", 5.0))
    ]

    result = simulate_baseline(DepotScenario.model_validate(data))

    assert result.delivered_energy_kwh == {"A": 1.25, "B": 3.75}
    assert result.unmet_energy_kwh == {"A": 0.0, "B": 1.25}
    assert result.baseline_feasible is False
    assert result.charging_kw == [5.0]


def test_counts_partial_availability_duration_within_interval() -> None:
    data = scenario(intervals=1)
    data["vehicles"][0]["availability_start"] += timedelta(minutes=30)
    data["vehicles"][0]["availability_end"] = data["start_at"] + timedelta(hours=1)
    data["vehicles"][0]["required_energy_kwh"] = 4
    result = simulate_baseline(DepotScenario.model_validate(data))

    assert result.feasible is False
    assert result.baseline_feasible is False
    assert result.feasible_alternative_schedule == "unknown"
    assert result.delivered_energy_kwh["van-1"] == 3.5
