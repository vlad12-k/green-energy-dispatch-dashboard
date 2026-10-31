import type { ForecastScenario, ForecastSchedulingResult, SimulationResult } from '../api/client';

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
    baseline_constraint_checks: { ...checks },
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
      constraint_checks: { ...checks },
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

export function forecastSchedulingResultFixture(
  scenario: ForecastScenario,
  complete = true,
): ForecastSchedulingResult {
  const start = Date.parse(scenario.start_at);
  const intervalMs = scenario.interval_minutes * 60_000;
  const requiredTotal = scenario.vehicles.reduce((sum, vehicle) => sum + vehicle.required_energy_kwh, 0);
  const intensityAt = (index: number) => index === 0 ? 400 : index === 1 ? 250 : 50;

  const makeSchedule = (reverse: boolean) => {
    const scheduleComplete = complete || reverse;
    const delivered: Record<string, number> = {};
    const unmet: Record<string, number> = {};
    const vehicleResults: Record<string, { required_energy_kwh: number; delivered_energy_kwh: number; unmet_energy_kwh: number }> = {};
    const allocations: NonNullable<ForecastSchedulingResult['immediate_baseline']>['allocations'] = [];
    const intervalEnergy = Array.from({ length: scenario.intervals }, () => 0);

    for (const vehicle of scenario.vehicles) {
      const leaveUnmetEnergy = !scheduleComplete && vehicle.vehicle_id === scenario.vehicles[0]?.vehicle_id;
      const targetEnergy = leaveUnmetEnergy
        ? Math.max(0, vehicle.required_energy_kwh - 1)
        : vehicle.required_energy_kwh;
      let remaining = targetEnergy;
      const order = Array.from({ length: scenario.intervals }, (_, index) => index);
      if (reverse) order.reverse();
      for (const index of order) {
        if (remaining <= 1e-9) break;
        const energy = Math.min(remaining, vehicle.charging_power_kw * scenario.interval_minutes / 60);
        const intervalStart = new Date(start + index * intervalMs).toISOString();
        allocations.push({
          interval_index: index,
          segment_start: intervalStart,
          segment_end: new Date(start + (index + 1) * intervalMs).toISOString(),
          vehicle_id: vehicle.vehicle_id,
          charging_power_kw: energy * 60 / scenario.interval_minutes,
          energy_kwh: energy,
          carbon_intensity_value: intensityAt(index),
          intensity_unit: 'gCO2/kWh',
        });
        intervalEnergy[index] = (intervalEnergy[index] ?? 0) + energy;
        remaining -= energy;
      }
      const received = targetEnergy - remaining;
      const unmetEnergy = vehicle.required_energy_kwh - received;
      delivered[vehicle.vehicle_id] = received;
      unmet[vehicle.vehicle_id] = unmetEnergy;
      vehicleResults[vehicle.vehicle_id] = {
        required_energy_kwh: vehicle.required_energy_kwh,
        delivered_energy_kwh: received,
        unmet_energy_kwh: unmetEnergy,
      };
    }

    const loads = intervalEnergy.map((energy, index) => ({
      interval_index: index,
      interval_start: new Date(start + index * intervalMs).toISOString(),
      interval_end: new Date(start + (index + 1) * intervalMs).toISOString(),
      average_power_kw: energy * 60 / scenario.interval_minutes,
      peak_power_kw: energy * 60 / scenario.interval_minutes,
      delivered_energy_kwh: energy,
    }));
    const totalDelivered = Object.values(delivered).reduce((sum, value) => sum + value, 0);
    const carbonProxy = allocations.reduce((sum, allocation) => sum + allocation.energy_kwh * allocation.carbon_intensity_value, 0);
    return {
      strategy: reverse ? 'carbon_aware_edf_then_bounded_carbon_shift_v1' : 'immediate_charge_in_vehicle_id_order',
      feasible: scheduleComplete,
      feasibility_classification: scheduleComplete ? 'complete_schedule_demonstrated' : 'complete_schedule_not_demonstrated',
      total_required_energy_kwh: requiredTotal,
      total_delivered_energy_kwh: totalDelivered,
      delivered_energy_kwh: delivered,
      unmet_energy_kwh: unmet,
      vehicle_results: vehicleResults,
      interval_charging_kw: loads.map((row) => row.average_power_kw),
      allocations,
      aggregate_depot_load: loads,
      peak_depot_power_kw: Math.max(0, ...loads.map((row) => row.peak_power_kw)),
      carbon_intensity_weighted_energy_proxy: carbonProxy,
      carbon_proxy_unit: 'gCO2',
      binding_operational_constraints: {
        depot_capacity_interval_indexes: [],
        vehicle_power_limit_segments: {},
        availability_window_end_used_segments: {},
      },
      constraint_checks: { ...checks },
    };
  };

  const baseline = makeSchedule(false);
  const forecastAware = makeSchedule(true);
  const eligible = complete && baseline.feasible && forecastAware.feasible;
  const baselineProxy = baseline.carbon_intensity_weighted_energy_proxy;
  const forecastProxy = forecastAware.carbon_intensity_weighted_energy_proxy;
  const predictionStart = new Date(start - intervalMs).toISOString();
  const predictionEnd = new Date(start - intervalMs + 48 * 30 * 60_000).toISOString();
  const decisionTime = new Date(Date.now() - 30_000).toISOString();
  return {
    scenario_id: scenario.scenario_id,
    status: 'available',
    unavailable_reason: null,
    unavailable_detail: null,
    input_provenance: {
      source_classification: 'regional_forecast',
      source_api: 'neso-carbon-intensity-api',
      source_url: `https://api.carbonintensity.org.uk/regional/intensity/${predictionStart.slice(0, 16)}Z/fw24h/regionid/13`,
      region_id: 13,
      region_name: 'London',
      measurement_type: 'forecast',
      forecast_retrieved_at: new Date(Date.parse(decisionTime) - 30_000).toISOString(),
      prediction_horizon_start: predictionStart,
      prediction_horizon_end: predictionEnd,
      scheduling_horizon_start: scenario.start_at,
      scheduling_horizon_end: new Date(start + scenario.intervals * intervalMs).toISOString(),
      decision_time: decisionTime,
      forecast_age_seconds: 30,
      intensity_unit: 'gCO2/kWh',
      interval_count: 48,
    },
    immediate_baseline: baseline,
    forecast_aware: forecastAware,
    comparison_eligible: eligible,
    absolute_carbon_proxy_reduction: eligible ? baselineProxy - forecastProxy : null,
    percentage_carbon_proxy_reduction: eligible && baselineProxy > 0 ? (baselineProxy - forecastProxy) / baselineProxy * 100 : null,
    comparison_unavailable_reason: eligible
      ? null
      : 'Equivalent-service comparison is unavailable because at least one strategy did not complete every vehicle requirement.',
    percentage_difference_unavailable_reason: null,
    carbon_proxy_unit: 'gCO2',
    advisory_only: true,
    forecast_based: true,
    measured: false,
    real_world_emissions_reduction_claimed: false,
  } as ForecastSchedulingResult;
}

export function unavailableForecastSchedulingResult(scenario: ForecastScenario): ForecastSchedulingResult {
  return {
    scenario_id: scenario.scenario_id,
    status: 'unavailable',
    unavailable_reason: 'forecast_unavailable',
    unavailable_detail: 'upstream_error',
    input_provenance: null,
    immediate_baseline: null,
    forecast_aware: null,
    comparison_eligible: false,
    absolute_carbon_proxy_reduction: null,
    percentage_carbon_proxy_reduction: null,
    comparison_unavailable_reason: 'No schedule is available because server-owned forecast evidence could not be retrieved.',
    percentage_difference_unavailable_reason: null,
    carbon_proxy_unit: 'gCO2',
    advisory_only: true,
    forecast_based: true,
    measured: false,
    real_world_emissions_reduction_claimed: false,
  };
}

export function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}
