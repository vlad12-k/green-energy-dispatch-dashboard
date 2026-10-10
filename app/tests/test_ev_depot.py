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
    assert result.feasible is True
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


def test_reports_infeasible_for_concurrent_demand_beyond_depot_capacity() -> None:
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
    assert result.unmet_energy_kwh["van-2"] == 3.0
    assert result.charging_kw == [5.0, 0.0]


def test_counts_partial_availability_duration_within_interval() -> None:
    data = scenario(intervals=1)
    data["vehicles"][0]["availability_start"] += timedelta(minutes=30)
    data["vehicles"][0]["availability_end"] = data["start_at"] + timedelta(hours=1)
    data["vehicles"][0]["required_energy_kwh"] = 4
    result = simulate_baseline(DepotScenario.model_validate(data))

    assert result.feasible is False
    assert result.delivered_energy_kwh["van-1"] == 3.5
