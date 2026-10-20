import { describe, expect, it, vi } from 'vitest';
import { createDemoScenario } from '../data/scenario';
import { evaluateScenario, SimulationApiError } from './client';
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
