import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { AggregateDepotLoad, CarbonAwareSchedule, Scenario } from '../api/client';
import { createDemoScenario } from '../data/scenario';
import { simulationResult } from '../test/fixtures';
import { CarbonProfileChart, DepotLoadChart, ScheduleChart } from './Charts';

function scenarioWithSize(vehicleCount: number, intervalCount: number): Scenario {
  const scenario = createDemoScenario();
  const start = Date.parse(scenario.start_at);
  const intervalMs = 15 * 60_000;
  const end = new Date(start + intervalCount * intervalMs).toISOString();
  const vehicleTemplate = scenario.vehicles[0]!;
  const profileTemplate = scenario.carbon_intensity_profile[0]!;

  scenario.interval_minutes = 15;
  scenario.intervals = intervalCount;
  scenario.vehicles = Array.from({ length: vehicleCount }, (_, index) => ({
    ...vehicleTemplate,
    vehicle_id: `vehicle-${String(index + 1).padStart(2, '0')}`,
    availability_end: end,
  }));
  scenario.carbon_intensity_profile = Array.from({ length: intervalCount }, (_, index) => ({
    ...profileTemplate,
    interval_start: new Date(start + index * intervalMs).toISOString(),
    interval_end: new Date(start + (index + 1) * intervalMs).toISOString(),
  }));
  return scenario;
}

function scheduleFor(scenario: Scenario): CarbonAwareSchedule {
  const template = simulationResult().carbon_aware;
  const intervalMs = scenario.interval_minutes * 60_000;
  const start = Date.parse(scenario.start_at);
  return {
    ...template,
    allocations: scenario.vehicles.map((vehicle, index) => {
      const intervalIndex = index % scenario.intervals;
      const segmentStart = new Date(start + intervalIndex * intervalMs).toISOString();
      return {
        interval_index: intervalIndex,
        segment_start: segmentStart,
        segment_end: new Date(start + (intervalIndex + 1) * intervalMs).toISOString(),
        vehicle_id: vehicle.vehicle_id,
        charging_power_kw: 0.004,
        energy_kwh: 0.001,
        carbon_intensity_value: 100,
        intensity_unit: scenario.carbon_intensity_profile[0]!.intensity_unit,
      };
    }),
  };
}

function intervalLoadRows(scenario: Scenario): AggregateDepotLoad[] {
  return scenario.carbon_intensity_profile.map((interval, index) => ({
    interval_index: index,
    interval_start: interval.interval_start,
    interval_end: interval.interval_end,
    average_power_kw: 0.004,
    peak_power_kw: 0.005,
    delivered_energy_kwh: 0.001,
  }));
}

describe('Discrete interval visualisation', () => {
  it('renders one carbon-intensity bar per interval without interpolating between intervals', () => {
    const scenario = scenarioWithSize(1, 2);
    const { container } = render(<CarbonProfileChart profile={scenario.carbon_intensity_profile} />);

    expect(container.querySelectorAll('.carbon-interval-bar')).toHaveLength(2);
    expect(container.querySelector('.intensity-line')).toBeNull();
  });

  it('renders one average-load bar and one peak marker per interval', () => {
    const scenario = scenarioWithSize(1, 2);
    const rows = intervalLoadRows(scenario);
    const { container } = render(<DepotLoadChart rows={rows} depotPowerKw={10} label="Demo" />);

    expect(container.querySelectorAll('.load-average-bar')).toHaveLength(2);
    expect(container.querySelectorAll('.load-peak-marker')).toHaveLength(2);
    expect(container.querySelector('.load-average-line')).toBeNull();
    expect(container.querySelector('.load-peak-line')).toBeNull();
  });
});

describe('ScheduleChart', () => {
  it('keeps ordinary scenario ticks and allocation detail readable', () => {
    const scenario = createDemoScenario();
    const { container } = render(
      <ScheduleChart schedule={simulationResult().carbon_aware} scenario={scenario} label="Demo" />,
    );

    expect(container.querySelectorAll('[data-time-tick]')).toHaveLength(scenario.intervals + 1);
    fireEvent.click(screen.getByText('View allocation data'));
    expect(container.querySelectorAll('.allocation-data-table tbody tr')).toHaveLength(2);
    expect(container.querySelector('.allocation-data-table')).toHaveTextContent('2026-01-01T00:00:00Z');
  });

  it('bounds time ticks and preserves all labels and allocation rows at 96 intervals and 50 vehicles', () => {
    const scenario = scenarioWithSize(50, 96);
    const { container } = render(
      <ScheduleChart schedule={scheduleFor(scenario)} scenario={scenario} label="Maximum scenario" />,
    );

    expect(container.querySelectorAll('[data-time-tick]').length).toBeLessThanOrEqual(5);
    expect(container.querySelectorAll('.schedule-vehicle-label')).toHaveLength(50);
    expect(container.querySelector('.schedule-chart-viewport')).not.toBeNull();
    fireEvent.click(screen.getByText('View allocation data'));
    expect(container.querySelectorAll('.allocation-data-table tbody tr')).toHaveLength(50);
    expect(container.querySelector('.allocation-data-table')).toHaveTextContent('vehicle-50');
  });

  it('exposes exact UTC bounds for every maximum-size interval in accessible tables', () => {
    const scenario = scenarioWithSize(1, 96);
    const rows = intervalLoadRows(scenario);
    const { container } = render(
      <>
        <CarbonProfileChart profile={scenario.carbon_intensity_profile} />
        <DepotLoadChart rows={rows} depotPowerKw={100} label="Maximum scenario" />
      </>,
    );
    const profileTable = container.querySelector('.interval-data-table')!;
    const loadTable = container.querySelector('.load-data-table')!;
    const lastInterval = scenario.carbon_intensity_profile.at(-1)!;
    const lastLoadInterval = rows.at(-1)!;

    expect(profileTable.querySelectorAll('tbody tr')).toHaveLength(96);
    expect(profileTable.querySelector('tbody tr:last-child')).toHaveTextContent(lastInterval.interval_end);
    expect(profileTable).toHaveAttribute('aria-label', 'Synthetic carbon intensity profile by interval');
    expect(loadTable.querySelectorAll('tbody tr')).toHaveLength(96);
    expect(loadTable.querySelector('tbody tr:last-child')).toHaveTextContent(lastLoadInterval.interval_start);
    expect(loadTable.querySelector('tbody tr:last-child')).toHaveTextContent(lastLoadInterval.interval_end);
    expect(loadTable).toHaveAttribute('aria-label', 'Maximum scenario depot load by interval');
  });

  it('exposes each horizontally scrollable data table as a labelled keyboard region', () => {
    const scenario = createDemoScenario();
    render(
      <>
        <CarbonProfileChart profile={scenario.carbon_intensity_profile} />
        <ScheduleChart schedule={simulationResult().carbon_aware} scenario={scenario} label="Baseline" />
        <DepotLoadChart rows={intervalLoadRows(scenario)} depotPowerKw={100} label="Baseline" />
      </>,
    );

    fireEvent.click(screen.getByText('View interval values'));
    fireEvent.click(screen.getByText('View allocation data'));
    fireEvent.click(screen.getByText('View load data'));

    for (const name of [
      'Carbon intensity profile data table; horizontally scrollable',
      'Baseline charging allocation data table; horizontally scrollable',
      'Baseline depot load data table; horizontally scrollable',
    ]) {
      expect(screen.getByRole('region', { name })).toHaveAttribute('tabindex', '0');
    }
  });

  it('uses fewer time ticks in a narrow viewport while retaining the maximum on a wider viewport', () => {
    let viewportWidth = 360;
    vi.stubGlobal('ResizeObserver', class {
      constructor(private readonly callback: ResizeObserverCallback) {}

      observe(target: Element) {
        const contentRect = {
          width: viewportWidth,
          height: 300,
          top: 0,
          right: viewportWidth,
          bottom: 300,
          left: 0,
          x: 0,
          y: 0,
          toJSON: () => ({}),
        } as DOMRectReadOnly;
        this.callback([{ target, contentRect } as ResizeObserverEntry], this as unknown as ResizeObserver);
      }

      disconnect() {}
    });

    try {
      const scenario = scenarioWithSize(2, 96);
      const narrowRender = render(
        <ScheduleChart schedule={scheduleFor(scenario)} scenario={scenario} label="Responsive scenario" />,
      );
      expect(narrowRender.container.querySelectorAll('[data-time-tick]')).toHaveLength(4);
      narrowRender.unmount();

      viewportWidth = 840;
      const wideRender = render(
        <ScheduleChart schedule={scheduleFor(scenario)} scenario={scenario} label="Responsive scenario" />,
      );
      expect(wideRender.container.querySelectorAll('[data-time-tick]')).toHaveLength(5);
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
