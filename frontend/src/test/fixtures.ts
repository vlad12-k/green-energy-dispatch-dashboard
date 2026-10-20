import type { SimulationResult } from '../api/client';

const checks = {
  scenario_bounds_respected: true,
  availability_windows_respected: true,
  vehicle_power_limits_respected: true,
  depot_power_limit_respected: true,
  energy_conservation_respected: true,
};

const allocations = [
  {
    interval_index: 0,
    segment_start: '2026-01-01T00:00:00Z',
    segment_end: '2026-01-01T01:00:00Z',
    vehicle_id: 'van-1',
    charging_power_kw: 7,
    energy_kwh: 7,
    carbon_intensity_value: 400,
    intensity_unit: 'gCO2/kWh',
  },
  {
    interval_index: 1,
    segment_start: '2026-01-01T01:00:00Z',
    segment_end: '2026-01-01T02:00:00Z',
    vehicle_id: 'van-1',
    charging_power_kw: 1,
    energy_kwh: 1,
    carbon_intensity_value: 100,
    intensity_unit: 'gCO2/kWh',
  },
];

const aggregateLoad = [
  {
    interval_index: 0,
    interval_start: '2026-01-01T00:00:00Z',
    interval_end: '2026-01-01T01:00:00Z',
    average_power_kw: 7,
    peak_power_kw: 7,
    delivered_energy_kwh: 7,
  },
  {
    interval_index: 1,
    interval_start: '2026-01-01T01:00:00Z',
    interval_end: '2026-01-01T02:00:00Z',
    average_power_kw: 1,
    peak_power_kw: 1,
    delivered_energy_kwh: 1,
  },
];

export function simulationResult(): SimulationResult {
  return {
    scenario_id: 'synthetic-carbon-shift-demo',
    strategy: 'carbon_aware_edf_then_bounded_carbon_shift_v1',
    baseline: {
      strategy: 'immediate_charge_in_vehicle_id_order',
      feasible: true,
      baseline_feasible: true,
      feasible_alternative_schedule: 'unknown',
      delivered_energy_kwh: { 'van-1': 8 },
      unmet_energy_kwh: { 'van-1': 0 },
      charging_kw: [7, 1],
    },
    baseline_feasible: true,
    baseline_requirements_met_for_comparison: true,
    carbon_aware_feasible: true,
    alternative_feasible_schedule_demonstrated: true,
    baseline_delivered_energy_kwh: { 'van-1': 8 },
    baseline_unmet_energy_kwh: { 'van-1': 0 },
    baseline_allocations: allocations,
    baseline_aggregate_depot_load: aggregateLoad,
    baseline_peak_depot_power_kw: 7,
    baseline_binding_operational_constraints: {
      depot_capacity_interval_indexes: [],
      vehicle_power_limit_segments: { 'van-1': [0] },
      availability_window_end_used_segments: {},
    },
    baseline_constraint_checks: checks,
    carbon_aware: {
      strategy: 'carbon_aware_edf_then_bounded_carbon_shift_v1',
      feasible: true,
      feasibility_classification: 'complete_schedule_demonstrated',
      total_required_energy_kwh: 8,
      total_delivered_energy_kwh: 8,
      delivered_energy_kwh: { 'van-1': 8 },
      unmet_energy_kwh: { 'van-1': 0 },
      vehicle_results: {
        'van-1': { required_energy_kwh: 8, delivered_energy_kwh: 8, unmet_energy_kwh: 0 },
      },
      interval_charging_kw: [1, 7],
      allocations: [...allocations].reverse(),
      aggregate_depot_load: [...aggregateLoad].reverse(),
      peak_depot_power_kw: 7,
      carbon_intensity_weighted_energy_proxy: 1100,
      carbon_proxy_unit: 'gCO2',
      binding_operational_constraints: {
        depot_capacity_interval_indexes: [],
        vehicle_power_limit_segments: { 'van-1': [1] },
        availability_window_end_used_segments: {},
      },
      constraint_checks: checks,
    },
    baseline_carbon_proxy: 2900,
    carbon_aware_carbon_proxy: 1100,
    carbon_proxy_unit: 'gCO2',
    comparison_eligible: true,
    absolute_carbon_proxy_reduction: 1800,
    percentage_carbon_proxy_reduction: 62.06896551724138,
    comparison_unavailable_reason: null,
    percentage_difference_unavailable_reason: null,
    input_provenance: {
      source_classification: 'synthetic_scenario',
      source_id: 'synthetic-scenario',
      geography_code: 'simulation-only',
      provenance_label: 'synthetic:operator-dashboard-test',
      intensity_unit: 'gCO2/kWh',
      interval_count: 2,
    },
    real_world_emissions_reduction_claimed: false,
  } as SimulationResult;
}

export function incompleteSimulationResult(): SimulationResult {
  const result = simulationResult();
  result.baseline_feasible = false;
  result.baseline_requirements_met_for_comparison = false;
  result.baseline.baseline_feasible = false;
  result.baseline.feasible = false;
  result.baseline_unmet_energy_kwh = { 'van-1': 12 };
  result.carbon_aware_feasible = false;
  result.carbon_aware.feasible = false;
  result.carbon_aware.feasibility_classification = 'complete_schedule_not_demonstrated';
  result.carbon_aware.unmet_energy_kwh = { 'van-1': 12 };
  result.carbon_aware.total_delivered_energy_kwh = 8;
  result.comparison_eligible = false;
  result.absolute_carbon_proxy_reduction = null;
  result.percentage_carbon_proxy_reduction = null;
  result.comparison_unavailable_reason = 'At least one strategy did not demonstrate complete service; this does not prove infeasibility.';
  result.percentage_difference_unavailable_reason = 'Not defined for an unavailable equal-service comparison.';
  return result;
}

export function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}
