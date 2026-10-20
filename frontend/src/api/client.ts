import type { components } from './schema';

export type Scenario = components['schemas']['CarbonAwareDepotScenarioRequest'];
export type SimulationResult = components['schemas']['CarbonAwareSimulationResult'];
export type CarbonAwareSchedule = components['schemas']['CarbonAwareSchedule'];
export type ChargingAllocation = components['schemas']['ChargingAllocation'];
export type AggregateDepotLoad = components['schemas']['AggregateDepotLoad'];
export type CarbonIntensityInterval = components['schemas']['CarbonIntensityInterval'];
export type Vehicle = Scenario['vehicles'][number];

export type FieldErrors = Record<string, string>;

export class SimulationApiError extends Error {
  readonly status: number;
  readonly fieldErrors: FieldErrors;

  constructor(message: string, status: number, fieldErrors: FieldErrors = {}) {
    super(message);
    this.name = 'SimulationApiError';
    this.status = status;
    this.fieldErrors = fieldErrors;
  }
}

function validationTarget(location: string, message: string): string {
  if (location === 'vehicles' || /^vehicles\.\d+$/.test(location)) return 'vehicles';
  if (location === 'carbon_intensity_profile' || /^carbon_intensity_profile\.\d+$/.test(location)) return 'carbon_intensity_profile';
  if (location) return location;

  const lowerMessage = message.toLowerCase();
  if (/availability|vehicle|charger/.test(lowerMessage)) return 'vehicles';
  if (/carbon|intensity|profile|synthetic|geography|provenance/.test(lowerMessage)) return 'carbon_intensity_profile';
  if (/start_at|scenario start/.test(lowerMessage)) return 'start_at';
  if (/depot/.test(lowerMessage)) return 'depot_power_kw';
  if (/interval/.test(lowerMessage)) return 'intervals';
  if (/scenario_id/.test(lowerMessage)) return 'scenario_id';
  return 'scenario';
}

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '';

export async function evaluateScenario(
  scenario: Scenario,
  signal?: AbortSignal,
): Promise<SimulationResult> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/api/v1/simulations/ev-depot/evaluate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(scenario),
      signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new SimulationApiError('The simulation API could not be reached. Check that the local API is running.', 0);
  }

  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const fieldErrors: FieldErrors = {};
    let message = response.status >= 500
      ? 'The simulation service could not complete this request.'
      : `Simulation request rejected (HTTP ${response.status}).`;

    if (response.status === 422 && isValidationErrorBody(body)) {
      for (const issue of body.detail) {
        const location = issue.loc.filter((part) => part !== 'body').join('.');
        fieldErrors[validationTarget(location, issue.msg)] = issue.msg;
      }
      if (Object.keys(fieldErrors).length > 0) {
        message = 'Check the highlighted scenario fields and try again.';
      }
    }
    throw new SimulationApiError(message, response.status, fieldErrors);
  }

  return body as SimulationResult;
}

function isValidationErrorBody(
  value: unknown,
): value is { detail: Array<{ loc: Array<string | number>; msg: string }> } {
  if (typeof value !== 'object' || value === null || !('detail' in value)) return false;
  const detail = value.detail;
  return Array.isArray(detail) && detail.every((item: unknown) => (
    typeof item === 'object'
    && item !== null
    && 'loc' in item
    && Array.isArray(item.loc)
    && 'msg' in item
    && typeof item.msg === 'string'
  ));
}
