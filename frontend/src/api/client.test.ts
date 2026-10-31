import { afterEach, describe, expect, it, vi } from 'vitest';
import { createDemoScenario } from '../data/scenario';
import { createDefaultForecastScenario } from '../data/forecastScenario';
import { evaluateForecastScenario, evaluateScenario, getCarbonIntensity, getCarbonIntensityForecast, ForecastApiError, SimulationApiError } from './client';
import {
  forecastSchedulingResultFixture,
  jsonResponse,
  simulationResult,
  unavailableForecastSchedulingResult,
} from '../test/fixtures';

type ForecastResultFixture = ReturnType<typeof forecastSchedulingResultFixture>;
type ForecastScheduleFixture = NonNullable<ForecastResultFixture['immediate_baseline']>;

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

function setScheduleIntensity(schedule: ForecastScheduleFixture, intensity: number) {
  let proxy = 0;
  for (const allocation of schedule.allocations) {
    allocation.carbon_intensity_value = intensity;
    proxy += allocation.energy_kwh * intensity;
  }
  schedule.carbon_intensity_weighted_energy_proxy = proxy;
}

function setComparisonMetrics(result: ForecastResultFixture) {
  const baseline = result.immediate_baseline?.carbon_intensity_weighted_energy_proxy;
  const forecastAware = result.forecast_aware?.carbon_intensity_weighted_energy_proxy;
  if (baseline === undefined || baseline === null || forecastAware === undefined || forecastAware === null) {
    throw new Error('The forecast fixture must include both strategy schedules.');
  }

  const reduction = baseline - forecastAware;
  result.absolute_carbon_proxy_reduction = reduction;
  result.comparison_eligible = true;
  result.comparison_unavailable_reason = null;
  if (baseline > 0) {
    result.percentage_carbon_proxy_reduction = (reduction / baseline) * 100;
    result.percentage_difference_unavailable_reason = null;
  } else {
    result.percentage_carbon_proxy_reduction = null;
    result.percentage_difference_unavailable_reason = 'Percentage difference is undefined because the baseline proxy is zero.';
  }
}

function makeAllocation(
  scenario: ReturnType<typeof createDefaultForecastScenario>,
  intervalIndex: number,
  vehicleId: string,
  startOffsetMinutes: number,
  durationMinutes: number,
  chargingPowerKw: number,
): ForecastScheduleFixture['allocations'][number] {
  const intervalStart = Date.parse(scenario.start_at) + intervalIndex * scenario.interval_minutes * 60_000;
  const segmentStart = intervalStart + startOffsetMinutes * 60_000;
  const segmentEnd = segmentStart + durationMinutes * 60_000;
  return {
    interval_index: intervalIndex,
    segment_start: new Date(segmentStart).toISOString(),
    segment_end: new Date(segmentEnd).toISOString(),
    vehicle_id: vehicleId,
    charging_power_kw: chargingPowerKw,
    energy_kwh: chargingPowerKw * durationMinutes / 60,
    carbon_intensity_value: 0,
    intensity_unit: 'gCO2/kWh',
  };
}

function replaceScheduleAllocations(
  schedule: ForecastScheduleFixture,
  scenario: ReturnType<typeof createDefaultForecastScenario>,
  allocations: ForecastScheduleFixture['allocations'],
  intervalPeaks: number[],
) {
  schedule.allocations = allocations;
  const delivered = Object.fromEntries(scenario.vehicles.map((vehicle) => [
    vehicle.vehicle_id,
    allocations
      .filter((allocation) => allocation.vehicle_id === vehicle.vehicle_id)
      .reduce((sum, allocation) => sum + allocation.energy_kwh, 0),
  ]));
  const unmet = Object.fromEntries(scenario.vehicles.map((vehicle) => [
    vehicle.vehicle_id,
    Math.max(vehicle.required_energy_kwh - (delivered[vehicle.vehicle_id] ?? 0), 0),
  ]));
  schedule.delivered_energy_kwh = delivered;
  schedule.unmet_energy_kwh = unmet;
  schedule.vehicle_results = Object.fromEntries(scenario.vehicles.map((vehicle) => [
    vehicle.vehicle_id,
    {
      required_energy_kwh: vehicle.required_energy_kwh,
      delivered_energy_kwh: delivered[vehicle.vehicle_id] ?? 0,
      unmet_energy_kwh: unmet[vehicle.vehicle_id] ?? vehicle.required_energy_kwh,
    },
  ]));
  schedule.total_required_energy_kwh = scenario.vehicles.reduce((sum, vehicle) => sum + vehicle.required_energy_kwh, 0);
  schedule.total_delivered_energy_kwh = Object.values(delivered).reduce((sum, energy) => sum + energy, 0);
  schedule.feasible = Object.values(unmet).every((energy) => energy === 0);
  schedule.feasibility_classification = schedule.feasible
    ? 'complete_schedule_demonstrated'
    : 'complete_schedule_not_demonstrated';
  const intervalEnergy = Array.from({ length: scenario.intervals }, (_, index) => (
    allocations
      .filter((allocation) => allocation.interval_index === index)
      .reduce((sum, allocation) => sum + allocation.energy_kwh, 0)
  ));
  schedule.aggregate_depot_load = intervalEnergy.map((energy, index) => {
    const intervalStart = Date.parse(scenario.start_at) + index * scenario.interval_minutes * 60_000;
    return {
      interval_index: index,
      interval_start: new Date(intervalStart).toISOString(),
      interval_end: new Date(intervalStart + scenario.interval_minutes * 60_000).toISOString(),
      average_power_kw: energy / (scenario.interval_minutes / 60),
      peak_power_kw: intervalPeaks[index] ?? 0,
      delivered_energy_kwh: energy,
    };
  });
  schedule.interval_charging_kw = schedule.aggregate_depot_load.map((row) => row.average_power_kw);
  schedule.peak_depot_power_kw = Math.max(0, ...intervalPeaks);
  schedule.carbon_intensity_weighted_energy_proxy = 0;
}


describe('evaluateScenario', () => {
  it('posts the exact scenario to the versioned simulation endpoint', async () => {
    const scenario = createDemoScenario();
    const responseBody = simulationResult();
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(responseBody));
    vi.stubGlobal('fetch', fetchMock);

    await expect(evaluateScenario(scenario)).resolves.toEqual(responseBody);
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/simulations/ev-depot/evaluate',
      expect.objectContaining({
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify(scenario),
      }),
    );
  });

  it('maps FastAPI validation locations to field errors', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({
      detail: [{
        type: 'greater_than',
        loc: ['body', 'vehicles', 0, 'required_energy_kwh'],
        msg: 'Input should be greater than 0',
      }],
    }, 422)));

    await expect(evaluateScenario(createDemoScenario())).rejects.toMatchObject({
      name: 'SimulationApiError',
      status: 422,
      fieldErrors: { 'vehicles.0.required_energy_kwh': 'Input should be greater than 0' },
    });
  });

  it('maps cross-field validation failures to the affected form group', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({
      detail: [{
        type: 'value_error',
        loc: ['body'],
        msg: 'availability window must be within the scenario horizon',
      }],
    }, 422)));

    await expect(evaluateScenario(createDemoScenario())).rejects.toMatchObject({
      fieldErrors: { vehicles: 'availability window must be within the scenario horizon' },
    });
  });

  it('reports request timeout separately from a network failure', async () => {
    const timeoutController = new AbortController();
    const timeoutSpy = vi.spyOn(AbortSignal, 'timeout').mockReturnValue(timeoutController.signal);
    vi.stubGlobal('fetch', vi.fn().mockImplementation(() => {
      timeoutController.abort();
      return Promise.reject(new DOMException('private simulation timeout detail', 'AbortError'));
    }));

    try {
      await expect(evaluateScenario(createDemoScenario())).rejects.toMatchObject({
        name: 'SimulationApiError',
        status: 0,
        message: 'The simulation request timed out. Review the scenario before retrying.',
      });
    } finally {
      timeoutSpy.mockRestore();
    }
  });

  it('classifies a response-body timeout as a simulation timeout', async () => {
    const timeoutController = new AbortController();
    const timeoutSpy = vi.spyOn(AbortSignal, 'timeout').mockReturnValue(timeoutController.signal);
    const response = {
      ok: true,
      status: 200,
      json: vi.fn().mockImplementation(() => {
        timeoutController.abort();
        return Promise.reject(new DOMException('private simulation body timeout detail', 'AbortError'));
      }),
    } as unknown as Response;
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response));

    try {
      await expect(evaluateScenario(createDemoScenario())).rejects.toMatchObject({
        name: 'SimulationApiError',
        status: 0,
        message: 'The simulation request timed out. Review the scenario before retrying.',
      });
    } finally {
      timeoutSpy.mockRestore();
    }
  });

  it('does not expose server error details to the dashboard', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ detail: 'internal stack trace' }, 500)));

    try {
      await evaluateScenario(createDemoScenario());
      throw new Error('Expected request rejection');
    } catch (error) {
      expect(error).toBeInstanceOf(SimulationApiError);
      expect((error as Error).message).not.toContain('internal stack trace');
      expect((error as Error).message).toContain('could not complete');
    }
  });

  it('preserves caller cancellation while reading the synthetic response body', async () => {
    const controller = new AbortController();
    const response = {
      ok: true,
      status: 200,
      json: vi.fn().mockImplementation(() => {
        controller.abort();
        return Promise.reject(new DOMException('private cancellation detail', 'AbortError'));
      }),
    } as unknown as Response;
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response));

    await expect(evaluateScenario(createDemoScenario(), controller.signal)).rejects.toMatchObject({ name: 'AbortError' });
  });
});

describe('evaluateForecastScenario', () => {
  it('maps FastAPI forecast validation locations to vehicle field errors', async () => {
    const scenario = createDefaultForecastScenario();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ detail: [{
      type: 'greater_than',
      loc: ['body', 'scenario', 'vehicles', 0, 'required_energy_kwh'],
      msg: 'Input should be greater than 0',
    }] }, 422)));

    await expect(evaluateForecastScenario(scenario)).rejects.toMatchObject({
      status: 422,
      fieldErrors: { 'vehicles.0.required_energy_kwh': 'Input should be greater than 0' },
    });
  });

  it('maps network failures to a safe, actionable API error', async () => {
    const scenario = createDefaultForecastScenario();
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('private network detail')));

    await expect(evaluateForecastScenario(scenario)).rejects.toMatchObject({
      name: 'ForecastApiError',
      status: 0,
      message: 'Forecast evaluation could not reach the API. Check the local service and try again.',
    });
  });

  it('reports request timeout separately from a network failure', async () => {
    const scenario = createDefaultForecastScenario();
    const timeoutController = new AbortController();
    const timeoutSpy = vi.spyOn(AbortSignal, 'timeout').mockReturnValue(timeoutController.signal);
    vi.stubGlobal('fetch', vi.fn().mockImplementation(() => {
      timeoutController.abort();
      return Promise.reject(new DOMException('private timeout detail', 'AbortError'));
    }));

    try {
      await expect(evaluateForecastScenario(scenario)).rejects.toMatchObject({
        name: 'ForecastApiError',
        status: 0,
        message: 'Forecast evaluation timed out. Review the current scenario before retrying.',
      });
    } finally {
      timeoutSpy.mockRestore();
    }
  });

  it('classifies a timeout while reading the forecast response body', async () => {
    const scenario = createDefaultForecastScenario();
    const timeoutController = new AbortController();
    const timeoutSpy = vi.spyOn(AbortSignal, 'timeout').mockReturnValue(timeoutController.signal);
    const response = {
      ok: true,
      status: 200,
      json: vi.fn().mockImplementation(() => {
        timeoutController.abort();
        return Promise.reject(new DOMException('private body timeout detail', 'AbortError'));
      }),
    } as unknown as Response;
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response));

    try {
      await expect(evaluateForecastScenario(scenario)).rejects.toMatchObject({
        name: 'ForecastApiError',
        status: 0,
        message: 'Forecast evaluation timed out. Review the current scenario before retrying.',
      });
    } finally {
      timeoutSpy.mockRestore();
    }
  });

  it('does not display an available schedule returned with HTTP 503', async () => {
    const scenario = createDefaultForecastScenario();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(forecastSchedulingResultFixture(scenario), 503)));

    await expect(evaluateForecastScenario(scenario)).rejects.toMatchObject<Partial<ForecastApiError>>({
      status: 503,
      result: null,
    });
  });

  it('retains a validated unavailable server result for explicit rendering', async () => {
    const scenario = createDefaultForecastScenario();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(unavailableForecastSchedulingResult(scenario), 503)));

    await expect(evaluateForecastScenario(scenario)).rejects.toMatchObject({
      status: 503,
      result: expect.objectContaining({ status: 'unavailable', unavailable_reason: 'forecast_unavailable' }),
    });
  });

  it('rejects eligible comparisons when either strategy does not demonstrate complete equal service', async () => {
    const scenario = createDefaultForecastScenario();
    const corruptions: Array<(result: ReturnType<typeof forecastSchedulingResultFixture>) => void> = [
      (result) => { result.immediate_baseline!.feasible = false; },
      (result) => { result.forecast_aware!.total_delivered_energy_kwh -= 1; },
      (result) => {
        const vehicleId = scenario.vehicles[0]!.vehicle_id;
        const schedule = result.forecast_aware!;
        schedule.delivered_energy_kwh[vehicleId] = (schedule.delivered_energy_kwh[vehicleId] ?? 0) - 1;
      },
    ];

    for (const corrupt of corruptions) {
      const payload = forecastSchedulingResultFixture(scenario);
      corrupt(payload);
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(payload)));

      await expect(evaluateForecastScenario(scenario)).rejects.toMatchObject({
        name: 'ForecastApiError',
        status: 200,
      });
    }
  });

  it('rejects proxy comparison eligibility when a strategy reports any positive unmet energy', async () => {
    const scenario = createDefaultForecastScenario();
    const payload = forecastSchedulingResultFixture(scenario);
    const vehicleId = scenario.vehicles[0]!.vehicle_id;
    const schedule = payload.forecast_aware!;
    schedule.unmet_energy_kwh[vehicleId] = Number.MIN_VALUE;
    schedule.vehicle_results[vehicleId]!.unmet_energy_kwh = Number.MIN_VALUE;
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(payload)));

    await expect(evaluateForecastScenario(scenario)).rejects.toMatchObject({
      name: 'ForecastApiError',
      status: 200,
      result: null,
    });
  });

  it('rejects an available incomplete schedule with missing operational constraint checks', async () => {
    const scenario = createDefaultForecastScenario();
    const payload = forecastSchedulingResultFixture(scenario, false);
    Reflect.deleteProperty(payload.forecast_aware!.constraint_checks, 'vehicle_power_limits_respected');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(payload)));

    await expect(evaluateForecastScenario(scenario)).rejects.toMatchObject({
      name: 'ForecastApiError',
      status: 200,
      result: null,
    });
  });

  it('preserves incomplete schedules as non-comparable diagnostics', async () => {
    const scenario = createDefaultForecastScenario();
    const payload = forecastSchedulingResultFixture(scenario, false);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(payload)));

    const result = await evaluateForecastScenario(scenario);
    expect(result.comparison_eligible).toBe(false);
    expect(result.absolute_carbon_proxy_reduction).toBeNull();
    expect(result.comparison_unavailable_reason).toBeTruthy();
  });

  it('rejects complete schedules that the API incorrectly marks as non-comparable', async () => {
    const scenario = createDefaultForecastScenario();
    const payload = forecastSchedulingResultFixture(scenario);
    payload.comparison_eligible = false;
    payload.absolute_carbon_proxy_reduction = null;
    payload.comparison_unavailable_reason = 'comparison_unavailable';
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(payload)));

    await expect(evaluateForecastScenario(scenario)).rejects.toMatchObject({
      name: 'ForecastApiError',
      status: 200,
      result: null,
    });
  });

  it('rejects strategy schedules with contradictory intensity values for a shared interval', async () => {
    const scenario = createDefaultForecastScenario();
    const payload = forecastSchedulingResultFixture(scenario);
    setScheduleIntensity(payload.forecast_aware!, 200);
    setComparisonMetrics(payload);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(payload)));

    await expect(evaluateForecastScenario(scenario)).rejects.toMatchObject({
      name: 'ForecastApiError',
      status: 200,
      result: null,
    });
  });

  it('rejects an absolute reduction that disagrees with the strategy carbon proxies', async () => {
    const scenario = createDefaultForecastScenario();
    const payload = forecastSchedulingResultFixture(scenario);
    payload.absolute_carbon_proxy_reduction = (payload.absolute_carbon_proxy_reduction ?? 0) + 1;
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(payload)));

    await expect(evaluateForecastScenario(scenario)).rejects.toMatchObject({
      name: 'ForecastApiError',
      status: 200,
      result: null,
    });
  });

  it('rejects a percentage that disagrees with the signed proxy reduction', async () => {
    const scenario = createDefaultForecastScenario();
    const payload = forecastSchedulingResultFixture(scenario);
    payload.percentage_carbon_proxy_reduction = (payload.percentage_carbon_proxy_reduction ?? 0) + 1;
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(payload)));

    await expect(evaluateForecastScenario(scenario)).rejects.toMatchObject({
      name: 'ForecastApiError',
      status: 200,
      result: null,
    });
  });

  it('rejects a missing percentage when the baseline proxy is positive', async () => {
    const scenario = createDefaultForecastScenario();
    const payload = forecastSchedulingResultFixture(scenario);
    payload.percentage_carbon_proxy_reduction = null;
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(payload)));

    await expect(evaluateForecastScenario(scenario)).rejects.toMatchObject({
      name: 'ForecastApiError',
      status: 200,
      result: null,
    });
  });

  it('accepts an omitted optional percentage reason when the baseline proxy is positive', async () => {
    const scenario = createDefaultForecastScenario();
    const payload = forecastSchedulingResultFixture(scenario);
    delete payload.percentage_difference_unavailable_reason;
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(payload)));

    const result = await evaluateForecastScenario(scenario);

    expect(result.percentage_carbon_proxy_reduction).toBe(payload.percentage_carbon_proxy_reduction);
    expect(result).not.toHaveProperty('percentage_difference_unavailable_reason');
  });

  it('rejects forecast retrieval recorded after its decision time', async () => {
    const scenario = createDefaultForecastScenario();
    const payload = forecastSchedulingResultFixture(scenario);
    const provenance = payload.input_provenance!;
    provenance.forecast_retrieved_at = new Date(Date.parse(provenance.decision_time) + 1).toISOString();
    provenance.forecast_age_seconds = 0;
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(payload)));

    await expect(evaluateForecastScenario(scenario)).rejects.toMatchObject({
      name: 'ForecastApiError',
      status: 200,
      result: null,
    });
  });

  it('rejects sub-millisecond provenance chronology contradictions', async () => {
    const scenario = createDefaultForecastScenario();
    const payload = forecastSchedulingResultFixture(scenario);
    const provenance = payload.input_provenance!;
    provenance.decision_time = '2026-10-30T12:10:00.000000Z';
    provenance.forecast_retrieved_at = '2026-10-30T12:10:00.000500Z';
    provenance.forecast_age_seconds = 0;
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(payload)));

    await expect(evaluateForecastScenario(scenario)).rejects.toMatchObject({
      name: 'ForecastApiError',
      status: 200,
      result: null,
    });
  });

  it('rejects a forecast age inconsistent with retrieval and decision timestamps', async () => {
    const scenario = createDefaultForecastScenario();
    const payload = forecastSchedulingResultFixture(scenario);
    payload.input_provenance!.forecast_age_seconds = 31;
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(payload)));

    await expect(evaluateForecastScenario(scenario)).rejects.toMatchObject({
      name: 'ForecastApiError',
      status: 200,
      result: null,
    });
  });

  it('accepts a forecast at the server contract maximum age', async () => {
    const scenario = createDefaultForecastScenario();
    const payload = forecastSchedulingResultFixture(scenario);
    const provenance = payload.input_provenance!;
    provenance.forecast_retrieved_at = new Date(Date.parse(provenance.decision_time) - 3_600_000).toISOString();
    provenance.forecast_age_seconds = 3600;
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(payload)));

    const result = await evaluateForecastScenario(scenario);

    expect(result.status).toBe('available');
    expect(result.input_provenance?.forecast_age_seconds).toBe(3600);
  });

  it('rejects a forecast older than the server contract maximum age', async () => {
    const scenario = createDefaultForecastScenario();
    const payload = forecastSchedulingResultFixture(scenario);
    const provenance = payload.input_provenance!;
    provenance.forecast_retrieved_at = new Date(Date.parse(provenance.decision_time) - 3_601_000).toISOString();
    provenance.forecast_age_seconds = 3601;
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(payload)));

    await expect(evaluateForecastScenario(scenario)).rejects.toMatchObject({
      name: 'ForecastApiError',
      status: 200,
      result: null,
    });
  });

  it('rejects timestamp-derived age above the server maximum despite rounded age metadata', async () => {
    const scenario = createDefaultForecastScenario();
    const payload = forecastSchedulingResultFixture(scenario);
    const provenance = payload.input_provenance!;
    provenance.decision_time = '2026-10-30T12:10:00.000000Z';
    provenance.forecast_retrieved_at = '2026-10-30T11:09:59.999500Z';
    provenance.forecast_age_seconds = 3600;
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(payload)));

    await expect(evaluateForecastScenario(scenario)).rejects.toMatchObject({
      name: 'ForecastApiError',
      status: 200,
      result: null,
    });
  });

  it('rejects a forecast horizon that starts after the submitted scheduling horizon', async () => {
    const scenario = createDefaultForecastScenario();
    const payload = forecastSchedulingResultFixture(scenario);
    const provenance = payload.input_provenance!;
    const predictionStart = Date.parse(scenario.start_at) + 30 * 60_000;
    provenance.prediction_horizon_start = new Date(predictionStart).toISOString();
    provenance.prediction_horizon_end = new Date(predictionStart + 48 * 30 * 60_000).toISOString();
    provenance.source_url = `https://api.carbonintensity.org.uk/regional/intensity/${provenance.prediction_horizon_start.slice(0, 16)}Z/fw24h/regionid/${provenance.region_id}`;
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(payload)));

    await expect(evaluateForecastScenario(scenario)).rejects.toMatchObject({
      name: 'ForecastApiError',
      status: 200,
      result: null,
    });
  });

  it('rejects provenance inconsistent with the verified application region', async () => {
    const scenario = createDefaultForecastScenario();
    const payload = forecastSchedulingResultFixture(scenario);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(payload)));

    await expect(evaluateForecastScenario(scenario, undefined, {
      region_id: 14,
      region_name: 'Wales',
    })).rejects.toMatchObject({
      name: 'ForecastApiError',
      status: 200,
      result: null,
    });
  });

  it('rejects a strategy proxy inconsistent with its returned allocations', async () => {
    const scenario = createDefaultForecastScenario();
    const payload = forecastSchedulingResultFixture(scenario);
    payload.immediate_baseline!.carbon_intensity_weighted_energy_proxy += 1;
    const baseline = payload.immediate_baseline!.carbon_intensity_weighted_energy_proxy;
    const forecastAware = payload.forecast_aware!.carbon_intensity_weighted_energy_proxy;
    payload.absolute_carbon_proxy_reduction = baseline - forecastAware;
    payload.percentage_carbon_proxy_reduction = (payload.absolute_carbon_proxy_reduction / baseline) * 100;
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(payload)));

    await expect(evaluateForecastScenario(scenario)).rejects.toMatchObject({
      name: 'ForecastApiError',
      status: 200,
      result: null,
    });
  });

  it('rejects delivered allocations inconsistent with schedule energy totals', async () => {
    const scenario = createDefaultForecastScenario();
    const payload = forecastSchedulingResultFixture(scenario);
    const schedule = payload.immediate_baseline!;
    schedule.allocations[0]!.energy_kwh += 1;
    schedule.carbon_intensity_weighted_energy_proxy = schedule.allocations.reduce(
      (total, allocation) => total + allocation.energy_kwh * allocation.carbon_intensity_value,
      0,
    );
    const baseline = schedule.carbon_intensity_weighted_energy_proxy;
    const forecastAware = payload.forecast_aware!.carbon_intensity_weighted_energy_proxy;
    payload.absolute_carbon_proxy_reduction = baseline - forecastAware;
    payload.percentage_carbon_proxy_reduction = (payload.absolute_carbon_proxy_reduction / baseline) * 100;
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(payload)));

    await expect(evaluateForecastScenario(scenario)).rejects.toMatchObject({
      name: 'ForecastApiError',
      status: 200,
      result: null,
    });
  });

  it('rejects interval power values inconsistent with aggregate depot loads', async () => {
    const scenario = createDefaultForecastScenario();
    const payload = forecastSchedulingResultFixture(scenario);
    payload.immediate_baseline!.interval_charging_kw[0]! += 1;
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(payload)));

    await expect(evaluateForecastScenario(scenario)).rejects.toMatchObject({
      name: 'ForecastApiError',
      status: 200,
      result: null,
    });
  });

  it('rejects allocation times that do not belong to the reported interval', async () => {
    const scenario = createDefaultForecastScenario();
    const payload = forecastSchedulingResultFixture(scenario);
    const allocation = payload.immediate_baseline!.allocations[0]!;
    allocation.segment_start = new Date(Date.parse(allocation.segment_start) + 30 * 60_000).toISOString();
    allocation.segment_end = new Date(Date.parse(allocation.segment_end) + 30 * 60_000).toISOString();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(payload)));

    await expect(evaluateForecastScenario(scenario)).rejects.toMatchObject({
      name: 'ForecastApiError',
      status: 200,
      result: null,
    });
  });

  it('rejects allocation power that does not match delivered energy and segment duration', async () => {
    const scenario = createDefaultForecastScenario();
    const payload = forecastSchedulingResultFixture(scenario);
    payload.immediate_baseline!.allocations[0]!.charging_power_kw += 1;
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(payload)));

    await expect(evaluateForecastScenario(scenario)).rejects.toMatchObject({
      name: 'ForecastApiError',
      status: 200,
      result: null,
    });
  });

  it('rejects allocations above the submitted depot capacity despite passing API checks', async () => {
    const scenario = createDefaultForecastScenario();
    scenario.depot_power_kw = 13;
    const payload = forecastSchedulingResultFixture(scenario);
    expect(payload.immediate_baseline!.constraint_checks.depot_power_limit_respected).toBe(true);
    expect(payload.immediate_baseline!.allocations
      .filter((allocation) => allocation.interval_index === 0)
      .reduce((sum, allocation) => sum + allocation.charging_power_kw, 0)).toBe(14);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(payload)));

    await expect(evaluateForecastScenario(scenario)).rejects.toMatchObject({
      name: 'ForecastApiError',
      status: 200,
      result: null,
    });
  });

  it.each([
    { limit: 'depot', depotPowerKw: 1e-12, vehiclePowerKw: 1e-8 },
    { limit: 'vehicle', depotPowerKw: 1e-8, vehiclePowerKw: 1e-12 },
  ])('rejects a 1e-9 kW allocation above a small positive $limit limit', async ({ depotPowerKw, vehiclePowerKw }) => {
    const scenario = createDefaultForecastScenario();
    scenario.intervals = 1;
    scenario.depot_power_kw = depotPowerKw;
    const intervalEnd = new Date(Date.parse(scenario.start_at) + 30 * 60_000).toISOString();
    const vehicle = scenario.vehicles[0]!;
    scenario.vehicles = [{
      ...vehicle,
      availability_start: scenario.start_at,
      availability_end: intervalEnd,
      required_energy_kwh: 5e-10,
      charging_power_kw: vehiclePowerKw,
    }];
    const payload = forecastSchedulingResultFixture(scenario);
    const allocation = makeAllocation(scenario, 0, vehicle.vehicle_id, 0, 30, 1e-9);
    replaceScheduleAllocations(payload.immediate_baseline!, scenario, [allocation], [1e-9]);
    replaceScheduleAllocations(payload.forecast_aware!, scenario, [allocation], [1e-9]);
    setComparisonMetrics(payload);
    expect(payload.immediate_baseline!.unmet_energy_kwh[vehicle.vehicle_id]).toBe(0);
    expect(payload.immediate_baseline!.aggregate_depot_load[0]!.peak_power_kw).toBe(1e-9);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(payload)));

    await expect(evaluateForecastScenario(scenario)).rejects.toMatchObject({
      name: 'ForecastApiError',
      status: 200,
      result: null,
    });
  });

  it('checks actual overlapping power when interval-average load is below depot capacity', async () => {
    const scenario = createDefaultForecastScenario();
    scenario.intervals = 1;
    scenario.depot_power_kw = 13;
    const intervalEnd = new Date(Date.parse(scenario.start_at) + 30 * 60_000).toISOString();
    for (const vehicle of scenario.vehicles) {
      vehicle.availability_start = scenario.start_at;
      vehicle.availability_end = intervalEnd;
      vehicle.required_energy_kwh = 7 / 3;
      vehicle.charging_power_kw = 7;
    }
    const payload = forecastSchedulingResultFixture(scenario);
    const allocations = [
      makeAllocation(scenario, 0, scenario.vehicles[0]!.vehicle_id, 0, 20, 7),
      makeAllocation(scenario, 0, scenario.vehicles[1]!.vehicle_id, 10, 20, 7),
    ];
    replaceScheduleAllocations(payload.immediate_baseline!, scenario, allocations, [14]);
    replaceScheduleAllocations(payload.forecast_aware!, scenario, allocations, [14]);
    setComparisonMetrics(payload);
    expect(payload.immediate_baseline!.aggregate_depot_load[0]!.average_power_kw).toBeLessThan(13);
    expect(payload.immediate_baseline!.aggregate_depot_load[0]!.peak_power_kw).toBe(14);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(payload)));

    await expect(evaluateForecastScenario(scenario)).rejects.toMatchObject({
      name: 'ForecastApiError',
      status: 200,
      result: null,
    });
  });

  it('rejects allocations outside the exact submitted vehicle availability window', async () => {
    const scenario = createDefaultForecastScenario();
    scenario.vehicles[0]!.availability_end = new Date(Date.parse(scenario.start_at) + 15 * 60_000).toISOString();
    const payload = forecastSchedulingResultFixture(scenario);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(payload)));

    await expect(evaluateForecastScenario(scenario)).rejects.toMatchObject({
      name: 'ForecastApiError',
      status: 200,
      result: null,
    });
  });

  it('rejects per-vehicle power above the submitted limit despite passing API checks', async () => {
    const scenario = createDefaultForecastScenario();
    const payload = forecastSchedulingResultFixture(scenario);
    scenario.vehicles[0]!.charging_power_kw = 6;
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(payload)));

    await expect(evaluateForecastScenario(scenario)).rejects.toMatchObject({
      name: 'ForecastApiError',
      status: 200,
      result: null,
    });
  });

  it('accepts backend summation rounding and tiny positive delivery but rejects an energy violation', async () => {
    const scenario = createDefaultForecastScenario();
    scenario.intervals = 47;
    scenario.depot_power_kw = 1_000_000_000;
    const required = [
      783_798_589.0347726,
      303_312_726.07892746,
      476_596_954.1523558,
      1e-10,
    ];
    const horizonEnd = new Date(Date.parse(scenario.start_at) + scenario.intervals * 30 * 60_000).toISOString();
    const prototypeVehicle = scenario.vehicles[0]!;
    scenario.vehicles = required.map((energy, index) => ({
      ...prototypeVehicle,
      vehicle_id: `vehicle-${index + 1}`,
      availability_start: scenario.start_at,
      availability_end: horizonEnd,
      required_energy_kwh: energy,
      charging_power_kw: index === 3 ? 1e-6 : 1_000_000_000,
    }));

    const payload = forecastSchedulingResultFixture(scenario);
    const allocations = [
      makeAllocation(scenario, 0, 'vehicle-1', 0, 30, 1_000_000_000),
      makeAllocation(scenario, 1, 'vehicle-1', 0, 30, (required[0]! - 500_000_000) * 2),
      makeAllocation(scenario, 2, 'vehicle-2', 0, 30, required[1]! * 2),
      makeAllocation(scenario, 3, 'vehicle-3', 0, 30, required[2]! * 2),
      makeAllocation(scenario, 4, 'vehicle-4', 0, 30, 2e-10),
    ];
    const intervalPeaks = [
      1_000_000_000,
      (required[0]! - 500_000_000) * 2,
      required[1]! * 2,
      required[2]! * 2,
      2e-10,
    ];
    replaceScheduleAllocations(payload.immediate_baseline!, scenario, allocations, intervalPeaks);
    replaceScheduleAllocations(payload.forecast_aware!, scenario, allocations, intervalPeaks);
    for (const schedule of [payload.immediate_baseline!, payload.forecast_aware!]) {
      schedule.total_required_energy_kwh = 1_563_708_269.2660558;
      schedule.total_delivered_energy_kwh = 1_563_708_269.2660558;
    }
    setComparisonMetrics(payload);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(payload)));

    const result = await evaluateForecastScenario(scenario);
    expect(result.comparison_eligible).toBe(true);
    expect(result.immediate_baseline?.total_required_energy_kwh).toBe(1_563_708_269.2660558);
    expect(result.immediate_baseline?.vehicle_results['vehicle-4']?.delivered_energy_kwh).toBe(1e-10);

    const invalidPayload = structuredClone(payload);
    invalidPayload.forecast_aware!.total_required_energy_kwh += 0.1;
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(invalidPayload)));
    await expect(evaluateForecastScenario(scenario)).rejects.toMatchObject({
      name: 'ForecastApiError',
      status: 200,
      result: null,
    });
  });

  it('preserves negative reductions, zero reductions and zero-baseline outcomes', async () => {
    const scenario = createDefaultForecastScenario();
    const cases: Array<{ intervalIntensities: number[]; sign: -1 | 0; baselineIsZero?: boolean }> = [
      { intervalIntensities: [10, 100, 200, 300], sign: -1 },
      { intervalIntensities: [100, 100, 100, 100], sign: 0 },
      { intervalIntensities: [0, 0, 0, 100], sign: -1, baselineIsZero: true },
    ];

    for (const testCase of cases) {
      const payload = forecastSchedulingResultFixture(scenario);
      for (const schedule of [payload.immediate_baseline!, payload.forecast_aware!]) {
        for (const allocation of schedule.allocations) {
          allocation.carbon_intensity_value = testCase.intervalIntensities[allocation.interval_index]!;
        }
        schedule.carbon_intensity_weighted_energy_proxy = schedule.allocations.reduce(
          (sum, allocation) => sum + allocation.energy_kwh * allocation.carbon_intensity_value,
          0,
        );
      }
      setComparisonMetrics(payload);
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(payload)));

      const result = await evaluateForecastScenario(scenario);
      if (testCase.sign < 0) expect(result.absolute_carbon_proxy_reduction).toBeLessThan(0);
      else expect(result.absolute_carbon_proxy_reduction).toBe(0);

      if (testCase.baselineIsZero) {
        expect(result.percentage_carbon_proxy_reduction).toBeNull();
        expect(result.percentage_difference_unavailable_reason).toMatch(/baseline proxy is zero/i);
      } else {
        expect(result.percentage_carbon_proxy_reduction).not.toBeNull();
      }
    }
  });

  it('retains a valid percentage for a near-zero positive baseline proxy', async () => {
    const scenario = createDefaultForecastScenario();
    for (const vehicle of scenario.vehicles) vehicle.required_energy_kwh = 1e-6;
    const payload = forecastSchedulingResultFixture(scenario);
    setScheduleIntensity(payload.immediate_baseline!, 400);
    setScheduleIntensity(payload.forecast_aware!, 200);
    setComparisonMetrics(payload);
    expect(payload.immediate_baseline!.carbon_intensity_weighted_energy_proxy).toBeGreaterThan(0);
    expect(payload.immediate_baseline!.carbon_intensity_weighted_energy_proxy).toBeLessThan(0.01);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(payload)));

    const result = await evaluateForecastScenario(scenario);

    expect(result.percentage_carbon_proxy_reduction).toBeCloseTo(50, 10);
  });

  it('accepts binary64-scale representation noise without rewriting API values', async () => {
    const scenario = createDefaultForecastScenario();
    const payload = forecastSchedulingResultFixture(scenario);
    const reportedReduction = payload.absolute_carbon_proxy_reduction!;
    const reportedPercentage = payload.percentage_carbon_proxy_reduction!;
    payload.absolute_carbon_proxy_reduction = reportedReduction
      + Math.abs(reportedReduction) * Number.EPSILON * 2;
    payload.percentage_carbon_proxy_reduction = reportedPercentage
      + Math.abs(reportedPercentage) * Number.EPSILON * 2;
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(payload)));

    const result = await evaluateForecastScenario(scenario);

    expect(result.absolute_carbon_proxy_reduction).toBe(payload.absolute_carbon_proxy_reduction);
    expect(result.percentage_carbon_proxy_reduction).toBe(payload.percentage_carbon_proxy_reduction);
  });
});

function forecastTimelineFixture(requestedFrom: string, retrievedAt: string) {
  const start = Date.parse(requestedFrom);
  return {
    status: 'available',
    measurement_type: 'forecast',
    units: 'gCO2/kWh',
    region_id: 13,
    region_name: 'London',
    requested_from: requestedFrom,
    valid_until: new Date(start + 48 * 30 * 60_000).toISOString(),
    retrieved_at: retrievedAt,
    source_api: 'neso-carbon-intensity-api',
    source_url: `https://api.carbonintensity.org.uk/regional/intensity/${requestedFrom.slice(0, 16)}Z/fw24h/regionid/13`,
    intervals: Array.from({ length: 48 }, (_, index) => {
      const observedAt = new Date(start + index * 30 * 60_000).toISOString();
      return {
        measurement_type: 'forecast',
        observed_at: observedAt,
        valid_until: new Date(Date.parse(observedAt) + 30 * 60_000).toISOString(),
        intensity_gco2_per_kwh: 100,
        intensity_index: 'moderate',
      };
    }),
  };
}

function forecastTimelineHttpResponse(body: unknown, serverTime?: string): Response {
  const headers = new Headers({ 'Content-Type': 'application/json' });
  if (serverTime) headers.set('Date', new Date(serverTime).toUTCString());
  return new Response(JSON.stringify(body), {
    status: 200,
    headers,
  });
}

describe('getCarbonIntensityForecast', () => {
  it('accepts aligned forecast intervals within the HTTP Date handoff tolerance', async () => {
    const timeline = forecastTimelineFixture('2026-10-30T12:30:00Z', '2026-10-30T12:03:01.999Z');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      forecastTimelineHttpResponse(timeline, '2026-10-30T12:03:00Z'),
    ));

    await expect(getCarbonIntensityForecast(13)).resolves.toEqual(timeline);
  });

  it('rejects timeline provenance when the response has no trusted server Date header', async () => {
    const timeline = forecastTimelineFixture('2026-10-30T12:30:00Z', '2026-10-30T12:03:00Z');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(forecastTimelineHttpResponse(timeline)));

    await expect(getCarbonIntensityForecast(13)).rejects.toMatchObject({
      name: 'ForecastApiError',
      status: 200,
      message: 'The server returned an invalid or mismatched forecast timeline.',
    });
  });

  it.each([
    {
      description: 'an unaligned requested forecast interval',
      timeline: forecastTimelineFixture('2026-10-30T12:15:00Z', '2026-10-30T12:03:00Z'),
      serverTime: '2026-10-30T12:03:00Z',
    },
    {
      description: 'a retrieval timestamp beyond the server response time',
      timeline: forecastTimelineFixture('2026-10-30T12:30:00Z', '2026-10-30T12:03:02.001Z'),
      serverTime: '2026-10-30T12:03:00Z',
    },
  ])('rejects $description', async ({ timeline, serverTime }) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      forecastTimelineHttpResponse(timeline, serverTime),
    ));

    await expect(getCarbonIntensityForecast(13)).rejects.toMatchObject({
      name: 'ForecastApiError',
      status: 200,
      message: 'The server returned an invalid or mismatched forecast timeline.',
    });
  });

  it('classifies a response-body timeout as a preview timeout', async () => {
    const timeoutController = new AbortController();
    const timeoutSpy = vi.spyOn(AbortSignal, 'timeout').mockReturnValue(timeoutController.signal);
    const response = {
      ok: true,
      status: 200,
      json: vi.fn().mockImplementation(() => {
        timeoutController.abort();
        return Promise.reject(new DOMException('private preview-body timeout detail', 'AbortError'));
      }),
    } as unknown as Response;
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response));

    try {
      await expect(getCarbonIntensityForecast(1)).rejects.toMatchObject({
        name: 'ForecastApiError',
        status: 0,
        message: 'The forecast preview request timed out.',
      });
    } finally {
      timeoutSpy.mockRestore();
    }
  });
});

describe('getCarbonIntensity', () => {
  const retrievedAt = new Date();
  const intervalStart = new Date(retrievedAt.getTime() - 5 * 60 * 1000);
  const intervalEnd = new Date(intervalStart.getTime() + 30 * 60 * 1000);
  const reading = {
    intensity_gco2_per_kwh: 79,
    measurement_type: 'actual',
    units: 'gCO2/kWh',
    region_id: 13,
    region_name: 'London',
    intensity_index: 'moderate',
    observed_at: intervalStart.toISOString(),
    valid_until: intervalEnd.toISOString(),
    retrieved_at: retrievedAt.toISOString(),
    interval_status: 'in_progress',
    interval_lag_seconds: 0,
    is_fresh: true,
    age_seconds: 300,
    fresh_until: new Date(retrievedAt.getTime() + 55 * 60 * 1000).toISOString(),
    source_api: 'neso-carbon-intensity-api',
    source_url: 'https://api.carbonintensity.org.uk/regional/regionid/13',
  };

  it('makes a read-only request and returns validated live data', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(reading));
    vi.stubGlobal('fetch', fetchMock);

    await expect(getCarbonIntensity()).resolves.toEqual(reading);
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/energy/carbon-intensity',
      expect.objectContaining({
        method: 'GET',
        headers: { Accept: 'application/json' },
        cache: 'no-store',
      }),
    );
  });

  it('classifies a body-read timeout as a live-data timeout', async () => {
    const timeoutController = new AbortController();
    const timeoutSpy = vi.spyOn(AbortSignal, 'timeout').mockReturnValue(timeoutController.signal);
    const response = {
      ok: true,
      status: 200,
      headers: new Headers(),
      json: vi.fn().mockImplementation(() => {
        timeoutController.abort();
        return Promise.reject(new DOMException('private live-body timeout detail', 'AbortError'));
      }),
    } as unknown as Response;
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response));

    try {
      await expect(getCarbonIntensity()).rejects.toMatchObject({
        name: 'CarbonIntensityApiError',
        status: 0,
        message: 'Live NESO carbon-intensity data request timed out.',
      });
    } finally {
      timeoutSpy.mockRestore();
    }
  });

  it('reports the verified API server Date header separately from upstream retrieval time', async () => {
    const response = new Response(JSON.stringify(reading), {
      headers: {
        'Content-Type': 'application/json',
        Date: 'Fri, 30 Oct 2026 12:30:00 GMT',
      },
    });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response));
    const onServerTime = vi.fn();

    await getCarbonIntensity(undefined, onServerTime);

    expect(onServerTime).toHaveBeenCalledWith('2026-10-30T12:30:00.000Z');
  });

  it('rejects a response with an unsupported measurement unit', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ ...reading, units: 'kWh' })));

    await expect(getCarbonIntensity()).rejects.toMatchObject({ name: 'CarbonIntensityApiError' });
  });

  it('rejects the persisted-observation unit on the raw NESO endpoint', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({
      ...reading,
      units: 'gCO2e/kWh',
    })));

    await expect(getCarbonIntensity()).rejects.toMatchObject({ name: 'CarbonIntensityApiError' });
  });

  it('rejects a response without a freshness deadline', async () => {
    const { fresh_until: _freshUntil, ...responseWithoutFreshnessDeadline } = reading;
    expect(Date.parse(_freshUntil)).not.toBeNaN();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(responseWithoutFreshnessDeadline)));

    await expect(getCarbonIntensity()).rejects.toMatchObject({ name: 'CarbonIntensityApiError' });
  });

  it('rejects a source URL outside the configured NESO regional endpoint', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({
      ...reading,
      source_url: 'https://api.carbonintensity.org.uk/regional/13',
    })));

    await expect(getCarbonIntensity()).rejects.toMatchObject({ name: 'CarbonIntensityApiError' });
  });

  it('does not expose upstream error details', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ detail: 'internal stack trace' }, 503)));

    await expect(getCarbonIntensity()).rejects.toMatchObject({
      name: 'CarbonIntensityApiError',
      message: expect.not.stringContaining('internal stack trace'),
    });
  });
});
