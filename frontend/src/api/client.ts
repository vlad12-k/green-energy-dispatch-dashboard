import type { components } from './schema';

export type Scenario = components['schemas']['CarbonAwareDepotScenarioRequest'];
export type SimulationResult = components['schemas']['CarbonAwareSimulationResult'];
export type CarbonIntensityResponse = components['schemas']['CarbonIntensityResponse'];
export type CarbonAwareSchedule = components['schemas']['CarbonAwareSchedule'];
export type ChargingAllocation = components['schemas']['ChargingAllocation'];
export type AggregateDepotLoad = components['schemas']['AggregateDepotLoad'];
export type CarbonIntensityInterval = components['schemas']['CarbonIntensityInterval'];
export type Vehicle = Scenario['vehicles'][number];
export type ForecastScenario = components['schemas']['ForecastBackedDepotScenarioRequest'];
export type ForecastTimeline = components['schemas']['CarbonIntensityForecastTimelineResponse'];
export type ForecastSchedulingResult = components['schemas']['ForecastBackedSchedulingResult'];
export type ForecastVehicle = ForecastScenario['vehicles'][number];

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

export class ForecastApiError extends Error {
  readonly status: number;
  readonly fieldErrors: FieldErrors;
  readonly result: ForecastSchedulingResult | null;

  constructor(
    message: string,
    status: number,
    fieldErrors: FieldErrors = {},
    result: ForecastSchedulingResult | null = null,
  ) {
    super(message);
    this.name = 'ForecastApiError';
    this.status = status;
    this.fieldErrors = fieldErrors;
    this.result = result;
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
const FLOATING_POINT_ULP_TOLERANCE = 4;
const POWER_LIMIT_RELATIVE_TOLERANCE = 1e-12;
const TIMESTAMP_PRECISION_TOLERANCE_SECONDS = 0.001;
const HTTP_DATE_CHRONOLOGY_TOLERANCE_MS = 2_000;
const MAX_FORECAST_AGE_SECONDS = 3600;
const REQUIRED_CONSTRAINT_CHECKS = [
  'scenario_bounds_respected',
  'availability_windows_respected',
  'vehicle_power_limits_respected',
  'depot_power_limit_respected',
  'energy_conservation_respected',
] as const;

async function responseJsonOrNull(response: Response, signal?: AbortSignal): Promise<unknown> {
  try {
    return await response.json();
  } catch (error) {
    if (!signal?.aborted) return null;
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new DOMException('The operation was aborted.', 'AbortError');
  }
}

export async function evaluateScenario(
  scenario: Scenario,
  signal?: AbortSignal,
): Promise<SimulationResult> {
  const timeoutSignal = AbortSignal.timeout(15_000);
  const requestSignal = signal ? AbortSignal.any([signal, timeoutSignal]) : timeoutSignal;
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/api/v1/simulations/ev-depot/evaluate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(scenario),
      signal: requestSignal,
    });
  } catch (error) {
    if (signal?.aborted) {
      if (error instanceof DOMException && error.name === 'AbortError') throw error;
      throw new DOMException('The operation was aborted.', 'AbortError');
    }
    throw new SimulationApiError(
      timeoutSignal.aborted
        ? 'The simulation request timed out. Review the scenario before retrying.'
        : 'The simulation API could not be reached. Check that the local API is running.',
      0,
    );
  }

  let body: unknown;
  try {
    body = await responseJsonOrNull(response, requestSignal);
  } catch (error) {
    if (signal?.aborted) throw error;
    if (timeoutSignal.aborted) {
      throw new SimulationApiError('The simulation request timed out. Review the scenario before retrying.', 0);
    }
    throw error;
  }
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

export async function getCarbonIntensity(
  signal?: AbortSignal,
  onServerTime?: (serverTime: string) => void,
): Promise<CarbonIntensityResponse> {
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

  const serverDate = response.headers.get('Date');
  const serverTimestamp = serverDate === null ? Number.NaN : Date.parse(serverDate);
  if (Number.isFinite(serverTimestamp)) {
    onServerTime?.(new Date(serverTimestamp).toISOString());
  }

  let body: unknown;
  try {
    body = await responseJsonOrNull(response, requestSignal);
  } catch (error) {
    if (signal?.aborted) throw error;
    if (timeoutSignal.aborted) {
      throw new CarbonIntensityApiError('Live NESO carbon-intensity data request timed out.', 0);
    }
    throw error;
  }
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

export async function evaluateForecastScenario(
  scenario: ForecastScenario,
  signal?: AbortSignal,
  expectedRegion?: Pick<CarbonIntensityResponse, 'region_id' | 'region_name'>,
): Promise<ForecastSchedulingResult> {
  const timeoutSignal = AbortSignal.timeout(15_000);
  const requestSignal = signal ? AbortSignal.any([signal, timeoutSignal]) : timeoutSignal;
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/api/v1/simulations/ev-depot/forecast-evaluate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ scenario }),
      signal: requestSignal,
    });
  } catch (error) {
    if (signal?.aborted && error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new ForecastApiError(
      timeoutSignal.aborted
        ? 'Forecast evaluation timed out. Review the current scenario before retrying.'
        : 'Forecast evaluation could not reach the API. Check the local service and try again.',
      0,
    );
  }

  let body: unknown;
  try {
    body = await responseJsonOrNull(response, requestSignal);
  } catch (error) {
    if (signal?.aborted) throw error;
    if (timeoutSignal.aborted) {
      throw new ForecastApiError(
        'Forecast evaluation timed out. Review the current scenario before retrying.',
        0,
      );
    }
    throw error;
  }
  if (!response.ok) {
    if (response.status === 503 && isForecastSchedulingResult(body, scenario, expectedRegion) && body.status === 'unavailable') {
      const reason = body.unavailable_reason ?? 'forecast_unavailable';
      throw new ForecastApiError(`Forecast evaluation is temporarily unavailable (${reason}).`, response.status, {}, body);
    }
    if (response.status === 429) {
      throw new ForecastApiError('The forecast service rate-limited this request. Wait before trying again.', 429);
    }
    if (response.status === 503) {
      throw new ForecastApiError('Forecast evaluation is temporarily unavailable. The server returned no usable forecast result.', 503);
    }
    if (response.status === 422) {
      const fieldErrors = forecastValidationErrors(body);
      throw new ForecastApiError(
        Object.keys(fieldErrors).length > 0
          ? 'Check the highlighted forecast scenario fields and try again.'
          : 'The forecast scenario was rejected by the API (HTTP 422).',
        422,
        fieldErrors,
      );
    }
    throw new ForecastApiError(
      response.status >= 500
        ? 'Forecast evaluation could not be completed by the service.'
        : `Forecast evaluation was rejected (HTTP ${response.status}).`,
      response.status,
    );
  }
  if (!isForecastSchedulingResult(body, scenario, expectedRegion) || body.status !== 'available') {
    throw new ForecastApiError('The API returned an invalid or unavailable forecast result.', response.status);
  }
  return body;
}

export async function getCarbonIntensityForecast(
  regionId: number,
  signal?: AbortSignal,
): Promise<ForecastTimeline> {
  if (!Number.isInteger(regionId) || regionId < 1 || regionId > 17) {
    throw new ForecastApiError('A verified NESO regional identifier is required.', 0);
  }
  const timeoutSignal = AbortSignal.timeout(12_000);
  const requestSignal = signal ? AbortSignal.any([signal, timeoutSignal]) : timeoutSignal;
  let response: Response;
  try {
    response = await fetch(
      `${API_BASE_URL}/api/v1/energy/carbon-intensity/forecast?region_id=${regionId}`,
      { method: 'GET', headers: { Accept: 'application/json' }, cache: 'no-store', signal: requestSignal },
    );
  } catch (error) {
    if (signal?.aborted && error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new ForecastApiError(
      timeoutSignal.aborted
        ? 'The forecast preview request timed out.'
        : 'The forecast preview could not reach the API.',
      0,
    );
  }
  let body: unknown;
  try {
    body = await responseJsonOrNull(response, requestSignal);
  } catch (error) {
    if (signal?.aborted) throw error;
    if (timeoutSignal.aborted) {
      throw new ForecastApiError('The forecast preview request timed out.', 0);
    }
    throw error;
  }
  if (!response.ok) {
    const message = response.status === 429
      ? 'The forecast preview was rate-limited. Wait before refreshing.'
      : response.status === 503
        ? 'The forecast preview is unavailable from the server.'
        : `The forecast preview request failed (HTTP ${response.status}).`;
    throw new ForecastApiError(message, response.status);
  }
  const serverTime = Date.parse(response.headers.get('Date') ?? '');
  if (!Number.isFinite(serverTime)
    || !isForecastTimeline(body, serverTime)
    || body.region_id !== regionId) {
    throw new ForecastApiError('The server returned an invalid or mismatched forecast timeline.', response.status);
  }
  return body;
}

function isValidationErrorBody(
  value: unknown,
): value is { detail: Array<{ loc: Array<string | number>; msg: string }> } {
  if (!isRecord(value) || !('detail' in value)) return false;
  const detail = value.detail;
  return Array.isArray(detail) && detail.every((item: unknown) => (
    isRecord(item)
    && 'loc' in item
    && Array.isArray(item.loc)
    && 'msg' in item
    && typeof item.msg === 'string'
  ));
}

function isCarbonIntensityResponse(value: unknown): value is CarbonIntensityResponse {
  if (!isRecord(value)) return false;
  const sourceUrl = typeof value.source_url === 'string' ? parsePublicNesoUrl(value.source_url) : null;
  return isFiniteNonNegative(value.intensity_gco2_per_kwh)
    && (value.measurement_type === 'actual' || value.measurement_type === 'forecast')
    && value.units === 'gCO2/kWh'
    && isRegionId(value.region_id)
    && typeof value.region_name === 'string'
    && value.region_name.trim().length > 0
    && typeof value.intensity_index === 'string'
    && value.intensity_index.trim().length > 0
    && isZonedTimestamp(value.observed_at)
    && isZonedTimestamp(value.valid_until)
    && isZonedTimestamp(value.retrieved_at)
    && isZonedTimestamp(value.fresh_until)
    && (value.interval_status === 'upcoming'
      || value.interval_status === 'in_progress'
      || value.interval_status === 'completed')
    && isFiniteNonNegative(value.interval_lag_seconds)
    && typeof value.is_fresh === 'boolean'
    && (!value.is_fresh || Date.parse(value.fresh_until) > Date.parse(value.retrieved_at))
    && isFiniteNonNegative(value.age_seconds)
    && value.source_api === 'neso-carbon-intensity-api'
    && sourceUrl !== null
    && sourceUrl.pathname === `/regional/regionid/${value.region_id}`
    && sourceUrl.search === ''
    && sourceUrl.hash === '';
}

function forecastValidationErrors(value: unknown): FieldErrors {
  if (!isValidationErrorBody(value)) return {};
  const errors: FieldErrors = {};
  for (const issue of value.detail) {
    const path = issue.loc.filter((part) => part !== 'body' && part !== 'scenario').join('.');
    errors[path || 'scenario'] = issue.msg;
  }
  return errors;
}

function isForecastTimeline(value: unknown, serverTime: number): value is ForecastTimeline {
  if (!isRecord(value)) return false;
  const requestedFrom = strictUtcEpoch(value.requested_from);
  const retrievedAt = strictUtcEpoch(value.retrieved_at);
  const validUntil = strictUtcEpoch(value.valid_until);
  if (
    value.status !== 'available'
    || value.measurement_type !== 'forecast'
    || value.units !== 'gCO2/kWh'
    || value.source_api !== 'neso-carbon-intensity-api'
    || !isRegionId(value.region_id)
    || typeof value.region_name !== 'string'
    || value.region_name.trim().length === 0
    || requestedFrom === null
    || retrievedAt === null
    || !isUtcHalfHour(requestedFrom)
    || retrievedAt > serverTime + HTTP_DATE_CHRONOLOGY_TOLERANCE_MS
    || validUntil !== requestedFrom + 48 * 30 * 60_000
    || !Array.isArray(value.intervals)
    || value.intervals.length !== 48
    || !isNesoForecastUrl(value.source_url, value.region_id, requestedFrom)
  ) return false;

  return value.intervals.every((item: unknown, index: number) => {
    if (!isRecord(item)) return false;
    const observedAt = strictUtcEpoch(item.observed_at);
    const intervalEnd = strictUtcEpoch(item.valid_until);
    return item.measurement_type === 'forecast'
      && isFiniteNonNegative(item.intensity_gco2_per_kwh)
      && typeof item.intensity_index === 'string'
      && item.intensity_index.trim().length > 0
      && observedAt === requestedFrom + index * 30 * 60_000
      && intervalEnd === observedAt + 30 * 60_000;
  });
}

function isForecastSchedulingResult(
  value: unknown,
  scenario: ForecastScenario,
  expectedRegion?: Pick<CarbonIntensityResponse, 'region_id' | 'region_name'>,
): value is ForecastSchedulingResult {
  if (!isRecord(value)
    || value.scenario_id !== scenario.scenario_id
    || (value.status !== 'available' && value.status !== 'unavailable')
    || value.advisory_only !== true
    || value.forecast_based !== true
    || value.measured !== false
    || value.real_world_emissions_reduction_claimed !== false
    || value.carbon_proxy_unit !== 'gCO2') return false;

  if (value.status === 'unavailable') {
    return typeof value.unavailable_reason === 'string'
      && value.input_provenance === null
      && value.immediate_baseline === null
      && value.forecast_aware === null
      && value.comparison_eligible === false
      && value.absolute_carbon_proxy_reduction === null
      && value.percentage_carbon_proxy_reduction === null
      && typeof value.comparison_unavailable_reason === 'string'
      && value.comparison_unavailable_reason.length > 0;
  }

  const baselineComplete = isCompleteForecastSchedule(value.immediate_baseline, scenario);
  const forecastAwareComplete = isCompleteForecastSchedule(value.forecast_aware, scenario);
  return isForecastProvenance(value.input_provenance, scenario, expectedRegion)
    && isForecastSchedule(value.immediate_baseline, scenario, 'immediate_charge_in_vehicle_id_order')
    && isForecastSchedule(value.forecast_aware, scenario, 'carbon_aware_edf_then_bounded_carbon_shift_v1')
    && forecastSchedulesShareIntervalIntensities(value.immediate_baseline, value.forecast_aware)
    && typeof value.comparison_eligible === 'boolean'
    && value.comparison_eligible === (baselineComplete && forecastAwareComplete)
    && (value.comparison_eligible
      ? isConsistentCarbonProxyComparison(value)
        && value.comparison_unavailable_reason === null
      : value.absolute_carbon_proxy_reduction === null
        && value.percentage_carbon_proxy_reduction === null
        && typeof value.comparison_unavailable_reason === 'string'
        && value.comparison_unavailable_reason.length > 0);
}

function forecastSchedulesShareIntervalIntensities(baseline: unknown, forecastAware: unknown): boolean {
  const intensitiesByInterval = new Map<number, number>();
  for (const schedule of [baseline, forecastAware]) {
    if (!isRecord(schedule) || !Array.isArray(schedule.allocations)) return false;
    for (const allocation of schedule.allocations) {
      if (!isForecastAllocation(allocation)) return false;
      const previousIntensity = intensitiesByInterval.get(allocation.interval_index);
      if (previousIntensity !== undefined && previousIntensity !== allocation.carbon_intensity_value) return false;
      intensitiesByInterval.set(allocation.interval_index, allocation.carbon_intensity_value);
    }
  }
  return true;
}

function isForecastProvenance(
  value: unknown,
  scenario: ForecastScenario,
  expectedRegion?: Pick<CarbonIntensityResponse, 'region_id' | 'region_name'>,
): boolean {
  if (!isRecord(value)) return false;
  const forecastRetrievedAt = strictUtcEpoch(value.forecast_retrieved_at);
  const decisionTime = strictUtcEpoch(value.decision_time);
  const predictionStart = strictUtcEpoch(value.prediction_horizon_start);
  const predictionEnd = strictUtcEpoch(value.prediction_horizon_end);
  const schedulingStart = strictUtcEpoch(value.scheduling_horizon_start);
  const schedulingEnd = strictUtcEpoch(value.scheduling_horizon_end);
  const scenarioStart = strictUtcEpoch(scenario.start_at);
  const expectedAgeSeconds = forecastRetrievedAt === null || decisionTime === null
    ? Number.NaN
    : (decisionTime - forecastRetrievedAt) / 1000;
  const regionId = value.region_id;
  return value.source_classification === 'regional_forecast'
    && value.source_api === 'neso-carbon-intensity-api'
    && value.measurement_type === 'forecast'
    && value.intensity_unit === 'gCO2/kWh'
    && isRegionId(regionId)
    && typeof value.region_name === 'string'
    && value.region_name.trim().length > 0
    && (expectedRegion === undefined
      || (regionId === expectedRegion.region_id && value.region_name === expectedRegion.region_name))
    && isFiniteNonNegative(value.forecast_age_seconds)
    && value.forecast_age_seconds <= MAX_FORECAST_AGE_SECONDS
    && expectedAgeSeconds <= MAX_FORECAST_AGE_SECONDS
    && value.interval_count === 48
    && forecastRetrievedAt !== null
    && decisionTime !== null
    && forecastRetrievedAt <= decisionTime
    && scenarioStart !== null
    && decisionTime <= (scenarioStart ?? Number.NEGATIVE_INFINITY)
    && approximatelyEqualWithAbsoluteTolerance(
      value.forecast_age_seconds,
      expectedAgeSeconds,
      TIMESTAMP_PRECISION_TOLERANCE_SECONDS,
    )
    && predictionStart !== null
    && schedulingStart !== null
    && predictionEnd === predictionStart + 48 * 30 * 60_000
    && isUtcHalfHour(predictionStart)
    && schedulingStart === scenarioStart
    && schedulingEnd === schedulingStart + scenario.intervals * 30 * 60_000
    && predictionStart <= schedulingStart
    && schedulingEnd <= predictionEnd
    && scenario.interval_minutes === 30
    && Number.isInteger(scenario.intervals)
    && scenario.intervals >= 1
    && scenario.intervals <= 48
    && isUtcHalfHour(schedulingStart)
    && isNesoForecastUrl(value.source_url, regionId, predictionStart);
}

function isForecastSchedule(
  value: unknown,
  scenario: ForecastScenario,
  expectedStrategy?: string,
): boolean {
  const constraintChecks = isRecord(value) ? value.constraint_checks : null;
  if (!isRecord(value)
    || (value.strategy !== 'immediate_charge_in_vehicle_id_order'
      && value.strategy !== 'carbon_aware_edf_then_bounded_carbon_shift_v1')
    || (expectedStrategy !== undefined && value.strategy !== expectedStrategy)
    || typeof value.feasible !== 'boolean'
    || value.feasibility_classification !== (value.feasible
      ? 'complete_schedule_demonstrated'
      : 'complete_schedule_not_demonstrated')
    || !isFiniteNonNegative(value.peak_depot_power_kw)
    || !isFiniteNonNegative(value.total_delivered_energy_kwh)
    || !isFiniteNonNegative(value.total_required_energy_kwh)
    || !isFiniteNonNegative(value.carbon_intensity_weighted_energy_proxy)
    || value.carbon_proxy_unit !== 'gCO2'
    || !Array.isArray(value.interval_charging_kw)
    || value.interval_charging_kw.length !== scenario.intervals
    || !value.interval_charging_kw.every(isFiniteNonNegative)
    || !Array.isArray(value.allocations)
    || !value.allocations.every(isForecastAllocation)
    || !Array.isArray(value.aggregate_depot_load)
    || value.aggregate_depot_load.length !== scenario.intervals
    || !value.aggregate_depot_load.every(isForecastLoad)
    || !isEnergyMap(value.delivered_energy_kwh)
    || !isEnergyMap(value.unmet_energy_kwh)
    || !hasConstraintChecks(constraintChecks)
    || !isBindingConstraints(value.binding_operational_constraints)
    || !isRecord(value.vehicle_results)) return false;
  const allocationProxy = forecastProxyFromAllocations(value.allocations);
  if (allocationProxy === null
    || !Object.values(constraintChecks).every((passed) => passed === true)
    || !approximatelyEqual(value.carbon_intensity_weighted_energy_proxy, allocationProxy)
    || !isScheduleEnergyAndLoadConsistent(value, scenario)) return false;
  return value.aggregate_depot_load.every((row: unknown, index: number) => (
    isRecord(row)
    && row.interval_index === index
    && strictUtcEpoch(row.interval_start) === Date.parse(scenario.start_at) + index * 30 * 60_000
    && strictUtcEpoch(row.interval_end) === Date.parse(scenario.start_at) + (index + 1) * 30 * 60_000
  ));
}

function isCompleteForecastSchedule(value: unknown, scenario: ForecastScenario): boolean {
  if (!isRecord(value) || !isForecastSchedule(value, scenario) || value.feasible !== true
    || value.feasibility_classification !== 'complete_schedule_demonstrated') return false;

  const requiredEnergy = compensatedSum(scenario.vehicles.map((vehicle) => vehicle.required_energy_kwh));
  const constraintChecks = value.constraint_checks;
  const deliveredEnergy = value.delivered_energy_kwh;
  const unmetEnergy = value.unmet_energy_kwh;
  const vehicleResults = value.vehicle_results;
  if (!energyMatches(value.total_required_energy_kwh, requiredEnergy)
    || !energyMatches(value.total_delivered_energy_kwh, requiredEnergy)
    || !isRecord(constraintChecks)
    || !REQUIRED_CONSTRAINT_CHECKS.every((key) => constraintChecks[key] === true)
    || !isRecord(deliveredEnergy)
    || !isRecord(unmetEnergy)
    || !isRecord(vehicleResults)) return false;

  const vehicleIds = scenario.vehicles.map((vehicle) => vehicle.vehicle_id);
  if ([deliveredEnergy, unmetEnergy, vehicleResults]
    .some((record) => Object.keys(record).length !== vehicleIds.length)) return false;

  return scenario.vehicles.every((vehicle) => {
    const vehicleResult = vehicleResults[vehicle.vehicle_id];
    return isRecord(vehicleResult)
      && energyMatches(deliveredEnergy[vehicle.vehicle_id], vehicle.required_energy_kwh)
      && unmetEnergy[vehicle.vehicle_id] === 0
      && energyMatches(vehicleResult.required_energy_kwh, vehicle.required_energy_kwh)
      && energyMatches(vehicleResult.delivered_energy_kwh, vehicle.required_energy_kwh)
      && vehicleResult.unmet_energy_kwh === 0;
  });
}

function isScheduleEnergyAndLoadConsistent(value: Record<string, unknown>, scenario: ForecastScenario): boolean {
  const deliveredEnergy = value.delivered_energy_kwh;
  const unmetEnergy = value.unmet_energy_kwh;
  const vehicleResults = value.vehicle_results;
  if (!isRecord(deliveredEnergy)
    || !isRecord(unmetEnergy)
    || !isRecord(vehicleResults)
    || !Array.isArray(value.allocations)
    || !Array.isArray(value.aggregate_depot_load)
    || !Array.isArray(value.interval_charging_kw)
    || Object.keys(deliveredEnergy).length !== scenario.vehicles.length
    || Object.keys(unmetEnergy).length !== scenario.vehicles.length
    || Object.keys(vehicleResults).length !== scenario.vehicles.length) return false;

  const energyByVehicle = new Map<string, number[]>();
  for (const vehicle of scenario.vehicles) energyByVehicle.set(vehicle.vehicle_id, []);
  const energyByInterval = Array.from({ length: scenario.intervals }, () => [] as number[]);
  const allocationsByInterval = Array.from({ length: scenario.intervals }, () => [] as ChargingAllocation[]);
  const intensityByInterval = new Map<number, number>();
  const scenarioStart = Date.parse(scenario.start_at);
  if (!Number.isFinite(scenarioStart)) return false;
  const horizonEnd = scenarioStart + scenario.intervals * scenario.interval_minutes * 60_000;
  const vehiclesById = new Map(scenario.vehicles.map((vehicle) => [vehicle.vehicle_id, vehicle]));
  for (const allocation of value.allocations) {
    if (!isForecastAllocation(allocation) || allocation.interval_index >= scenario.intervals) return false;
    const segmentStart = strictUtcEpoch(allocation.segment_start);
    const segmentEnd = strictUtcEpoch(allocation.segment_end);
    const vehicle = vehiclesById.get(allocation.vehicle_id);
    const availabilityStart = vehicle ? strictUtcEpoch(vehicle.availability_start) : null;
    const availabilityEnd = vehicle ? strictUtcEpoch(vehicle.availability_end) : null;
    if (segmentStart === null || segmentEnd === null || !vehicle
      || availabilityStart === null || availabilityEnd === null) return false;
    const intervalStart = scenarioStart + allocation.interval_index * scenario.interval_minutes * 60_000;
    const intervalEnd = intervalStart + scenario.interval_minutes * 60_000;
    const durationHours = (segmentEnd - segmentStart) / 3_600_000;
    if (segmentStart < intervalStart || segmentEnd > intervalEnd
      || segmentStart < scenarioStart || segmentEnd > horizonEnd
      || segmentStart < availabilityStart || segmentEnd > availabilityEnd
      || !approximatelyEqual(allocation.charging_power_kw, allocation.energy_kwh / durationHours)) return false;
    const intervalIntensity = intensityByInterval.get(allocation.interval_index);
    if (intervalIntensity !== undefined && intervalIntensity !== allocation.carbon_intensity_value) return false;
    intensityByInterval.set(allocation.interval_index, allocation.carbon_intensity_value);
    const vehicleEnergy = energyByVehicle.get(allocation.vehicle_id);
    const intervalEnergy = energyByInterval[allocation.interval_index];
    const intervalAllocations = allocationsByInterval[allocation.interval_index];
    if (!vehicleEnergy || !intervalEnergy || !intervalAllocations) return false;
    vehicleEnergy.push(allocation.energy_kwh);
    intervalEnergy.push(allocation.energy_kwh);
    intervalAllocations.push(allocation);
  }

  const vehicleTotals: number[] = [];
  for (const vehicle of scenario.vehicles) {
    const allocatedEnergy = compensatedSum(energyByVehicle.get(vehicle.vehicle_id) ?? []);
    const reportedDelivered = deliveredEnergy[vehicle.vehicle_id];
    const reportedUnmet = unmetEnergy[vehicle.vehicle_id];
    const vehicleResult = vehicleResults[vehicle.vehicle_id];
    const expectedUnmet = requestIsSatisfied(vehicle.required_energy_kwh, allocatedEnergy)
      ? 0
      : Math.max(vehicle.required_energy_kwh - allocatedEnergy, 0);
    if (!energyMatches(reportedDelivered, allocatedEnergy)
      || !energyMatches(reportedUnmet, expectedUnmet)
      || !energyMatches(reportedDelivered + reportedUnmet, vehicle.required_energy_kwh)
      || !isRecord(vehicleResult)
      || !energyMatches(vehicleResult.required_energy_kwh, vehicle.required_energy_kwh)
      || !energyMatches(vehicleResult.delivered_energy_kwh, reportedDelivered)
      || !energyMatches(vehicleResult.unmet_energy_kwh, reportedUnmet)) return false;
    vehicleTotals.push(allocatedEnergy);
  }
  const totalRequiredEnergy = compensatedSum(scenario.vehicles.map((vehicle) => vehicle.required_energy_kwh));
  if (!energyMatches(value.total_required_energy_kwh, totalRequiredEnergy)
    || !energyMatches(value.total_delivered_energy_kwh, compensatedSum(vehicleTotals))) return false;

  const intervalPeaks: number[] = [];
  for (let index = 0; index < scenario.intervals; index += 1) {
    const allocatedEnergy = compensatedSum(energyByInterval[index] ?? []);
    const load = value.aggregate_depot_load[index];
    const intervalPower = allocatedEnergy / (scenario.interval_minutes / 60);
    const reportedIntervalPower = value.interval_charging_kw[index];
    const concurrentPeak = maximumConcurrentAllocationPower(allocationsByInterval[index] ?? []);
    if (!isRecord(load)
      || concurrentPeak === null
      || !energyMatches(load.delivered_energy_kwh, allocatedEnergy)
      || !isFiniteNonNegative(load.average_power_kw)
      || !approximatelyEqual(load.average_power_kw, intervalPower)
      || !isFiniteNonNegative(reportedIntervalPower)
      || !approximatelyEqual(reportedIntervalPower, intervalPower)
      || !isFiniteNonNegative(load.peak_power_kw)
      || !approximatelyEqual(load.peak_power_kw, concurrentPeak.depotPowerKw)
      || exceedsPowerLimit(concurrentPeak.depotPowerKw, scenario.depot_power_kw)) return false;
    for (const [vehicleId, peakPowerKw] of concurrentPeak.vehiclePowerKw) {
      const vehicle = vehiclesById.get(vehicleId);
      if (!vehicle || exceedsPowerLimit(peakPowerKw, vehicle.charging_power_kw)) return false;
    }
    intervalPeaks.push(concurrentPeak.depotPowerKw);
  }

  return isFiniteNonNegative(value.peak_depot_power_kw)
    && approximatelyEqual(value.peak_depot_power_kw, Math.max(0, ...intervalPeaks))
    && scenario.vehicles.every((vehicle) => requestIsSatisfied(
      vehicle.required_energy_kwh,
      deliveredEnergy[vehicle.vehicle_id] as number,
    )) === (value.feasible === true);
}

function isConsistentCarbonProxyComparison(value: Record<string, unknown>): boolean {
  if (!isRecord(value.immediate_baseline) || !isRecord(value.forecast_aware)) return false;
  const baselineProxy = value.immediate_baseline.carbon_intensity_weighted_energy_proxy;
  const forecastProxy = value.forecast_aware.carbon_intensity_weighted_energy_proxy;
  if (!isFiniteNonNegative(baselineProxy) || !isFiniteNonNegative(forecastProxy)) return false;

  const expectedReduction = baselineProxy - forecastProxy;
  if (!isFiniteNumber(value.absolute_carbon_proxy_reduction)
    || !approximatelyEqual(value.absolute_carbon_proxy_reduction, expectedReduction)) return false;

  const percentageReason = value.percentage_difference_unavailable_reason;
  if (baselineProxy === 0) {
    return value.percentage_carbon_proxy_reduction === null
      && typeof percentageReason === 'string'
      && percentageReason.trim().length > 0;
  }

  const expectedPercentage = (expectedReduction / baselineProxy) * 100;
  if (!Number.isFinite(expectedPercentage)) {
    return value.percentage_carbon_proxy_reduction === null
      && typeof percentageReason === 'string'
      && percentageReason.trim().length > 0;
  }
  return isFiniteNumber(value.percentage_carbon_proxy_reduction)
    && approximatelyEqual(value.percentage_carbon_proxy_reduction, expectedPercentage)
    && (percentageReason === undefined || percentageReason === null);
}

function forecastProxyFromAllocations(allocations: unknown[]): number | null {
  const segments = new Map<string, { intensity: number; energies: number[] }>();
  for (const value of allocations) {
    if (!isForecastAllocation(value)) return null;
    const key = `${value.interval_index}\u0000${value.segment_start}\u0000${value.segment_end}`;
    const existing = segments.get(key);
    if (existing) {
      if (existing.intensity !== value.carbon_intensity_value) return null;
      existing.energies.push(value.energy_kwh);
    } else {
      segments.set(key, {
        intensity: value.carbon_intensity_value,
        energies: [value.energy_kwh],
      });
    }
  }

  const segmentProxies: number[] = [];
  for (const segment of segments.values()) {
    const proxy = compensatedSum(segment.energies) * segment.intensity;
    if (!Number.isFinite(proxy)) return null;
    segmentProxies.push(proxy);
  }
  const totalProxy = compensatedSum(segmentProxies);
  return Number.isFinite(totalProxy) && totalProxy >= 0 ? totalProxy : null;
}

function compensatedSum(values: readonly number[]): number {
  let sum = 0;
  let compensation = 0;
  for (const value of values) {
    const next = sum + value;
    compensation += Math.abs(sum) >= Math.abs(value)
      ? (sum - next) + value
      : (value - next) + sum;
    sum = next;
  }
  return sum + compensation;
}

function ulp(value: number): number {
  const magnitude = Math.abs(value);
  if (magnitude === 0 || magnitude < 2 ** -1022) return Number.MIN_VALUE;
  return 2 ** (Math.floor(Math.log2(magnitude)) - 52);
}

function approximatelyEqual(actual: number, expected: number): boolean {
  if (!Number.isFinite(actual) || !Number.isFinite(expected)) return false;
  const tolerance = FLOATING_POINT_ULP_TOLERANCE * Math.max(ulp(actual), ulp(expected));
  return Math.abs(actual - expected) <= tolerance;
}

function approximatelyEqualWithAbsoluteTolerance(
  actual: number,
  expected: number,
  absoluteTolerance: number,
): boolean {
  if (!Number.isFinite(actual) || !Number.isFinite(expected)) return false;
  const tolerance = absoluteTolerance
    + FLOATING_POINT_ULP_TOLERANCE * Math.max(ulp(actual), ulp(expected));
  return Math.abs(actual - expected) <= tolerance;
}

function exceedsPowerLimit(actual: number, limit: number): boolean {
  if (actual <= limit) return false;
  const ulpTolerance = FLOATING_POINT_ULP_TOLERANCE * Math.max(ulp(actual), ulp(limit));
  const relativeTolerance = limit * POWER_LIMIT_RELATIVE_TOLERANCE;
  return actual - limit > Math.min(ulpTolerance, relativeTolerance);
}

function requestIsSatisfied(requiredEnergyKwh: number, deliveredEnergyKwh: number): boolean {
  const deficit = requiredEnergyKwh - deliveredEnergyKwh;
  const tolerance = Math.min(
    2 * ulp(requiredEnergyKwh),
    requiredEnergyKwh * 1e-12,
  );
  return deficit <= tolerance;
}

function isUtcHalfHour(timestamp: number | null): timestamp is number {
  if (timestamp === null) return false;
  const date = new Date(timestamp);
  return Number.isInteger(timestamp)
    && date.getUTCMinutes() % 30 === 0
    && date.getUTCSeconds() === 0
    && date.getUTCMilliseconds() === 0;
}

interface ConcurrentAllocationPower {
  depotPowerKw: number;
  vehiclePowerKw: Map<string, number>;
}

function maximumConcurrentAllocationPower(
  allocations: readonly ChargingAllocation[],
): ConcurrentAllocationPower | null {
  type Event = { starts: number[]; ends: number[] };
  const events = new Map<number, Event>();
  const ensureEvent = (timestamp: number): Event => {
    const existing = events.get(timestamp);
    if (existing) return existing;
    const created = { starts: [], ends: [] };
    events.set(timestamp, created);
    return created;
  };

  allocations.forEach((allocation, index) => {
    const start = strictUtcEpoch(allocation.segment_start);
    const end = strictUtcEpoch(allocation.segment_end);
    if (start === null || end === null || end <= start) return;
    ensureEvent(start).starts.push(index);
    ensureEvent(end).ends.push(index);
  });
  if (events.size === 0 && allocations.length > 0) return null;

  const timestamps = [...events.keys()].sort((left, right) => left - right);
  const active = new Map<number, ChargingAllocation>();
  const maxVehiclePowerKw = new Map<string, number>();
  let maxDepotPowerKw = 0;
  for (let index = 0; index < timestamps.length - 1; index += 1) {
    const timestamp = timestamps[index];
    const nextTimestamp = timestamps[index + 1];
    if (timestamp === undefined || nextTimestamp === undefined) return null;
    const event = events.get(timestamp);
    if (!event) return null;
    for (const allocationIndex of event.ends) active.delete(allocationIndex);
    for (const allocationIndex of event.starts) {
      const allocation = allocations[allocationIndex];
      if (!allocation) return null;
      active.set(allocationIndex, allocation);
    }
    if (nextTimestamp <= timestamp) continue;

    const powerByVehicle = new Map<string, number[]>();
    for (const allocation of active.values()) {
      const powers = powerByVehicle.get(allocation.vehicle_id) ?? [];
      powers.push(allocation.charging_power_kw);
      powerByVehicle.set(allocation.vehicle_id, powers);
    }
    const vehicleTotals = [...powerByVehicle].map(([vehicleId, powers]) => {
      const total = compensatedSum(powers);
      maxVehiclePowerKw.set(vehicleId, Math.max(maxVehiclePowerKw.get(vehicleId) ?? 0, total));
      return total;
    });
    maxDepotPowerKw = Math.max(maxDepotPowerKw, compensatedSum(vehicleTotals));
  }
  return { depotPowerKw: maxDepotPowerKw, vehiclePowerKw: maxVehiclePowerKw };
}

function energyMatches(value: unknown, expected: number): value is number {
  return isFiniteNonNegative(value) && approximatelyEqual(value, expected);
}

function isForecastAllocation(value: unknown): value is ChargingAllocation {
  if (!isRecord(value)) return false;
  const start = strictUtcEpoch(value.segment_start);
  const end = strictUtcEpoch(value.segment_end);
  return typeof value.vehicle_id === 'string'
    && value.vehicle_id.length > 0
    && isNonNegativeInteger(value.interval_index)
    && start !== null
    && end !== null
    && end > start
    && isFiniteNonNegative(value.energy_kwh)
    && isFiniteNonNegative(value.charging_power_kw)
    && isFiniteNonNegative(value.carbon_intensity_value)
    && value.intensity_unit === 'gCO2/kWh';
}

function isForecastLoad(value: unknown): boolean {
  return isRecord(value)
    && isNonNegativeInteger(value.interval_index)
    && strictUtcEpoch(value.interval_start) !== null
    && strictUtcEpoch(value.interval_end) !== null
    && isFiniteNonNegative(value.average_power_kw)
    && isFiniteNonNegative(value.peak_power_kw)
    && isFiniteNonNegative(value.delivered_energy_kwh);
}

function isBindingConstraints(value: unknown): boolean {
  return isRecord(value)
    && Array.isArray(value.depot_capacity_interval_indexes)
    && value.depot_capacity_interval_indexes.every(isNonNegativeInteger)
    && isNumberArrayRecord(value.availability_window_end_used_segments)
    && isNumberArrayRecord(value.vehicle_power_limit_segments);
}

function isNumberArrayRecord(value: unknown): boolean {
  return isRecord(value) && Object.values(value).every((item) => (
    Array.isArray(item) && item.every(isNonNegativeInteger)
  ));
}

function isEnergyMap(value: unknown): boolean {
  return isRecord(value) && Object.values(value).every(isFiniteNonNegative);
}

function hasConstraintChecks(value: unknown): value is Record<string, unknown> {
  return isRecord(value)
    && Object.values(value).every((item) => typeof item === 'boolean')
    && REQUIRED_CONSTRAINT_CHECKS.every((key) => typeof value[key] === 'boolean');
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isRegionId(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 1 && value <= 17;
}

function isNonNegativeInteger(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0;
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function isFiniteNonNegative(value: unknown): value is number {
  return isFiniteNumber(value) && value >= 0;
}

function strictUtcEpoch(value: unknown): number | null {
  if (typeof value !== 'string') return null;
  const match = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,6}))?Z$/.exec(value);
  if (!match) return null;
  const timestamp = Date.parse(`${match[1]}Z`);
  if (!Number.isFinite(timestamp)) return null;
  const fraction = match[2] ?? '';
  const milliseconds = Number(fraction.slice(0, 3).padEnd(3, '0'));
  const subMillisecond = Number(fraction.slice(3).padEnd(3, '0')) / 1000;
  return timestamp + milliseconds + subMillisecond;
}

function isZonedTimestamp(value: unknown): value is string {
  return typeof value === 'string'
    && /(?:Z|[+-]\d{2}:\d{2})$/.test(value)
    && Number.isFinite(Date.parse(value));
}

function isNesoForecastUrl(value: unknown, regionId: number, requestedFrom: number): boolean {
  if (typeof value !== 'string') return false;
  try {
    const url = new URL(value);
    const timestamp = new Date(requestedFrom).toISOString().slice(0, 16) + 'Z';
    return url.protocol === 'https:'
      && url.hostname === 'api.carbonintensity.org.uk'
      && url.port === ''
      && url.username === ''
      && url.password === ''
      && url.pathname === `/regional/intensity/${timestamp}/fw24h/regionid/${regionId}`
      && url.search === ''
      && url.hash === '';
  } catch {
    return false;
  }
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
