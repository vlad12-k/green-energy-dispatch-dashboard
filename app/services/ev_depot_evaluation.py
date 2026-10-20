from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.ev_depot import CarbonAwareDepotScenario, _run_greedy_schedule
from app.services.carbon_aware_scheduler import (
    BindingOperationalConstraints,
    CarbonAwareSimulationResult,
    CarbonInputProvenance,
    ConstraintChecks,
    schedule_carbon_aware,
    _build_profile_segments,
    _request_is_satisfied,
)

MAX_EVALUATION_SUITE_BYTES = 512 * 1024
MAX_EVALUATION_SCENARIOS = 16
_EVALUATION_VERSION = "1.0"
_SUITE_ID = "ev-depot-synthetic-evaluation"
_DEFAULT_SUITE_PATH = (
    Path(__file__).resolve().parents[2]
    / "examples"
    / "ev_depot_evaluation_scenarios.json"
)


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=32)
    categories: list[str] = Field(min_length=1, max_length=16)
    description: str = Field(min_length=1, max_length=600)
    assumptions: list[str] = Field(min_length=1, max_length=8)
    scenario: CarbonAwareDepotScenario

    @model_validator(mode="after")
    def validate_identity(self) -> EvaluationCase:
        if self.scenario_id != self.scenario.scenario_id:
            raise ValueError("evaluation scenario_id must match the embedded depot scenario")
        if len(set(self.categories)) != len(self.categories):
            raise ValueError("evaluation scenario categories must be unique")
        if any(not category.replace("_", "").isalnum() for category in self.categories):
            raise ValueError("scenario categories must contain only letters, digits and underscores")
        return self


class EvaluationSuite(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    suite_id: Literal["ev-depot-synthetic-evaluation"]
    suite_version: Literal["1.0"]
    source_classification: Literal["synthetic_scenario"]
    source_id: Literal["synthetic-scenario"]
    geography_code: Literal["simulation-only"]
    provenance_label: str = Field(min_length=1, max_length=240)
    scenarios: list[EvaluationCase] = Field(
        min_length=1,
        max_length=MAX_EVALUATION_SCENARIOS,
    )

    @model_validator(mode="after")
    def validate_suite(self) -> EvaluationSuite:
        if not self.provenance_label.startswith("synthetic:"):
            raise ValueError("evaluation suite provenance must begin with synthetic:")
        if len({case.scenario_id for case in self.scenarios}) != len(self.scenarios):
            raise ValueError("evaluation suite scenario identifiers must be unique")
        units = {
            row.intensity_unit
            for case in self.scenarios
            for row in case.scenario.carbon_intensity_profile
        }
        if len(units) != 1:
            raise ValueError("evaluation suite must use one carbon-intensity unit")
        if any(
            row.provenance_label != self.provenance_label
            for case in self.scenarios
            for row in case.scenario.carbon_intensity_profile
        ):
            raise ValueError("suite and scenario provenance labels must match")
        return self


class VehicleInputSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    availability_start: datetime
    availability_end: datetime
    required_energy_kwh: float
    charging_power_limit_kw: float


class CarbonIntervalInputSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    interval_start: datetime
    interval_end: datetime
    intensity_value: float
    intensity_unit: str


class EvaluationInputSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    start_at: datetime
    horizon_end: datetime
    interval_minutes: int
    interval_count: int
    vehicle_count: int
    depot_power_limit_kw: float
    carbon_intensity_unit: str
    vehicles: dict[str, VehicleInputSummary]
    carbon_intensity_profile: list[CarbonIntervalInputSummary]


class VehicleEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    required_energy_kwh: float
    delivered_energy_kwh: float
    unmet_energy_kwh: float
    complete: bool


class StrategyEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy: Literal[
        "immediate_charge_in_vehicle_id_order",
        "carbon_aware_edf_then_bounded_carbon_shift_v1",
    ]
    complete: bool
    completion_classification: Literal[
        "complete_schedule_demonstrated",
        "complete_schedule_not_demonstrated",
    ]
    legacy_baseline_feasible: bool | None
    required_energy_kwh: float
    delivered_energy_kwh: float
    unmet_energy_kwh: float
    per_vehicle: dict[str, VehicleEvaluation]
    constraint_checks: ConstraintChecks
    constraints_respected: bool
    peak_depot_power_kw: float
    aggregate_capacity_utilisation_fraction: float
    carbon_proxy: float
    carbon_proxy_unit: str
    binding_operational_constraints: BindingOperationalConstraints


class InfeasibilityCertificate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    vehicle_id: str
    required_energy_kwh: float
    maximum_charger_delivery_kwh: float
    minimum_unmet_energy_kwh: float
    reason: str


class ProxyComparison(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    eligible: bool
    signed_proxy_difference: float | None
    percentage_difference: float | None
    unit: str
    unavailable_reason: str | None
    percentage_unavailable_reason: str | None


class ScenarioEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: str
    scenario_version: str
    categories: list[str]
    description: str
    assumptions: list[str]
    input_summary: EvaluationInputSummary
    input_provenance: CarbonInputProvenance
    scenario_feasibility: Literal[
        "feasible_schedule_demonstrated",
        "scenario_proven_infeasible",
        "feasibility_not_proven",
    ]
    infeasibility_certificate: InfeasibilityCertificate | None
    strategies: list[StrategyEvaluation]
    comparison: ProxyComparison


class EvaluationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scenarios_evaluated: int
    feasible_schedule_demonstrated: int
    scenario_proven_infeasible: int
    feasibility_not_proven: int
    equivalent_service_comparisons: int
    proxy_improvements: int
    proxy_neutral_results: int
    proxy_regressions: int


class EvaluationSuiteResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    suite_id: Literal["ev-depot-synthetic-evaluation"]
    suite_version: Literal["1.0"]
    evaluation_version: Literal["1.0"]
    scenarios: list[ScenarioEvaluation]
    summary: EvaluationSummary


def load_evaluation_suite(path: str | Path | None = None) -> EvaluationSuite:
    """Read and validate a size-bounded, synthetic-only scenario suite."""
    suite_path = Path(path) if path is not None else _DEFAULT_SUITE_PATH
    with suite_path.open("rb") as suite_file:
        raw = suite_file.read(MAX_EVALUATION_SUITE_BYTES + 1)
    if len(raw) > MAX_EVALUATION_SUITE_BYTES:
        raise ValueError(
            f"evaluation suite exceeds the {MAX_EVALUATION_SUITE_BYTES}-byte limit"
        )
    return EvaluationSuite.model_validate_json(raw)


def evaluate_suite(suite: EvaluationSuite) -> EvaluationSuiteResult:
    """Evaluate both existing strategies for every validated suite case."""
    scenarios = [
        _evaluate_case(case)
        for case in suite.scenarios
    ]
    eligible_differences = [
        row.comparison.signed_proxy_difference
        for row in scenarios
        if row.comparison.eligible
    ]
    complete_count = sum(
        row.scenario_feasibility == "feasible_schedule_demonstrated"
        for row in scenarios
    )
    infeasible_count = sum(
        row.scenario_feasibility == "scenario_proven_infeasible"
        for row in scenarios
    )
    return EvaluationSuiteResult(
        suite_id=suite.suite_id,
        suite_version=suite.suite_version,
        evaluation_version=_EVALUATION_VERSION,
        scenarios=scenarios,
        summary=EvaluationSummary(
            scenarios_evaluated=len(scenarios),
            feasible_schedule_demonstrated=complete_count,
            scenario_proven_infeasible=infeasible_count,
            feasibility_not_proven=len(scenarios) - complete_count - infeasible_count,
            equivalent_service_comparisons=len(eligible_differences),
            proxy_improvements=sum(value is not None and value > 0 for value in eligible_differences),
            proxy_neutral_results=sum(value == 0 for value in eligible_differences),
            proxy_regressions=sum(value is not None and value < 0 for value in eligible_differences),
        ),
    )


def profile_segment_count(scenario: CarbonAwareDepotScenario) -> int:
    """Return the EDF seed's temporal segment count for boundedness checks."""
    trace = _run_greedy_schedule(scenario, earliest_deadline_first=True)
    return len(_build_profile_segments(scenario, trace))


def generate_maximum_supported_scenario() -> CarbonAwareDepotScenario:
    """Build a deterministic 96-interval/50-vehicle performance scenario."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    horizon = start + timedelta(hours=96)
    vehicles = [
        {
            "vehicle_id": f"max-vehicle-{index:02d}",
            "availability_start": start + timedelta(minutes=index + 1),
            "availability_end": horizon - timedelta(minutes=index + 1),
            "required_energy_kwh": 24.0,
            "charging_power_kw": 1.0,
        }
        for index in range(50)
    ]
    profile = [
        {
            "interval_start": start + timedelta(hours=index),
            "interval_end": start + timedelta(hours=index + 1),
            "intensity_value": 400.0 if index % 2 == 0 else 100.0,
            "intensity_unit": "gCO2/kWh",
            "source_classification": "synthetic_scenario",
            "source_id": "synthetic-scenario",
            "geography_code": "simulation-only",
            "provenance_label": "synthetic:maximum-supported-performance-v1",
        }
        for index in range(96)
    ]
    return CarbonAwareDepotScenario.model_validate(
        {
            "scenario_id": "synthetic-maximum-supported-performance",
            "start_at": start,
            "interval_minutes": 60,
            "intervals": 96,
            "depot_power_kw": 50.0,
            "vehicles": vehicles,
            "geography_code": "simulation-only",
            "carbon_intensity_profile": profile,
        }
    )


def _evaluate_case(case: EvaluationCase) -> ScenarioEvaluation:
    scenario = case.scenario
    result = schedule_carbon_aware(scenario)
    baseline = _baseline_evaluation(scenario, result)
    carbon_aware = _carbon_aware_evaluation(scenario, result)
    comparison = _comparison(baseline, carbon_aware)
    certificate = _individual_capacity_certificate(scenario)
    if certificate is not None:
        feasibility = "scenario_proven_infeasible"
    elif baseline.complete or carbon_aware.complete:
        feasibility = "feasible_schedule_demonstrated"
    else:
        feasibility = "feasibility_not_proven"
    return ScenarioEvaluation(
        scenario_id=case.scenario_id,
        scenario_version=case.version,
        categories=list(case.categories),
        description=case.description,
        assumptions=list(case.assumptions),
        input_summary=EvaluationInputSummary(
            start_at=scenario.start_at,
            horizon_end=scenario.horizon_end,
            interval_minutes=scenario.interval_minutes,
            interval_count=scenario.intervals,
            vehicle_count=len(scenario.vehicles),
            depot_power_limit_kw=scenario.depot_power_kw,
            carbon_intensity_unit=scenario.carbon_intensity_profile[0].intensity_unit,
            vehicles={
                vehicle.vehicle_id: VehicleInputSummary(
                    availability_start=vehicle.availability_start,
                    availability_end=vehicle.availability_end,
                    required_energy_kwh=vehicle.required_energy_kwh,
                    charging_power_limit_kw=vehicle.charging_power_kw,
                )
                for vehicle in sorted(scenario.vehicles, key=lambda row: row.vehicle_id)
            },
            carbon_intensity_profile=[
                CarbonIntervalInputSummary(
                    interval_start=row.interval_start,
                    interval_end=row.interval_end,
                    intensity_value=row.intensity_value,
                    intensity_unit=row.intensity_unit,
                )
                for row in scenario.carbon_intensity_profile
            ],
        ),
        input_provenance=result.input_provenance,
        scenario_feasibility=feasibility,
        infeasibility_certificate=certificate,
        strategies=[baseline, carbon_aware],
        comparison=comparison,
    )


def _baseline_evaluation(
    scenario: CarbonAwareDepotScenario,
    result: CarbonAwareSimulationResult,
) -> StrategyEvaluation:
    required = {
        vehicle.vehicle_id: vehicle.required_energy_kwh
        for vehicle in scenario.vehicles
    }
    per_vehicle = _vehicle_evaluation(
        required,
        result.baseline_delivered_energy_kwh,
        result.baseline_unmet_energy_kwh,
    )
    return StrategyEvaluation(
        strategy=result.baseline.strategy,
        complete=result.baseline_requirements_met_for_comparison,
        completion_classification=(
            "complete_schedule_demonstrated"
            if result.baseline_requirements_met_for_comparison
            else "complete_schedule_not_demonstrated"
        ),
        legacy_baseline_feasible=result.baseline_feasible,
        required_energy_kwh=math.fsum(required.values()),
        delivered_energy_kwh=math.fsum(result.baseline_delivered_energy_kwh.values()),
        unmet_energy_kwh=math.fsum(result.baseline_unmet_energy_kwh.values()),
        per_vehicle=per_vehicle,
        constraint_checks=result.baseline_constraint_checks,
        constraints_respected=_all_constraints_respected(result.baseline_constraint_checks),
        peak_depot_power_kw=result.baseline_peak_depot_power_kw,
        aggregate_capacity_utilisation_fraction=_capacity_utilisation(
            scenario,
            math.fsum(result.baseline_delivered_energy_kwh.values()),
        ),
        carbon_proxy=result.baseline_carbon_proxy,
        carbon_proxy_unit=result.carbon_proxy_unit,
        binding_operational_constraints=result.baseline_binding_operational_constraints,
    )


def _carbon_aware_evaluation(
    scenario: CarbonAwareDepotScenario,
    result: CarbonAwareSimulationResult,
) -> StrategyEvaluation:
    schedule = result.carbon_aware
    required = {
        vehicle.vehicle_id: vehicle.required_energy_kwh
        for vehicle in scenario.vehicles
    }
    per_vehicle = _vehicle_evaluation(
        required,
        schedule.delivered_energy_kwh,
        schedule.unmet_energy_kwh,
    )
    return StrategyEvaluation(
        strategy=schedule.strategy,
        complete=schedule.feasible,
        completion_classification=schedule.feasibility_classification,
        legacy_baseline_feasible=None,
        required_energy_kwh=schedule.total_required_energy_kwh,
        delivered_energy_kwh=schedule.total_delivered_energy_kwh,
        unmet_energy_kwh=math.fsum(schedule.unmet_energy_kwh.values()),
        per_vehicle=per_vehicle,
        constraint_checks=schedule.constraint_checks,
        constraints_respected=_all_constraints_respected(schedule.constraint_checks),
        peak_depot_power_kw=schedule.peak_depot_power_kw,
        aggregate_capacity_utilisation_fraction=_capacity_utilisation(
            scenario,
            schedule.total_delivered_energy_kwh,
        ),
        carbon_proxy=schedule.carbon_intensity_weighted_energy_proxy,
        carbon_proxy_unit=schedule.carbon_proxy_unit,
        binding_operational_constraints=schedule.binding_operational_constraints,
    )


def _vehicle_evaluation(
    required: dict[str, float],
    delivered: dict[str, float],
    unmet: dict[str, float],
) -> dict[str, VehicleEvaluation]:
    return {
        vehicle_id: VehicleEvaluation(
            required_energy_kwh=required[vehicle_id],
            delivered_energy_kwh=delivered[vehicle_id],
            unmet_energy_kwh=unmet[vehicle_id],
            complete=_request_is_satisfied(required[vehicle_id], delivered[vehicle_id]),
        )
        for vehicle_id in sorted(required)
    }


def _all_constraints_respected(checks: ConstraintChecks) -> bool:
    return all(checks.model_dump().values())


def _capacity_utilisation(
    scenario: CarbonAwareDepotScenario,
    delivered_energy_kwh: float,
) -> float:
    available_capacity_kwh = (
        scenario.depot_power_kw
        * (scenario.interval.total_seconds() / 3600)
        * scenario.intervals
    )
    return delivered_energy_kwh / available_capacity_kwh


def _individual_capacity_certificate(
    scenario: CarbonAwareDepotScenario,
) -> InfeasibilityCertificate | None:
    for vehicle in sorted(scenario.vehicles, key=lambda row: row.vehicle_id):
        availability_hours = (
            vehicle.availability_end - vehicle.availability_start
        ).total_seconds() / 3600
        maximum_delivery = vehicle.charging_power_kw * availability_hours
        if not _request_is_satisfied(vehicle.required_energy_kwh, maximum_delivery):
            return InfeasibilityCertificate(
                vehicle_id=vehicle.vehicle_id,
                required_energy_kwh=vehicle.required_energy_kwh,
                maximum_charger_delivery_kwh=maximum_delivery,
                minimum_unmet_energy_kwh=max(
                    vehicle.required_energy_kwh - maximum_delivery,
                    0.0,
                ),
                reason=(
                    "The required energy exceeds this vehicle's individual charger "
                    "power multiplied by its entire availability duration."
                ),
            )
    return None


def compare_carbon_proxy_values(
    baseline_proxy: float,
    carbon_aware_proxy: float,
    proxy_unit: str,
    *,
    equivalent_service: bool,
) -> ProxyComparison:
    """Return signed proxy change; positive means the candidate proxy is lower."""
    if proxy_unit not in {"gCO2", "gCO2e"}:
        raise ValueError("carbon proxy unit must be gCO2 or gCO2e")
    if any(
        not math.isfinite(value) or value < 0
        for value in (baseline_proxy, carbon_aware_proxy)
    ):
        raise ValueError("carbon proxies must be finite and non-negative")
    if not equivalent_service:
        return ProxyComparison(
            eligible=False,
            signed_proxy_difference=None,
            percentage_difference=None,
            unit=proxy_unit,
            unavailable_reason=(
                "Equivalent-service comparison is unavailable because at least one "
                "strategy did not complete every vehicle requirement. Lower delivered "
                "energy is not a carbon-proxy improvement."
            ),
            percentage_unavailable_reason=None,
        )
    difference = baseline_proxy - carbon_aware_proxy
    if baseline_proxy == 0.0:
        percentage = None
        percentage_reason = "Percentage difference is undefined because the baseline proxy is zero."
    else:
        candidate_percentage = difference / baseline_proxy * 100
        if math.isfinite(candidate_percentage):
            percentage = candidate_percentage
            percentage_reason = None
        else:
            percentage = None
            percentage_reason = (
                "Percentage difference is unavailable because the calculated result "
                "is outside finite numeric range."
            )
    return ProxyComparison(
        eligible=True,
        signed_proxy_difference=difference,
        percentage_difference=percentage,
        unit=proxy_unit,
        unavailable_reason=None,
        percentage_unavailable_reason=percentage_reason,
    )


def _comparison(
    baseline: StrategyEvaluation,
    carbon_aware: StrategyEvaluation,
) -> ProxyComparison:
    return compare_carbon_proxy_values(
        baseline_proxy=baseline.carbon_proxy,
        carbon_aware_proxy=carbon_aware.carbon_proxy,
        proxy_unit=baseline.carbon_proxy_unit,
        equivalent_service=baseline.complete and carbon_aware.complete,
    )
