import type { components } from './schema';

export type Scenario = components['schemas']['CarbonAwareDepotScenarioRequest'];
export type SimulationResult = components['schemas']['CarbonAwareSimulationResult'];
export type CarbonIntensityResponse = components['schemas']['CarbonIntensityResponse'];
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

export class CarbonIntensityApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'CarbonIntensityApiError';
    this.status = status;
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

export async function getCarbonIntensity(signal?: AbortSignal): Promise<CarbonIntensityResponse> {
  const timeoutSignal = AbortSignal.timeout(10_000);
  const requestSignal = signal ? AbortSignal.any([signal, timeoutSignal]) : timeoutSignal;
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/api/v1/energy/carbon-intensity`, {
      method: 'GET',
      headers: { Accept: 'application/json' },
      cache: 'no-store',
      signal: requestSignal,
    });
  } catch (error) {
    if (signal?.aborted && error instanceof DOMException && error.name === 'AbortError') {
      throw error;
    }
    throw new CarbonIntensityApiError(
      'Live NESO carbon-intensity data is temporarily unavailable.',
      0,
    );
  }

  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    throw new CarbonIntensityApiError(
      'Live NESO carbon-intensity data is temporarily unavailable.',
      response.status,
    );
  }
  if (!isCarbonIntensityResponse(body)) {
    throw new CarbonIntensityApiError('The NESO carbon-intensity response was invalid.', response.status);
  }
  return body;
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

function isCarbonIntensityResponse(value: unknown): value is CarbonIntensityResponse {
  if (typeof value !== 'object' || value === null) return false;
  const record = value as Record<string, unknown>;
  const sourceUrl = typeof record.source_url === 'string' ? parsePublicNesoUrl(record.source_url) : null;
  return isFiniteNonNegative(record.intensity_gco2_per_kwh)
    && (record.measurement_type === 'actual' || record.measurement_type === 'forecast')
    && record.units === 'gCO2/kWh'
    && isFiniteNonNegative(record.region_id)
    && Number.isInteger(record.region_id)
    && record.region_id > 0
    && typeof record.region_name === 'string'
    && record.region_name.trim().length > 0
    && typeof record.intensity_index === 'string'
    && record.intensity_index.trim().length > 0
    && isZonedTimestamp(record.observed_at)
    && isZonedTimestamp(record.valid_until)
    && isZonedTimestamp(record.retrieved_at)
    && isZonedTimestamp(record.fresh_until)
    && (record.interval_status === 'upcoming'
      || record.interval_status === 'in_progress'
      || record.interval_status === 'completed')
    && isFiniteNonNegative(record.interval_lag_seconds)
    && typeof record.is_fresh === 'boolean'
    && (!record.is_fresh || Date.parse(record.fresh_until as string) > Date.parse(record.retrieved_at as string))
    && isFiniteNonNegative(record.age_seconds)
    && record.source_api === 'neso-carbon-intensity-api'
    && sourceUrl !== null
    && sourceUrl.pathname === `/regional/regionid/${record.region_id}`
    && sourceUrl.search === ''
    && sourceUrl.hash === '';
}

function isFiniteNonNegative(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0;
}

function isZonedTimestamp(value: unknown): value is string {
  return typeof value === 'string'
    && /(?:Z|[+-]\d{2}:\d{2})$/.test(value)
    && Number.isFinite(Date.parse(value));
}

function parsePublicNesoUrl(value: string): URL | null {
  try {
    const url = new URL(value);
    if (
      url.protocol !== 'https:'
      || url.hostname !== 'api.carbonintensity.org.uk'
      || url.port !== ''
      || url.username !== ''
      || url.password !== ''
    ) return null;
    return url;
  } catch {
    return null;
  }
}
