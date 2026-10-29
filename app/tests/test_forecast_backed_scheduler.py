"""Deterministic tests for the forecast-backed EV-depot scheduling core."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from app.schemas.ev_depot import CarbonAwareDepotScenario, DepotScenario
from app.schemas.forecast_backed_ev_depot import (
    ForecastBackedDepotScenario,
    ForecastUnavailableDetail,
    ScenarioRegionMapping,
)
from app.services.carbon_aware_scheduler import schedule_carbon_aware
from app.services.forecast_backed_scheduler import schedule_forecast_backed
from app.services.neso_carbon_intensity import parse_regional_forecast_response


START = datetime(2026, 1, 1, tzinfo=UTC)
DECISION_TIME = START - timedelta(minutes=1)
_UNSET_FORECAST = object()


def _forecast(
    *,
    values: tuple[float, ...] = (400.0, 300.0, 100.0, 50.0),
    requested_from: datetime = START,
    retrieved_at: datetime = START - timedelta(minutes=5),
):
    intervals = []
    for index in range(48):
        interval_start = requested_from + timedelta(minutes=30 * index)
        intervals.append(
            {
                "from": interval_start.strftime("%Y-%m-%dT%H:%MZ"),
                "to": (interval_start + timedelta(minutes=30)).strftime(
                    "%Y-%m-%dT%H:%MZ"
                ),
                "intensity": {
                    "forecast": values[index] if index < len(values) else 200.0,
                    "index": "moderate",
                },
            }
        )
    payload: dict[str, Any] = {
        "data": [{"regionid": 13, "shortname": "London", "data": intervals}]
    }
    return parse_regional_forecast_response(
        payload,
        region_id=13,
        requested_from=requested_from,
        retrieved_at=retrieved_at,
    )


def _depot_scenario(
    *,
    scenario_id: str = "depot-london-01",
    start_at: datetime = START,
    required_energy_kwh: float = 4.0,
    vehicles: list[dict[str, object]] | None = None,
    depot_power_kw: float = 4.0,
    intervals: int = 4,
) -> DepotScenario:
    end_at = start_at + timedelta(minutes=30 * intervals)
    vehicle_inputs = vehicles or [
        {
            "vehicle_id": "van-1",
            "availability_start": start_at,
            "availability_end": end_at,
            "required_energy_kwh": required_energy_kwh,
            "charging_power_kw": 4.0,
        }
    ]
    return DepotScenario.model_validate(
        {
            "scenario_id": scenario_id,
            "start_at": start_at,
            "interval_minutes": 30,
            "intervals": intervals,
            "depot_power_kw": depot_power_kw,
            "vehicles": vehicle_inputs,
        }
    )


def _request(
    *,
    scenario: DepotScenario | None = None,
    forecast: Any = _UNSET_FORECAST,
    forecast_unavailable_detail: ForecastUnavailableDetail | None = None,
    intensity_unit: str = "gCO2/kWh",
    decision_time: datetime = DECISION_TIME,
    max_forecast_age_seconds: int = 3600,
    mapped_scenario_id: str = "depot-london-01",
    mapped_region_id: int = 13,
) -> ForecastBackedDepotScenario:
    selected_forecast = _forecast() if forecast is _UNSET_FORECAST else forecast
    return ForecastBackedDepotScenario(
        scenario=scenario or _depot_scenario(),
        region_mapping=ScenarioRegionMapping(
            scenario_id=mapped_scenario_id,
            neso_region_id=mapped_region_id,
        ),
        forecast=selected_forecast,
        forecast_unavailable_detail=forecast_unavailable_detail,
        decision_time=decision_time,
        max_forecast_age_seconds=max_forecast_age_seconds,
        intensity_unit=intensity_unit,
    )


def test_valid_forecast_backed_schedule_compares_equal_service() -> None:
    result = schedule_forecast_backed(_request())

    assert result.status == "available"
    assert result.unavailable_reason is None
    assert result.input_provenance is not None
    assert result.immediate_baseline is not None
    assert result.forecast_aware is not None
    assert result.input_provenance.source_classification == "regional_forecast"
    assert result.input_provenance.region_id == 13
    assert result.input_provenance.region_name == "London"
    assert result.input_provenance.forecast_retrieved_at == START - timedelta(minutes=5)
    assert result.input_provenance.prediction_horizon_start == START
    assert result.input_provenance.prediction_horizon_end == START + timedelta(hours=24)
    assert result.input_provenance.intensity_unit == "gCO2/kWh"
    assert result.immediate_baseline.total_delivered_energy_kwh == pytest.approx(4.0)
    assert result.forecast_aware.total_delivered_energy_kwh == pytest.approx(4.0)
    assert result.immediate_baseline.unmet_energy_kwh == {"van-1": 0.0}
    assert result.forecast_aware.unmet_energy_kwh == {"van-1": 0.0}
    assert result.immediate_baseline.carbon_intensity_weighted_energy_proxy == pytest.approx(1400.0)
    assert result.forecast_aware.carbon_intensity_weighted_energy_proxy == pytest.approx(300.0)
    assert result.comparison_eligible is True
    assert result.absolute_carbon_proxy_reduction == pytest.approx(1100.0)
    assert result.forecast_based is True
    assert result.advisory_only is True
    assert result.measured is False
    assert result.real_world_emissions_reduction_claimed is False


def test_missing_forecast_fails_closed_with_an_explicit_reason() -> None:
    result = schedule_forecast_backed(_request(forecast=None))

    assert result.status == "unavailable"
    assert result.unavailable_reason == "missing_forecast"
    assert result.immediate_baseline is None
    assert result.forecast_aware is None
    assert result.comparison_eligible is False
    assert result.comparison_unavailable_reason is not None
    assert "missing_forecast" in result.comparison_unavailable_reason


def test_upstream_forecast_failure_is_not_replaced_by_synthetic_data() -> None:
    result = schedule_forecast_backed(
        _request(forecast=None, forecast_unavailable_detail="upstream_error")
    )

    assert result.status == "unavailable"
    assert result.unavailable_reason == "forecast_unavailable"
    assert result.unavailable_detail == "upstream_error"
    assert result.immediate_baseline is None
    assert result.forecast_aware is None


def test_stale_forecast_fails_closed_at_the_decision_time() -> None:
    timeline = _forecast(retrieved_at=START - timedelta(hours=2))

    result = schedule_forecast_backed(
        _request(forecast=timeline, max_forecast_age_seconds=300)
    )

    assert result.status == "unavailable"
    assert result.unavailable_reason == "stale_forecast"
    assert result.immediate_baseline is None


def test_freshness_limit_cannot_be_extended_beyond_one_hour() -> None:
    with pytest.raises(ValueError, match="max_forecast_age_seconds"):
        _request(max_forecast_age_seconds=3601)


def test_forecast_retrieved_after_historical_decision_is_rejected() -> None:
    timeline = _forecast(retrieved_at=START - timedelta(minutes=5))

    result = schedule_forecast_backed(
        _request(
            forecast=timeline,
            decision_time=START - timedelta(minutes=10),
        )
    )

    assert result.status == "unavailable"
    assert result.unavailable_reason == "forecast_retrieved_after_decision_time"
    assert result.input_provenance is None


def test_naive_decision_time_is_rejected() -> None:
    result = schedule_forecast_backed(
        _request(decision_time=cast(Any, START.replace(tzinfo=None)))
    )

    assert result.status == "unavailable"
    assert result.unavailable_reason == "invalid_decision_time"


def test_decision_time_cannot_follow_the_scheduling_horizon_start() -> None:
    result = schedule_forecast_backed(
        _request(decision_time=START + timedelta(minutes=15))
    )

    assert result.status == "unavailable"
    assert result.unavailable_reason == "decision_after_scenario_start"


def test_scenario_region_mapping_must_match_the_forecast_region() -> None:
    result = schedule_forecast_backed(_request(mapped_region_id=12))

    assert result.status == "unavailable"
    assert result.unavailable_reason == "forecast_region_mismatch"


def test_region_mapping_must_identify_the_scenario_being_scheduled() -> None:
    result = schedule_forecast_backed(_request(mapped_scenario_id="different-scenario"))

    assert result.status == "unavailable"
    assert result.unavailable_reason == "scenario_region_mapping_mismatch"


def test_forecast_unit_mismatch_is_not_converted_or_accepted() -> None:
    result = schedule_forecast_backed(_request(intensity_unit="gCO2e/kWh"))

    assert result.status == "unavailable"
    assert result.unavailable_reason == "intensity_unit_mismatch"


def test_unknown_forecast_unit_returns_an_explicit_unavailable_result() -> None:
    result = schedule_forecast_backed(_request(intensity_unit="gCO2/MWh"))

    assert result.status == "unavailable"
    assert result.unavailable_reason == "intensity_unit_mismatch"


def test_unbounded_finite_forecast_values_fail_closed_before_proxy_overflow() -> None:
    timeline = _forecast(values=(1_000_000_001.0,))

    result = schedule_forecast_backed(_request(forecast=timeline))

    assert result.status == "unavailable"
    assert result.unavailable_reason == "invalid_forecast_timeline"


def test_extremely_large_integer_forecast_values_fail_closed() -> None:
    timeline = _forecast()
    intervals = list(timeline.intervals)
    intervals[0] = replace(
        intervals[0],
        intensity_gco2_per_kwh=cast(Any, 10**1000),
    )

    result = schedule_forecast_backed(
        _request(forecast=replace(timeline, intervals=tuple(intervals)))
    )

    assert result.status == "unavailable"
    assert result.unavailable_reason == "invalid_forecast_timeline"


def test_overlapping_forecast_intervals_fail_closed() -> None:
    timeline = _forecast()
    intervals = list(timeline.intervals)
    intervals[2] = intervals[1]
    overlapping = replace(timeline, intervals=tuple(intervals))

    result = schedule_forecast_backed(_request(forecast=overlapping))

    assert result.status == "unavailable"
    assert result.unavailable_reason == "overlapping_forecast_intervals"


def test_actual_values_are_not_accepted_in_a_forecast_timeline() -> None:
    timeline = _forecast()
    intervals = list(timeline.intervals)
    intervals[0] = replace(intervals[0], measurement_type=cast(Any, "actual"))

    result = schedule_forecast_backed(
        _request(forecast=replace(timeline, intervals=tuple(intervals)))
    )

    assert result.status == "unavailable"
    assert result.unavailable_reason == "invalid_forecast_timeline"


def test_non_finite_forecast_values_are_not_scheduled() -> None:
    timeline = _forecast()
    intervals = list(timeline.intervals)
    intervals[0] = replace(
        intervals[0],
        intensity_gco2_per_kwh=cast(Any, float("nan")),
    )

    result = schedule_forecast_backed(
        _request(forecast=replace(timeline, intervals=tuple(intervals)))
    )

    assert result.status == "unavailable"
    assert result.unavailable_reason == "invalid_forecast_timeline"


def test_malformed_forecast_region_metadata_fails_closed() -> None:
    timeline = _forecast()
    malformed = replace(timeline, region_name=cast(Any, None))

    result = schedule_forecast_backed(_request(forecast=malformed))

    assert result.status == "unavailable"
    assert result.unavailable_reason == "invalid_forecast_source"


def test_missing_forecast_interval_coverage_fails_closed() -> None:
    timeline = _forecast()
    incomplete = replace(timeline, intervals=timeline.intervals[:-1])

    result = schedule_forecast_backed(_request(forecast=incomplete))

    assert result.status == "unavailable"
    assert result.unavailable_reason == "missing_interval_coverage"


def test_forecast_must_cover_the_entire_requested_scheduling_horizon() -> None:
    scenario_start = START - timedelta(minutes=30)
    timeline = _forecast(
        requested_from=START,
        retrieved_at=START - timedelta(minutes=45),
    )
    scenario = _depot_scenario(start_at=scenario_start)

    result = schedule_forecast_backed(
        _request(
            scenario=scenario,
            forecast=timeline,
            decision_time=START - timedelta(minutes=40),
        )
    )

    assert result.status == "unavailable"
    assert result.unavailable_reason == "missing_interval_coverage"


def test_forecast_backed_scenario_enforces_the_bounded_heuristic_vehicle_limit() -> None:
    end_at = START + timedelta(hours=2)
    vehicles: list[dict[str, object]] = [
        {
            "vehicle_id": f"van-{index:02d}",
            "availability_start": START,
            "availability_end": end_at,
            "required_energy_kwh": 0.1,
            "charging_power_kw": 1.0,
        }
        for index in range(51)
    ]

    result = schedule_forecast_backed(
        _request(scenario=_depot_scenario(vehicles=vehicles, depot_power_kw=4.0))
    )

    assert result.status == "unavailable"
    assert result.unavailable_reason == "invalid_depot_scenario"


def test_misaligned_scenario_horizon_fails_closed() -> None:
    scenario = _depot_scenario(start_at=START + timedelta(minutes=15))

    result = schedule_forecast_backed(_request(scenario=scenario))

    assert result.status == "unavailable"
    assert result.unavailable_reason == "scenario_interval_misaligned"


def test_misaligned_forecast_interval_fails_closed() -> None:
    timeline = _forecast()
    intervals = list(timeline.intervals)
    intervals[0] = replace(
        intervals[0],
        observed_at=intervals[0].observed_at + timedelta(minutes=15),
        valid_until=intervals[0].valid_until + timedelta(minutes=15),
    )

    result = schedule_forecast_backed(
        _request(forecast=replace(timeline, intervals=tuple(intervals)))
    )

    assert result.status == "unavailable"
    assert result.unavailable_reason == "misaligned_forecast_intervals"


def test_incomplete_schedules_report_unmet_energy_without_claiming_infeasibility() -> None:
    result = schedule_forecast_backed(
        _request(scenario=_depot_scenario(required_energy_kwh=10.0))
    )

    assert result.status == "available"
    assert result.comparison_eligible is False
    assert result.absolute_carbon_proxy_reduction is None
    assert result.comparison_unavailable_reason is not None
    assert "does not prove scenario infeasibility" in result.comparison_unavailable_reason
    assert result.immediate_baseline is not None
    assert result.forecast_aware is not None
    assert result.immediate_baseline.delivered_energy_kwh == {"van-1": 8.0}
    assert result.immediate_baseline.unmet_energy_kwh == {"van-1": 2.0}
    assert result.forecast_aware.delivered_energy_kwh == {"van-1": 8.0}
    assert result.forecast_aware.unmet_energy_kwh == {"van-1": 2.0}
    assert result.immediate_baseline.constraint_checks.energy_conservation_respected is True
    assert result.forecast_aware.constraint_checks.energy_conservation_respected is True
    assert result.forecast_aware.constraint_checks.depot_power_limit_respected is True


def test_deadlines_and_depot_capacity_are_respected_by_both_strategies() -> None:
    vehicles: list[dict[str, object]] = [
        {
            "vehicle_id": "A-urgent",
            "availability_start": START,
            "availability_end": START + timedelta(minutes=30),
            "required_energy_kwh": 2.0,
            "charging_power_kw": 4.0,
        },
        {
            "vehicle_id": "B-flexible",
            "availability_start": START,
            "availability_end": START + timedelta(hours=2),
            "required_energy_kwh": 2.0,
            "charging_power_kw": 4.0,
        },
    ]
    result = schedule_forecast_backed(
        _request(scenario=_depot_scenario(vehicles=vehicles, required_energy_kwh=4.0))
    )

    assert result.status == "available"
    assert result.immediate_baseline is not None
    assert result.forecast_aware is not None
    assert result.immediate_baseline.feasible is True
    assert result.forecast_aware.feasible is True
    for schedule in (result.immediate_baseline, result.forecast_aware):
        assert schedule.peak_depot_power_kw <= 4.0
        assert schedule.constraint_checks.availability_windows_respected is True
        assert schedule.constraint_checks.vehicle_power_limits_respected is True
        assert schedule.constraint_checks.depot_power_limit_respected is True
        assert schedule.constraint_checks.energy_conservation_respected is True


def test_repeated_and_reordered_vehicle_inputs_are_deterministic() -> None:
    vehicles: list[dict[str, object]] = [
        {
            "vehicle_id": vehicle_id,
            "availability_start": START,
            "availability_end": START + timedelta(hours=2),
            "required_energy_kwh": 2.0,
            "charging_power_kw": 2.0,
        }
        for vehicle_id in ("van-b", "van-a")
    ]
    forward = _request(scenario=_depot_scenario(vehicles=vehicles, depot_power_kw=2.0))
    reversed_request = _request(
        scenario=_depot_scenario(vehicles=list(reversed(vehicles)), depot_power_kw=2.0)
    )

    first = schedule_forecast_backed(forward)
    repeated = schedule_forecast_backed(forward)
    reordered = schedule_forecast_backed(reversed_request)

    assert first.model_dump_json() == repeated.model_dump_json()
    assert first.model_dump_json() == reordered.model_dump_json()


def test_existing_synthetic_scheduler_keeps_its_strict_provenance_contract() -> None:
    base = _depot_scenario()
    synthetic = CarbonAwareDepotScenario.model_validate(
        {
            **base.model_dump(),
            "geography_code": "simulation-only",
            "carbon_intensity_profile": [
                {
                    "interval_start": START + timedelta(minutes=30 * index),
                    "interval_end": START + timedelta(minutes=30 * (index + 1)),
                    "intensity_value": value,
                    "intensity_unit": "gCO2/kWh",
                    "source_classification": "synthetic_scenario",
                    "source_id": "synthetic-scenario",
                    "geography_code": "simulation-only",
                    "provenance_label": "synthetic:b2a-regression",
                }
                for index, value in enumerate((400.0, 300.0, 100.0, 50.0))
            ],
        }
    )

    result = schedule_carbon_aware(synthetic)

    assert result.input_provenance.source_classification == "synthetic_scenario"
    assert result.input_provenance.source_id == "synthetic-scenario"
    assert result.comparison_eligible is True
    assert result.baseline_carbon_proxy == pytest.approx(1400.0)
    assert result.carbon_aware_carbon_proxy == pytest.approx(300.0)
    assert result.real_world_emissions_reduction_claimed is False
