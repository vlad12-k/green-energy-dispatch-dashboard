import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ForecastResultsPanel } from './ForecastResultsPanel';
import { createDefaultForecastScenario } from '../data/forecastScenario';
import { forecastSchedulingResultFixture } from '../test/fixtures';

describe('ForecastResultsPanel service delivery', () => {
  it('does not claim complete service from the API feasibility flag while any energy remains unmet', () => {
    const scenario = createDefaultForecastScenario();
    const result = forecastSchedulingResultFixture(scenario);
    const baseline = result.immediate_baseline;
    if (!baseline) throw new Error('The forecast fixture must contain a baseline schedule.');

    const vehicle = scenario.vehicles[0];
    if (!vehicle) throw new Error('The scenario fixture must contain a vehicle.');
    const shortfall = 5e-10;
    baseline.unmet_energy_kwh[vehicle.vehicle_id] = shortfall;
    baseline.delivered_energy_kwh[vehicle.vehicle_id] -= shortfall;
    baseline.total_delivered_energy_kwh -= shortfall;
    const vehicleResult = baseline.vehicle_results[vehicle.vehicle_id];
    if (!vehicleResult) throw new Error('The schedule fixture must contain the vehicle result.');
    vehicleResult.delivered_energy_kwh -= shortfall;
    vehicleResult.unmet_energy_kwh = shortfall;
    result.comparison_eligible = false;
    result.absolute_carbon_proxy_reduction = null;
    result.percentage_carbon_proxy_reduction = null;
    result.comparison_unavailable_reason = 'Equivalent service was not demonstrated.';

    const { container } = render(<ForecastResultsPanel result={result} scenario={scenario} />);

    expect(container.querySelector('.forecast-strategy-card'))
      .toHaveTextContent('Complete schedule not demonstrated');
    expect(container.querySelector('.forecast-explainability'))
      .toHaveTextContent('Immediate Baseline: complete service was not demonstrated.');
  });

  it('labels every positive per-vehicle shortfall incomplete, including values below 1e-9 kWh', () => {
    const scenario = createDefaultForecastScenario();
    const result = forecastSchedulingResultFixture(scenario, false);
    const baseline = result.immediate_baseline;
    if (!baseline) throw new Error('The incomplete forecast fixture must contain a baseline schedule.');

    const vehicle = scenario.vehicles[1];
    if (!vehicle) throw new Error('The scenario fixture must contain a second vehicle.');
    const shortfall = 5e-10;
    baseline.unmet_energy_kwh[vehicle.vehicle_id] = shortfall;
    baseline.delivered_energy_kwh[vehicle.vehicle_id] -= shortfall;
    baseline.total_delivered_energy_kwh -= shortfall;
    const vehicleResult = baseline.vehicle_results[vehicle.vehicle_id];
    if (!vehicleResult) throw new Error('The schedule fixture must contain the second vehicle result.');
    vehicleResult.delivered_energy_kwh -= shortfall;
    vehicleResult.unmet_energy_kwh = shortfall;

    const { container } = render(<ForecastResultsPanel result={result} scenario={scenario} />);
    const rows = container.querySelectorAll<HTMLTableRowElement>(
      'table[aria-label="Immediate Baseline vehicle energy delivery and deadlines"] tbody tr',
    );
    const vehicleRow = Array.from(rows).find(
      (row: HTMLTableRowElement) => row.cells[0]?.textContent === vehicle.vehicle_id,
    );

    expect(vehicleRow).toBeDefined();
    expect(vehicleRow?.cells[6]).toHaveTextContent('Incomplete');
    expect(container.querySelector('.forecast-strategy-card'))
      .toHaveTextContent('Complete schedule not demonstrated');
  });
});
