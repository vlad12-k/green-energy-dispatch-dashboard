import { describe, expect, it } from 'vitest';
import {
  createDefaultForecastScenario,
  fromUtcDateTimeLocal,
  nextUtcHalfHour,
  toUtcDateTimeLocal,
  validateForecastScenario,
} from './forecastScenario';

describe('forecast scenario timing and bounds', () => {
  it('rounds a timestamp strictly up to the next UTC half-hour', () => {
    expect(nextUtcHalfHour('2026-10-30T12:00:00Z')).toBe('2026-10-30T12:30:00.000Z');
    expect(nextUtcHalfHour('2026-10-30T12:30:00Z')).toBe('2026-10-30T13:00:00.000Z');
  });

  it('creates a valid bounded scenario from a server timestamp with an explicit UTC half-hour', () => {
    const serverNow = '2026-10-30T12:17:00Z';
    const scenario = createDefaultForecastScenario(serverNow);

    expect(scenario.start_at).toBe('2026-10-30T13:30:00.000Z');
    expect(scenario.interval_minutes).toBe(30);
    expect(scenario.intervals).toBeGreaterThan(0);
    expect(scenario.intervals).toBeLessThanOrEqual(48);
    expect(validateForecastScenario(scenario, { serverNow })).toEqual({});
  });

  it('round-trips datetime-local values as UTC rather than the host timezone', () => {
    const value = '2026-10-30T13:30';
    expect(fromUtcDateTimeLocal(value)).toBe('2026-10-30T13:30:00.000Z');
    expect(toUtcDateTimeLocal('2026-10-30T13:30:00.000Z')).toBe(value);
  });

  it('rejects unaligned starts and over-limit horizons without rewriting requested deadlines', () => {
    const serverNow = '2026-10-30T12:00:00Z';
    const valid = createDefaultForecastScenario(serverNow);
    const requestedDeadline = '2026-10-31T14:30:00.000Z';
    const scenario = {
      ...valid,
      start_at: '2026-10-30T13:31:00.000Z',
      intervals: 49,
      vehicles: valid.vehicles.map((vehicle, index) => index === 0
        ? { ...vehicle, availability_end: requestedDeadline }
        : vehicle),
    };

    const errors = validateForecastScenario(scenario, { serverNow });

    expect(errors.start_at).toContain('UTC half-hour');
    expect(errors.intervals).toContain('48 intervals');
    expect(errors['vehicles.0.availability_end']).toContain('never adjusted automatically');
    expect(scenario.vehicles[0]?.availability_end).toBe(requestedDeadline);
  });
});
