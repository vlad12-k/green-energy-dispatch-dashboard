import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ResultsDetailsPanel, ResultsPanel } from './ResultsPanel';
import type { Scenario, SimulationResult } from '../api/client';
import { createDemoScenario } from '../data/scenario';
import { incompleteSimulationResult, simulationResult } from '../test/fixtures';

function resultsView(result: SimulationResult, scenario: Scenario) {
  return <><ResultsPanel result={result} scenario={scenario} /><ResultsDetailsPanel result={result} scenario={scenario} /></>;
}

function metricText(container: HTMLElement, metricIndex: number): string[] {
  return Array.from(container.querySelectorAll(`.schedule-metrics div:nth-child(${metricIndex}) strong`))
    .map((element) => element.textContent ?? '');
}

describe('ResultsPanel numerical precision', () => {
  it('shows the PR #10 large-request shortfall as non-zero in schedule summaries', () => {
    const scenario = createDemoScenario();
    const largeRequest = 1_000_000_000;
    const delivered = 999_999_999.9995;
    scenario.vehicles[0]!.required_energy_kwh = largeRequest;
    scenario.vehicles[0]!.charging_power_kw = delivered;
    scenario.depot_power_kw = delivered;

    const result = incompleteSimulationResult();
    result.baseline_delivered_energy_kwh = { 'van-1': delivered };
    result.baseline_unmet_energy_kwh = { 'van-1': 0.0005 };
    result.baseline_peak_depot_power_kw = delivered;
    result.carbon_aware.delivered_energy_kwh = { 'van-1': delivered };
    result.carbon_aware.unmet_energy_kwh = { 'van-1': 0.0005 };
    result.carbon_aware.total_required_energy_kwh = largeRequest;
    result.carbon_aware.total_delivered_energy_kwh = delivered;
    result.carbon_aware.vehicle_results = {
      'van-1': { required_energy_kwh: largeRequest, delivered_energy_kwh: delivered, unmet_energy_kwh: 0.0005 },
    };

    const { container } = render(resultsView(result, scenario));

    expect(metricText(container, 2)).toEqual(['0.0005 kWh', '0.0005 kWh']);
    expect(screen.getAllByText('Complete schedule not demonstrated')).toHaveLength(2);
  });

  it('shows tiny valid energy requests and backend shortfalls without rounding them to zero', () => {
    const scenario = createDemoScenario();
    scenario.vehicles[0]!.required_energy_kwh = 1e-9;
    scenario.vehicles[0]!.charging_power_kw = 1e-12;
    scenario.depot_power_kw = 1e-12;

    const result = incompleteSimulationResult();
    const delivered = 1e-12;
    const unmet = 0.999e-9;
    result.baseline_delivered_energy_kwh = { 'van-1': delivered };
    result.baseline_unmet_energy_kwh = { 'van-1': unmet };
    result.baseline_peak_depot_power_kw = delivered;
    result.carbon_aware.delivered_energy_kwh = { 'van-1': delivered };
    result.carbon_aware.unmet_energy_kwh = { 'van-1': unmet };
    result.carbon_aware.total_required_energy_kwh = 1e-9;
    result.carbon_aware.total_delivered_energy_kwh = delivered;
    result.carbon_aware.peak_depot_power_kw = delivered;
    result.carbon_aware.vehicle_results = {
      'van-1': { required_energy_kwh: 1e-9, delivered_energy_kwh: delivered, unmet_energy_kwh: unmet },
    };
    result.baseline_allocations = result.baseline_allocations.slice(0, 1).map((allocation) => ({
      ...allocation,
      energy_kwh: delivered,
      charging_power_kw: delivered,
    }));
    result.carbon_aware.allocations = result.carbon_aware.allocations.slice(0, 1).map((allocation) => ({
      ...allocation,
      energy_kwh: delivered,
      charging_power_kw: delivered,
    }));

    const { container } = render(resultsView(result, scenario));

    expect(metricText(container, 1)).toEqual(['1E-12 kWh', '1E-12 kWh']);
    expect(metricText(container, 2)).toEqual(['9.99E-10 kWh', '9.99E-10 kWh']);
    expect(metricText(container, 3)).toEqual(['1E-12 kW', '1E-12 kW']);
    expect(container.querySelector('.vehicle-results-details tbody tr')).toHaveTextContent('1E-9');
    expect(container.querySelector('.allocation-data-table tbody tr')).toHaveTextContent('1E-12');
  });

  it('preserves small positive, negative and true-zero proxy differences', () => {
    const scenario = createDemoScenario();
    const result = simulationResult();
    const { container, rerender } = render(resultsView(result, scenario));
    const difference = container.querySelector('.difference-value strong');

    result.absolute_carbon_proxy_reduction = 0.0005;
    result.baseline_carbon_proxy = 1.0005;
    result.carbon_aware_carbon_proxy = 1;
    rerender(resultsView(result, scenario));
    expect(difference).toHaveTextContent('+0.0005 gCO2');

    result.absolute_carbon_proxy_reduction = -0.0005;
    result.baseline_carbon_proxy = 1;
    result.carbon_aware_carbon_proxy = 1.0005;
    rerender(resultsView(result, scenario));
    expect(difference).toHaveTextContent('-0.0005 gCO2');

    result.absolute_carbon_proxy_reduction = 0;
    result.baseline_carbon_proxy = 1;
    result.carbon_aware_carbon_proxy = 1;
    result.percentage_carbon_proxy_reduction = 0;
    rerender(resultsView(result, scenario));
    expect(difference).toHaveTextContent('0 gCO2');
    expect(container.querySelector('.percentage-value')).toHaveTextContent('0%');
  });
});
