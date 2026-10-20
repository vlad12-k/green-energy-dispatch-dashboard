from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.evaluate_ev_depot_scenarios import main
from app.services.ev_depot_evaluation import (
    EvaluationSuite,
    compare_carbon_proxy_values,
    evaluate_suite,
    generate_maximum_supported_scenario,
    load_evaluation_suite,
    profile_segment_count,
)
from app.tests.discrete_ev_depot_reference import check_discrete_reference


EXPECTED_CATEGORIES = {
    "flexible_vehicle",
    "multiple_vehicles_identical_windows",
    "equal_deadlines",
    "different_departure_deadlines",
    "heavy_depot_competition",
    "partial_availability",
    "overlapping_windows",
    "varying_carbon_intensity",
    "constant_carbon_intensity",
    "tight_but_feasible",
    "clearly_infeasible_demand",
    "edf_feasible_baseline_fails",
    "carbon_shift_cannot_improve",
    "tiny_energy_request",
    "large_valid_numeric_inputs",
    "zero_carbon_intensity",
    "repeated_carbon_values",
}


def _single_vehicle_reference_scenario(intensity_values: list[float]):
    base = next(
        row.scenario
        for row in load_evaluation_suite().scenarios
        if row.scenario_id == "flexible-single-vehicle"
    )
    end_at = base.start_at + timedelta(hours=len(intensity_values))
    vehicle = base.vehicles[0].model_copy(
        update={
            "availability_start": base.start_at,
            "availability_end": end_at,
            "required_energy_kwh": 1.0,
            "charging_power_kw": 1.0,
        }
    )
    profile = [
        base.carbon_intensity_profile[0].model_copy(
            update={
                "interval_start": base.start_at + timedelta(hours=index),
                "interval_end": base.start_at + timedelta(hours=index + 1),
                "intensity_value": intensity,
            }
        )
        for index, intensity in enumerate(intensity_values)
    ]
    return base.model_copy(
        update={
            "intervals": len(intensity_values),
            "depot_power_kw": 1.0,
            "vehicles": [vehicle],
            "carbon_intensity_profile": profile,
        }
    )


def _results_by_id(result):
    return {scenario.scenario_id: scenario for scenario in result.scenarios}


def test_versioned_suite_covers_required_scenario_categories() -> None:
    suite = load_evaluation_suite()
    categories = {
        category
        for scenario in suite.scenarios
        for category in scenario.categories
    }

    assert len(suite.scenarios) == 11
    assert EXPECTED_CATEGORIES <= categories
    assert len({scenario.scenario_id for scenario in suite.scenarios}) == 11
    assert all(
        row.source_classification == "synthetic_scenario"
        and row.geography_code == "simulation-only"
        for case in suite.scenarios
        for row in case.scenario.carbon_intensity_profile
    )


def test_suite_rejects_mixed_proxy_units_across_scenarios() -> None:
    suite = load_evaluation_suite()
    payload = suite.model_dump(mode="json")
    for row in payload["scenarios"][1]["scenario"]["carbon_intensity_profile"]:
        row["intensity_unit"] = "gCO2e/kWh"

    with pytest.raises(ValidationError, match="must use one carbon-intensity unit"):
        EvaluationSuite.model_validate_json(json.dumps(payload))


def test_suite_preserves_gco2e_unit_labels() -> None:
    payload = load_evaluation_suite().model_dump(mode="json")
    for case in payload["scenarios"]:
        for row in case["scenario"]["carbon_intensity_profile"]:
            row["intensity_unit"] = "gCO2e/kWh"

    result = evaluate_suite(EvaluationSuite.model_validate_json(json.dumps(payload)))

    assert result.scenarios[0].input_provenance.intensity_unit == "gCO2e/kWh"
    assert all(
        strategy.carbon_proxy_unit == "gCO2e"
        for scenario in result.scenarios
        for strategy in scenario.strategies
    )
    assert all(scenario.comparison.unit == "gCO2e" for scenario in result.scenarios)


def test_vehicle_input_reordering_preserves_evaluation_results() -> None:
    suite = load_evaluation_suite()
    original = evaluate_suite(suite)
    target_id = "deadline-order-matters"
    reordered_cases = []
    for case in suite.scenarios:
        if case.scenario_id == target_id:
            reordered_scenario = case.scenario.model_copy(
                update={"vehicles": list(reversed(case.scenario.vehicles))}
            )
            case = case.model_copy(update={"scenario": reordered_scenario})
        reordered_cases.append(case)
    reordered = evaluate_suite(suite.model_copy(update={"scenarios": reordered_cases}))

    original_result = _results_by_id(original)[target_id]
    reordered_result = _results_by_id(reordered)[target_id]
    assert original_result.model_dump_json() == reordered_result.model_dump_json()


def test_evaluation_is_deterministic_and_preserves_all_constraint_results() -> None:
    suite = load_evaluation_suite()
    first = evaluate_suite(suite)
    repeated = evaluate_suite(suite)

    assert first.model_dump_json() == repeated.model_dump_json()
    assert first.summary.scenarios_evaluated == len(suite.scenarios)
    assert first.summary.feasible_schedule_demonstrated == 8
    assert first.summary.scenario_proven_infeasible == 1
    assert first.summary.feasibility_not_proven == 2
    assert first.summary.equivalent_service_comparisons == 7
    assert first.summary.proxy_improvements == 3
    assert first.summary.proxy_neutral_results == 4
    assert first.summary.proxy_regressions == 0
    cases_by_id = {case.scenario_id: case for case in suite.scenarios}
    for scenario in first.scenarios:
        input_case = cases_by_id[scenario.scenario_id].scenario
        assert len(scenario.input_summary.carbon_intensity_profile) == input_case.intervals
        assert set(scenario.input_summary.vehicles) == {
            vehicle.vehicle_id for vehicle in input_case.vehicles
        }
        assert [row.intensity_value for row in scenario.input_summary.carbon_intensity_profile] == [
            row.intensity_value for row in input_case.carbon_intensity_profile
        ]
        assert [row.strategy for row in scenario.strategies] == [
            "immediate_charge_in_vehicle_id_order",
            "carbon_aware_edf_then_bounded_carbon_shift_v1",
        ]
        for strategy in scenario.strategies:
            assert strategy.constraint_checks.energy_conservation_respected is True
            assert strategy.constraints_respected is True
            assert strategy.required_energy_kwh == pytest.approx(
                strategy.delivered_energy_kwh + strategy.unmet_energy_kwh
            )
        assert scenario.comparison.eligible == all(
            strategy.complete for strategy in scenario.strategies
        )
        if not scenario.comparison.eligible:
            assert scenario.comparison.signed_proxy_difference is None
            assert scenario.comparison.percentage_difference is None
            assert scenario.comparison.unavailable_reason


def test_evaluation_distinguishes_demonstrated_and_proven_feasibility() -> None:
    results = _results_by_id(evaluate_suite(load_evaluation_suite()))
    deadline_case = results["deadline-order-matters"]
    impossible_case = results["individual-capacity-infeasible"]

    assert deadline_case.strategies[0].complete is False
    assert deadline_case.strategies[1].complete is True
    assert deadline_case.scenario_feasibility == "feasible_schedule_demonstrated"
    assert deadline_case.comparison.eligible is False
    assert impossible_case.scenario_feasibility == "scenario_proven_infeasible"
    assert impossible_case.infeasibility_certificate is not None
    assert "charger" in impossible_case.infeasibility_certificate.reason
    assert impossible_case.strategies[0].complete is False
    assert impossible_case.strategies[1].complete is False


def test_vehicle_id_order_can_change_the_legacy_baseline_outcome() -> None:
    suite = load_evaluation_suite()
    target_id = "deadline-order-matters"
    original_case = next(case for case in suite.scenarios if case.scenario_id == target_id)
    renamed_vehicles = [
        vehicle.model_copy(
            update={
                "vehicle_id": {
                    "A-flexible": "Z-flexible",
                    "B-short-window": "A-short-window",
                }[vehicle.vehicle_id]
            }
        )
        for vehicle in original_case.scenario.vehicles
    ]
    renamed_scenario = original_case.scenario.model_copy(
        update={"vehicles": renamed_vehicles}
    )
    renamed_case = original_case.model_copy(update={"scenario": renamed_scenario})
    renamed_cases = [
        renamed_case if case.scenario_id == target_id else case
        for case in suite.scenarios
    ]

    original = _results_by_id(evaluate_suite(suite))[target_id]
    renamed = _results_by_id(
        evaluate_suite(suite.model_copy(update={"scenarios": renamed_cases}))
    )[target_id]

    assert original.strategies[0].complete is False
    assert renamed.strategies[0].complete is True
    assert original.strategies[1].complete is True
    assert renamed.strategies[1].complete is True


def test_evaluation_reports_positive_and_zero_proxy_differences() -> None:
    result = evaluate_suite(load_evaluation_suite())
    results = _results_by_id(result)
    improved = results["flexible-single-vehicle"]
    unchanged = results["constant-carbon-no-improvement"]
    zero_proxy = results["tiny-energy-boundary"]

    assert improved.comparison.eligible is True
    assert improved.comparison.signed_proxy_difference > 0
    assert unchanged.comparison.eligible is True
    assert unchanged.comparison.signed_proxy_difference == 0
    assert zero_proxy.comparison.eligible is True
    assert zero_proxy.comparison.signed_proxy_difference == 0
    assert zero_proxy.comparison.percentage_difference is None
    assert zero_proxy.comparison.percentage_unavailable_reason is not None
    assert result.summary.proxy_regressions == sum(
        scenario.comparison.signed_proxy_difference < 0
        for scenario in results.values()
        if scenario.comparison.eligible
        and scenario.comparison.signed_proxy_difference is not None
    )


def test_proxy_comparison_preserves_negative_difference() -> None:
    comparison = compare_carbon_proxy_values(
        baseline_proxy=100.0,
        carbon_aware_proxy=110.0,
        proxy_unit="gCO2",
        equivalent_service=True,
    )

    assert comparison.eligible is True
    assert comparison.signed_proxy_difference == -10.0
    assert comparison.percentage_difference == -10.0


def test_large_value_float_shortfall_is_not_eligible_as_complete_service() -> None:
    suite = load_evaluation_suite()
    target_id = "maximum-numeric-boundary"
    changed_cases = []
    for case in suite.scenarios:
        if case.scenario_id == target_id:
            vehicle = case.scenario.vehicles[0].model_copy(
                update={"charging_power_kw": 999_999_999.9995}
            )
            scenario = case.scenario.model_copy(
                update={
                    "depot_power_kw": 999_999_999.9995,
                    "vehicles": [vehicle],
                }
            )
            case = case.model_copy(update={"scenario": scenario})
        changed_cases.append(case)

    result = _results_by_id(
        evaluate_suite(suite.model_copy(update={"scenarios": changed_cases}))
    )[target_id]

    assert result.strategies[0].complete is False
    assert result.strategies[1].complete is False
    assert result.comparison.eligible is False
    assert result.comparison.signed_proxy_difference is None
    assert result.strategies[0].per_vehicle["maximum-1"].unmet_energy_kwh == pytest.approx(
        0.0005,
        abs=1e-7,
    )


def test_maximum_scenario_respects_the_real_segment_bound() -> None:
    scenario = generate_maximum_supported_scenario()

    assert scenario.intervals == 96
    assert len(scenario.vehicles) == 50
    assert profile_segment_count(scenario) <= 96 + 2 * 50


def test_default_cli_emits_reproducible_machine_readable_json(capsys) -> None:
    assert main([]) == 0
    first = capsys.readouterr().out
    assert main([]) == 0
    second = capsys.readouterr().out

    assert first == second
    parsed = json.loads(first)
    assert parsed["suite_id"] == "ev-depot-synthetic-evaluation"
    assert parsed["summary"]["scenarios_evaluated"] == 11


def test_cli_rejects_oversized_suite_files(tmp_path: Path, capsys) -> None:
    path = tmp_path / "oversized-suite.json"
    path.write_bytes(b" " * (512 * 1024 + 1))

    with pytest.raises(SystemExit) as error:
        main(["--suite", str(path)])

    assert error.value.code == 2
    assert "exceeds the 524288-byte limit" in capsys.readouterr().err


def test_discrete_reference_checker_reports_only_grid_feasibility_and_optimum() -> None:
    suite = load_evaluation_suite()
    simple = next(
        row.scenario
        for row in suite.scenarios
        if row.scenario_id == "constant-carbon-no-improvement"
    )
    reference = check_discrete_reference(simple)

    assert reference.grid_feasible is True
    assert reference.minimum_grid_carbon_proxy == 400
    assert reference.proxy_unit == "gCO2"
    assert reference.scope == "discretised_test_grid_only"


def test_discrete_reference_can_find_grid_schedule_when_heuristics_do_not() -> None:
    suite = load_evaluation_suite()
    case = next(
        row for row in suite.scenarios if row.scenario_id == "identical-window-fleet"
    )
    evaluated = _results_by_id(evaluate_suite(suite))[case.scenario_id]
    reference = check_discrete_reference(case.scenario)

    assert reference.grid_feasible is True
    assert evaluated.scenario_feasibility == "feasibility_not_proven"
    assert all(strategy.complete is False for strategy in evaluated.strategies)


def test_discrete_reference_returns_cheaper_later_slot_for_exact_counterexample() -> None:
    scenario = _single_vehicle_reference_scenario([400.0, 100.0])

    result = check_discrete_reference(scenario)

    assert result.grid_feasible is True
    assert result.minimum_grid_carbon_proxy == 100
    assert result.proxy_unit == "gCO2"


def test_discrete_reference_does_not_stop_at_first_early_completion() -> None:
    scenario = _single_vehicle_reference_scenario([500.0, 200.0, 75.0])

    result = check_discrete_reference(scenario)

    assert result.grid_feasible is True
    assert result.minimum_grid_carbon_proxy == 75


def test_discrete_reference_returns_deterministic_optimum() -> None:
    scenario = _single_vehicle_reference_scenario([400.0, 100.0])

    first = check_discrete_reference(scenario)
    second = check_discrete_reference(scenario)

    assert first == second
    assert first.minimum_grid_carbon_proxy == 100


def test_discrete_reference_checker_rejects_out_of_scope_instances() -> None:
    suite = load_evaluation_suite()
    impossible = next(
        row.scenario
        for row in suite.scenarios
        if row.scenario_id == "individual-capacity-infeasible"
    )
    infeasible_reference = check_discrete_reference(impossible)
    assert infeasible_reference.grid_feasible is False
    assert infeasible_reference.minimum_grid_carbon_proxy is None

    large_scenario = generate_maximum_supported_scenario()
    with pytest.raises(ValueError, match="limited to 4 hourly intervals"):
        check_discrete_reference(large_scenario)
