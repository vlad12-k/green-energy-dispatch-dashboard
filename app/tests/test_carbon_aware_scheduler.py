from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.schemas.ev_depot import CarbonAwareDepotScenario
from app.services.carbon_aware_scheduler import schedule_carbon_aware


START = datetime(2026, 1, 1, tzinfo=UTC)


def _scenario(
    intensities: tuple[float, ...] = (400.0, 100.0),
    *,
    vehicles: list[dict] | None = None,
    depot_power_kw: float = 10.0,
    interval_minutes: int = 60,
    intensity_unit: str = "gCO2/kWh",
) -> CarbonAwareDepotScenario:
    interval = timedelta(minutes=interval_minutes)
    vehicle_inputs = vehicles or [
        {
            "vehicle_id": "van-1",
            "availability_start": START,
            "availability_end": START + interval * len(intensities),
            "required_energy_kwh": 8.0,
            "charging_power_kw": 7.0,
        }
    ]
    return CarbonAwareDepotScenario.model_validate(
        {
            "scenario_id": "synthetic-carbon-shift-demo",
            "start_at": START,
            "interval_minutes": interval_minutes,
            "intervals": len(intensities),
            "depot_power_kw": depot_power_kw,
            "geography_code": "simulation-only",
            "vehicles": vehicle_inputs,
            "carbon_intensity_profile": [
                {
                    "interval_start": START + interval * index,
                    "interval_end": START + interval * (index + 1),
                    "intensity_value": value,
                    "intensity_unit": intensity_unit,
                    "source_classification": "synthetic_scenario",
                    "source_id": "synthetic-scenario",
                    "geography_code": "simulation-only",
                    "provenance_label": "synthetic:carbon-shift-v1",
                }
                for index, value in enumerate(intensities)
            ],
        }
    )


def test_carbon_aware_schedule_improves_proxy_without_reducing_service() -> None:
    result = schedule_carbon_aware(_scenario())

    assert result.baseline.strategy == "immediate_charge_in_vehicle_id_order"
    assert result.baseline.charging_kw == [7.0, 1.0]
    assert result.carbon_aware_feasible is True
    assert result.carbon_aware.delivered_energy_kwh == {"van-1": 8.0}
    assert result.carbon_aware.unmet_energy_kwh == {"van-1": 0.0}
    assert result.carbon_aware.interval_charging_kw == [1.0, 7.0]
    assert result.comparison_eligible is True
    assert result.baseline_carbon_proxy == pytest.approx(2900.0)
    assert result.carbon_aware_carbon_proxy == pytest.approx(1100.0)
    assert result.absolute_carbon_proxy_reduction == pytest.approx(1800.0)
    assert result.percentage_carbon_proxy_reduction == pytest.approx(1800 / 2900 * 100)
    assert result.carbon_proxy_unit == "gCO2"
    assert result.real_world_emissions_reduction_claimed is False
    assert result.carbon_aware.total_delivered_energy_kwh == 8.0
    assert result.carbon_aware.binding_operational_constraints.vehicle_power_limit_segments == {
        "van-1": [1]
    }
    assert result.carbon_aware.constraint_checks.energy_conservation_respected is True
    assert result.input_provenance.source_classification == "synthetic_scenario"
    assert sum(row.energy_kwh for row in result.carbon_aware.allocations) == pytest.approx(8.0)


def test_repeated_execution_and_vehicle_input_order_are_deterministic() -> None:
    vehicles = [
        {
            "vehicle_id": vehicle_id,
            "availability_start": START,
            "availability_end": START + timedelta(hours=2),
            "required_energy_kwh": 4.0,
            "charging_power_kw": 4.0,
        }
        for vehicle_id in ("van-b", "van-a")
    ]
    forward = _scenario(
        intensities=(300.0, 100.0),
        vehicles=vehicles,
        depot_power_kw=4.0,
    )
    reversed_input = _scenario(
        intensities=(300.0, 100.0),
        vehicles=list(reversed(vehicles)),
        depot_power_kw=4.0,
    )

    first = schedule_carbon_aware(forward)
    repeated = schedule_carbon_aware(forward)
    reordered = schedule_carbon_aware(reversed_input)

    assert first.model_dump_json() == repeated.model_dump_json()
    assert first.model_dump_json() == reordered.model_dump_json()


def test_service_first_deadline_order_can_demonstrate_an_alternative_feasible_schedule() -> None:
    vehicles = [
        {
            "vehicle_id": "A-flexible",
            "availability_start": START,
            "availability_end": START + timedelta(hours=2),
            "required_energy_kwh": 4.0,
            "charging_power_kw": 4.0,
        },
        {
            "vehicle_id": "B-short-window",
            "availability_start": START,
            "availability_end": START + timedelta(hours=1),
            "required_energy_kwh": 4.0,
            "charging_power_kw": 4.0,
        },
    ]

    result = schedule_carbon_aware(
        _scenario(
            intensities=(200.0, 100.0),
            vehicles=vehicles,
            depot_power_kw=4.0,
        )
    )

    assert result.baseline_feasible is False
    assert result.carbon_aware_feasible is True
    assert result.alternative_feasible_schedule_demonstrated is True
    assert result.carbon_aware.delivered_energy_kwh == {
        "A-flexible": 4.0,
        "B-short-window": 4.0,
    }
    assert result.comparison_eligible is False
    assert result.absolute_carbon_proxy_reduction is None
    assert result.percentage_carbon_proxy_reduction is None
    assert result.comparison_unavailable_reason is not None
    assert "failed heuristic does not prove" in result.comparison_unavailable_reason


def test_failed_carbon_aware_heuristic_does_not_claim_scenario_infeasibility() -> None:
    vehicle = {
        "vehicle_id": "van-1",
        "availability_start": START,
        "availability_end": START + timedelta(hours=1),
        "required_energy_kwh": 20.0,
        "charging_power_kw": 5.0,
    }

    result = schedule_carbon_aware(
        _scenario(intensities=(100.0,), vehicles=[vehicle], depot_power_kw=5.0)
    )

    assert result.baseline_feasible is False
    assert result.carbon_aware_feasible is False
    assert result.carbon_aware.feasibility_classification == "complete_schedule_not_demonstrated"
    assert result.carbon_aware.delivered_energy_kwh == {"van-1": 5.0}
    assert result.carbon_aware.unmet_energy_kwh == {"van-1": 15.0}
    assert result.alternative_feasible_schedule_demonstrated is False
    assert result.comparison_eligible is False
    assert result.absolute_carbon_proxy_reduction is None
    assert result.carbon_aware.constraint_checks.energy_conservation_respected is True


def test_tiny_unmet_energy_is_not_reported_as_complete_or_comparable() -> None:
    vehicle = {
        "vehicle_id": "tiny-request",
        "availability_start": START,
        "availability_end": START + timedelta(hours=1),
        "required_energy_kwh": 1e-9,
        "charging_power_kw": 1e-12,
    }

    result = schedule_carbon_aware(
        _scenario(intensities=(100.0,), vehicles=[vehicle], depot_power_kw=1e-12)
    )

    assert result.baseline_feasible is True
    assert result.baseline_requirements_met_for_comparison is False
    assert result.carbon_aware_feasible is False
    assert result.comparison_eligible is False
    assert result.carbon_aware.delivered_energy_kwh["tiny-request"] == pytest.approx(1e-12)
    assert result.carbon_aware.unmet_energy_kwh["tiny-request"] == pytest.approx(0.999e-9)
    assert result.carbon_aware.allocations[0].energy_kwh == pytest.approx(
        result.carbon_aware.allocations[0].charging_power_kw
        * (
            result.carbon_aware.allocations[0].segment_end
            - result.carbon_aware.allocations[0].segment_start
        ).total_seconds()
        / 3600
    )
    assert result.comparison_unavailable_reason is not None


def test_partial_overlapping_windows_conserve_energy_and_obey_power_limits() -> None:
    vehicles = [
        {
            "vehicle_id": vehicle_id,
            "availability_start": START + timedelta(minutes=start_minute),
            "availability_end": START + timedelta(minutes=end_minute),
            "required_energy_kwh": 3.75,
            "charging_power_kw": 10.0,
        }
        for vehicle_id, start_minute, end_minute in (
            ("van-a", 0, 30),
            ("van-b", 15, 45),
        )
    ]

    result = schedule_carbon_aware(
        _scenario(intensities=(100.0,), vehicles=vehicles, depot_power_kw=10.0)
    )
    schedule = result.carbon_aware

    assert schedule.feasible is True
    assert schedule.delivered_energy_kwh == {"van-a": 3.75, "van-b": 3.75}
    assert schedule.interval_charging_kw == [7.5]
    assert schedule.peak_depot_power_kw == 10.0
    assert schedule.binding_operational_constraints.depot_capacity_interval_indexes == [0]
    assert schedule.constraint_checks.scenario_bounds_respected is True
    assert schedule.constraint_checks.availability_windows_respected is True
    assert schedule.constraint_checks.vehicle_power_limits_respected is True
    assert schedule.constraint_checks.depot_power_limit_respected is True
    assert schedule.constraint_checks.energy_conservation_respected is True
    for allocation in schedule.allocations:
        vehicle = next(item for item in vehicles if item["vehicle_id"] == allocation.vehicle_id)
        assert allocation.segment_start >= vehicle["availability_start"]
        assert allocation.segment_end <= vehicle["availability_end"]
        assert allocation.energy_kwh == pytest.approx(
            allocation.charging_power_kw
            * (allocation.segment_end - allocation.segment_start).total_seconds()
            / 3600
        )


def test_profile_must_cover_aligned_unique_utc_intervals() -> None:
    payload = _scenario().model_dump(mode="python")
    payload["carbon_intensity_profile"].pop()
    with pytest.raises(ValidationError, match="cover every scenario interval"):
        CarbonAwareDepotScenario.model_validate(payload)

    payload = _scenario().model_dump(mode="python")
    payload["carbon_intensity_profile"][1]["interval_start"] = START
    payload["carbon_intensity_profile"][1]["interval_end"] = START + timedelta(hours=1)
    with pytest.raises(ValidationError, match="duplicate intervals"):
        CarbonAwareDepotScenario.model_validate(payload)

    payload = _scenario().model_dump(mode="python")
    payload["carbon_intensity_profile"][1]["interval_start"] += timedelta(minutes=15)
    with pytest.raises(ValidationError, match="align exactly"):
        CarbonAwareDepotScenario.model_validate(payload)

    payload = _scenario().model_dump(mode="python")
    payload["carbon_intensity_profile"][0]["interval_start"] = datetime(2026, 1, 1)
    with pytest.raises(ValidationError, match="timezone"):
        CarbonAwareDepotScenario.model_validate(payload)

    payload = _scenario().model_dump(mode="python")
    payload["carbon_intensity_profile"][0]["interval_start"] = START.replace(
        tzinfo=timezone(timedelta(hours=1))
    )
    with pytest.raises(ValidationError, match="must be UTC"):
        CarbonAwareDepotScenario.model_validate(payload)


@pytest.mark.parametrize("value", [-1.0, float("nan"), float("inf")])
def test_profile_rejects_negative_or_non_finite_intensity(value: float) -> None:
    payload = _scenario().model_dump(mode="python")
    payload["carbon_intensity_profile"][0]["intensity_value"] = value

    with pytest.raises(ValidationError):
        CarbonAwareDepotScenario.model_validate(payload)


def test_carbon_aware_inputs_reject_finite_values_that_can_overflow_proxy_math() -> None:
    payload = _scenario().model_dump(mode="python")
    payload["carbon_intensity_profile"][0]["intensity_value"] = 1e308
    with pytest.raises(ValidationError):
        CarbonAwareDepotScenario.model_validate(payload)

    for field_name in ("required_energy_kwh", "charging_power_kw"):
        payload = _scenario().model_dump(mode="python")
        payload["vehicles"][0][field_name] = 1e308
        with pytest.raises(ValidationError):
            CarbonAwareDepotScenario.model_validate(payload)

    payload = _scenario().model_dump(mode="python")
    payload["depot_power_kw"] = 1e308
    with pytest.raises(ValidationError):
        CarbonAwareDepotScenario.model_validate(payload)


def test_maximum_supported_carbon_aware_values_keep_proxy_finite() -> None:
    payload = _scenario().model_dump(mode="python")
    payload["depot_power_kw"] = 1_000_000_000.0
    payload["vehicles"][0]["required_energy_kwh"] = 1_000_000_000.0
    payload["vehicles"][0]["charging_power_kw"] = 1_000_000_000.0
    for row in payload["carbon_intensity_profile"]:
        row["intensity_value"] = 1_000_000_000.0
    scenario = CarbonAwareDepotScenario.model_validate(payload)

    result = schedule_carbon_aware(scenario)

    assert result.baseline_carbon_proxy == 1e18
    assert result.carbon_aware_carbon_proxy == 1e18
    assert result.carbon_aware_feasible is True


def test_profile_rejects_mixed_units_and_unsupported_external_source_claims() -> None:
    payload = _scenario().model_dump(mode="python")
    payload["carbon_intensity_profile"][1]["intensity_unit"] = "gCO2e/kWh"
    with pytest.raises(ValidationError, match="units must be consistent"):
        CarbonAwareDepotScenario.model_validate(payload)

    payload = _scenario().model_dump(mode="python")
    payload["carbon_intensity_profile"][0]["source_classification"] = "regional_forecast"
    payload["carbon_intensity_profile"][0]["source_id"] = "neso-carbon-intensity-api"
    with pytest.raises(ValidationError, match="only synthetic_scenario"):
        CarbonAwareDepotScenario.model_validate(payload)

    payload = _scenario().model_dump(mode="python")
    payload["carbon_intensity_profile"][0]["source_id"] = "neso-carbon-intensity-api"
    with pytest.raises(ValidationError, match="source_id synthetic-scenario"):
        CarbonAwareDepotScenario.model_validate(payload)

    payload = _scenario().model_dump(mode="python")
    payload["carbon_intensity_profile"][0]["provenance_label"] = "NESO archive"
    with pytest.raises(ValidationError, match="synthetic: provenance"):
        CarbonAwareDepotScenario.model_validate(payload)

    payload = _scenario().model_dump(mode="python")
    payload["geography_code"] = "NESO-13"
    with pytest.raises(ValidationError):
        CarbonAwareDepotScenario.model_validate(payload)


def test_profile_rejects_inconsistent_provenance_and_bounded_model_limits() -> None:
    payload = _scenario().model_dump(mode="python")
    payload["carbon_intensity_profile"][1]["provenance_label"] = "synthetic:other"
    with pytest.raises(ValidationError, match="source and provenance must be consistent"):
        CarbonAwareDepotScenario.model_validate(payload)

    with pytest.raises(ValidationError, match="at most 96 items"):
        _scenario(intensities=(100.0,) * 97)

    vehicles = [
        {
            "vehicle_id": f"van-{index:02d}",
            "availability_start": START,
            "availability_end": START + timedelta(hours=2),
            "required_energy_kwh": 1.0,
            "charging_power_kw": 1.0,
        }
        for index in range(51)
    ]
    with pytest.raises(ValidationError, match="limited to 50 vehicles"):
        _scenario(vehicles=vehicles)


def test_zero_intensity_profile_difference_reports_no_proxy_improvement() -> None:
    result = schedule_carbon_aware(_scenario(intensities=(100.0, 100.0)))

    assert result.baseline_feasible is True
    assert result.carbon_aware_feasible is True
    assert result.comparison_eligible is True
    assert result.baseline_carbon_proxy == result.carbon_aware_carbon_proxy == 800.0
    assert result.absolute_carbon_proxy_reduction == 0.0
    assert result.percentage_carbon_proxy_reduction == 0.0
    assert result.alternative_feasible_schedule_demonstrated is False


def test_gco2e_proxy_unit_is_preserved_without_unit_conversion() -> None:
    result = schedule_carbon_aware(
        _scenario(intensity_unit="gCO2e/kWh")
    )

    assert result.carbon_proxy_unit == "gCO2e"
    assert result.carbon_aware.carbon_proxy_unit == "gCO2e"


def test_zero_baseline_proxy_keeps_absolute_difference_but_has_no_percentage() -> None:
    result = schedule_carbon_aware(_scenario(intensities=(0.0, 0.0)))

    assert result.comparison_eligible is True
    assert result.baseline_carbon_proxy == 0.0
    assert result.carbon_aware_carbon_proxy == 0.0
    assert result.absolute_carbon_proxy_reduction == 0.0
    assert result.percentage_carbon_proxy_reduction is None
    assert result.comparison_unavailable_reason is None
    assert result.percentage_difference_unavailable_reason is not None
    assert "baseline proxy is zero" in result.percentage_difference_unavailable_reason


def test_missing_profile_and_invalid_units_are_rejected() -> None:
    payload = _scenario().model_dump(mode="python")
    payload.pop("carbon_intensity_profile")
    with pytest.raises(ValidationError):
        CarbonAwareDepotScenario.model_validate(payload)

    payload = _scenario().model_dump(mode="python")
    payload["carbon_intensity_profile"][0]["intensity_unit"] = "gCO2/kg"
    with pytest.raises(ValidationError):
        CarbonAwareDepotScenario.model_validate(payload)


def test_cli_runs_bounded_synthetic_example_and_prints_machine_readable_result(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from app.simulate_carbon_aware_ev_depot import main

    assert main(["examples/carbon_aware_ev_depot_demo.json"]) == 0
    result = json.loads(capsys.readouterr().out)

    assert result["scenario_id"] == "synthetic-carbon-shift-demo"
    assert result["comparison_eligible"] is True
    assert result["baseline_carbon_proxy"] == 2900.0
    assert result["carbon_aware_carbon_proxy"] == 1100.0


def test_cli_rejects_unsupported_timestamp_formats(tmp_path, capsys) -> None:
    from app.simulate_carbon_aware_ev_depot import main

    payload = _scenario().model_dump(mode="json")
    payload["carbon_intensity_profile"][0]["interval_start"] = "next Tuesday"
    path = tmp_path / "bad-scenario.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SystemExit) as error:
        main([str(path)])

    assert error.value.code == 2
    assert "invalid scenario" in capsys.readouterr().err


def test_cli_rejects_oversized_scenario_files(tmp_path, capsys) -> None:
    from app.simulate_carbon_aware_ev_depot import main

    path = tmp_path / "oversized-scenario.json"
    path.write_bytes(b" " * (256 * 1024 + 1))

    with pytest.raises(SystemExit) as error:
        main([str(path)])

    assert error.value.code == 2
    assert "exceeds the 262144-byte limit" in capsys.readouterr().err


def test_cli_accepts_a_valid_file_exactly_at_the_size_limit(tmp_path, capsys) -> None:
    from app.simulate_carbon_aware_ev_depot import _MAX_SCENARIO_FILE_BYTES, main

    encoded = json.dumps(_scenario().model_dump(mode="json")).encode("utf-8")
    assert len(encoded) < _MAX_SCENARIO_FILE_BYTES
    path = tmp_path / "max-sized-scenario.json"
    path.write_bytes(encoded + b" " * (_MAX_SCENARIO_FILE_BYTES - len(encoded)))

    assert path.stat().st_size == _MAX_SCENARIO_FILE_BYTES
    assert main([str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["scenario_id"] == "synthetic-carbon-shift-demo"


def test_cli_rejects_malformed_json(tmp_path, capsys) -> None:
    from app.simulate_carbon_aware_ev_depot import main

    path = tmp_path / "malformed-scenario.json"
    path.write_text("{", encoding="utf-8")

    with pytest.raises(SystemExit) as error:
        main([str(path)])

    assert error.value.code == 2
    assert "invalid scenario" in capsys.readouterr().err
