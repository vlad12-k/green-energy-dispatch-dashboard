import type { components } from '../api/schema';

export type ForecastScenario = components['schemas']['ForecastBackedDepotScenarioRequest'];
export type ForecastVehicle = ForecastScenario['vehicles'][number];
export type ForecastTimeline = components['schemas']['CarbonIntensityForecastTimelineResponse'];
export type ForecastSchedulingResult = components['schemas']['ForecastBackedSchedulingResult'];

export const FORECAST_INTERVAL_MINUTES = 30;
export const FORECAST_MAX_INTERVALS = 48;
export const FORECAST_MAX_VEHICLES = 50;
export const FORECAST_MAX_NUMERIC_VALUE = 1_000_000_000;
const INTERVAL_MS = FORECAST_INTERVAL_MINUTES * 60_000;
const DEFAULT_LEAD_MS = 60 * 60_000;

function validTime(value: string): number | null {
  if (!/(?:Z|\+00:00)$/.test(value)) return null;
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? timestamp : null;
}

export function nextUtcHalfHour(value: string | Date, leadMs = 0): string {
  const timestamp = typeof value === 'string' ? Date.parse(value) : value.getTime();
  if (!Number.isFinite(timestamp)) throw new RangeError('A valid UTC timestamp is required.');
  const boundary = Math.floor((timestamp + leadMs) / INTERVAL_MS) + 1;
  return new Date(boundary * INTERVAL_MS).toISOString();
}

export function createDefaultForecastScenario(serverNow?: string): ForecastScenario {
  const referenceTime = serverNow && validTime(serverNow) !== null ? serverNow : new Date();
  const startAt = nextUtcHalfHour(referenceTime, DEFAULT_LEAD_MS);
  const horizonEnd = new Date(Date.parse(startAt) + 4 * INTERVAL_MS).toISOString();
  return {
    scenario_id: 'forecast-depot-shift-demo',
    start_at: startAt,
    interval_minutes: FORECAST_INTERVAL_MINUTES,
    intervals: 4,
    depot_power_kw: 20,
    vehicles: [
      {
        vehicle_id: 'van-1',
        availability_start: startAt,
        availability_end: horizonEnd,
        required_energy_kwh: 8.5,
        charging_power_kw: 7,
      },
      {
        vehicle_id: 'van-2',
        availability_start: startAt,
        availability_end: horizonEnd,
        required_energy_kwh: 6,
        charging_power_kw: 7,
      },
    ],
  };
}

export function toUtcDateTimeLocal(value: string): string {
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? new Date(timestamp).toISOString().slice(0, 16) : '';
}

export function fromUtcDateTimeLocal(value: string): string {
  if (!value) return '';
  const parsed = new Date(`${value}:00Z`);
  return Number.isFinite(parsed.getTime()) ? parsed.toISOString() : '';
}

export function forecastScenarioSignature(scenario: ForecastScenario): string {
  return JSON.stringify(scenario);
}

export function validateForecastScenario(
  scenario: ForecastScenario,
  options: { serverNow?: string | null; preview?: ForecastTimeline | null } = {},
): Record<string, string> {
  const errors: Record<string, string> = {};
  const setError = (field: string, message: string) => { errors[field] = message; };
  const start = validTime(scenario.start_at);
  const horizonEnd = start === null
    ? Number.NaN
    : start + scenario.interval_minutes * scenario.intervals * 60_000;

  if (!scenario.scenario_id.trim() || scenario.scenario_id.length > 120) {
    setError('scenario_id', 'Enter an identifier of 1–120 characters.');
  }
  if (start === null) {
    setError('start_at', 'Choose a valid UTC scheduling start.');
  } else {
    const startDate = new Date(start);
    if (startDate.getUTCMinutes() % FORECAST_INTERVAL_MINUTES !== 0
      || startDate.getUTCSeconds() !== 0
      || startDate.getUTCMilliseconds() !== 0) {
      setError('start_at', 'Scheduling start must align to a UTC half-hour.');
    }
    const trustedTime = options.serverNow && validTime(options.serverNow) !== null
      ? Date.parse(options.serverNow)
      : Date.now();
    const minimumSafeStart = Date.parse(nextUtcHalfHour(new Date(trustedTime), FORECAST_INTERVAL_MINUTES * 60_000));
    if (start < minimumSafeStart) {
      setError('start_at', 'Choose a future UTC half-hour with at least one interval of evaluation lead time.');
    }
  }
  if (scenario.interval_minutes !== FORECAST_INTERVAL_MINUTES) {
    setError('interval_minutes', 'Forecast scheduling uses fixed 30-minute intervals.');
  }
  if (!Number.isInteger(scenario.intervals) || scenario.intervals < 1 || scenario.intervals > FORECAST_MAX_INTERVALS) {
    setError('intervals', `Choose between 1 and ${FORECAST_MAX_INTERVALS} intervals.`);
  }
  if (!Number.isFinite(scenario.depot_power_kw)
    || scenario.depot_power_kw <= 0
    || scenario.depot_power_kw > FORECAST_MAX_NUMERIC_VALUE) {
    setError('depot_power_kw', 'Depot power must be greater than 0 and no more than 1,000,000,000 kW.');
  }
  if (scenario.vehicles.length < 1 || scenario.vehicles.length > FORECAST_MAX_VEHICLES) {
    setError('vehicles', `Provide between 1 and ${FORECAST_MAX_VEHICLES} vehicles.`);
  }

  const vehicleIds = new Set<string>();
  scenario.vehicles.forEach((vehicle, index) => {
    const prefix = `vehicles.${index}`;
    const id = vehicle.vehicle_id.trim();
    if (!id || id.length > 120 || vehicleIds.has(id)) {
      setError(`${prefix}.vehicle_id`, 'Vehicle IDs must be unique and contain 1–120 characters.');
    }
    vehicleIds.add(id);
    if (!Number.isFinite(vehicle.required_energy_kwh)
      || vehicle.required_energy_kwh <= 0
      || vehicle.required_energy_kwh > FORECAST_MAX_NUMERIC_VALUE) {
      setError(`${prefix}.required_energy_kwh`, 'Required energy must be greater than 0 and within the API limit.');
    }
    if (!Number.isFinite(vehicle.charging_power_kw)
      || vehicle.charging_power_kw <= 0
      || vehicle.charging_power_kw > FORECAST_MAX_NUMERIC_VALUE) {
      setError(`${prefix}.charging_power_kw`, 'Vehicle power must be greater than 0 and within the API limit.');
    } else if (vehicle.charging_power_kw > scenario.depot_power_kw) {
      setError(`${prefix}.charging_power_kw`, 'Vehicle power cannot exceed the depot power limit.');
    }

    const availabilityStart = validTime(vehicle.availability_start);
    const availabilityEnd = validTime(vehicle.availability_end);
    if (availabilityStart === null) setError(`${prefix}.availability_start`, 'Choose a UTC availability start.');
    if (availabilityEnd === null) setError(`${prefix}.availability_end`, 'Choose a UTC availability deadline.');
    if (availabilityStart !== null && availabilityEnd !== null && availabilityEnd <= availabilityStart) {
      setError(`${prefix}.availability_end`, 'Availability deadline must be after the vehicle start.');
    }
    if (start !== null && Number.isFinite(horizonEnd)) {
      if (availabilityStart !== null && (availabilityStart < start || availabilityStart >= horizonEnd)) {
        setError(`${prefix}.availability_start`, 'Vehicle availability must begin inside the scheduling horizon.');
      }
      if (availabilityEnd !== null && (availabilityEnd > horizonEnd || availabilityEnd <= start)) {
        setError(`${prefix}.availability_end`, 'Deadline must remain inside the scheduling horizon. Change it explicitly; it is never adjusted automatically.');
      }
    }
  });

  const preview = options.preview;
  if (preview && start !== null && Number.isFinite(horizonEnd)) {
    if (start < Date.parse(preview.requested_from)) {
      setError('start_at', 'Scheduling start precedes the retrieved forecast preview horizon.');
    }
    if (horizonEnd > Date.parse(preview.valid_until)) {
      setError('intervals', 'Scenario horizon exceeds the retrieved forecast preview. Reduce intervals or edit the start explicitly.');
    }
  }
  return errors;
}
