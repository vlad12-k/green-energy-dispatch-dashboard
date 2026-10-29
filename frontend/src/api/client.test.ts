import { describe, expect, it, vi } from 'vitest';
import { createDemoScenario } from '../data/scenario';
import { evaluateScenario, getCarbonIntensity, SimulationApiError } from './client';
import { jsonResponse, simulationResult } from '../test/fixtures';


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
