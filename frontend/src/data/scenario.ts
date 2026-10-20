import exampleScenario from '../../../examples/carbon_aware_ev_depot_demo.json';
import type { CarbonIntensityInterval, Scenario } from '../api/client';

export function createDemoScenario(): Scenario {
  return structuredClone(exampleScenario) as Scenario;
}

export function toUtcInput(value: string): string {
  return value.slice(0, 16);
}

export function fromUtcInput(value: string): string {
  if (!value) return '';
  return `${value}:00Z`;
}

export function rebuildCarbonProfile(
  scenario: Scenario,
  startAt: string,
  intervalMinutes: number,
  intervalCount: number,
): CarbonIntensityInterval[] {
  const prior = scenario.carbon_intensity_profile;
  const template = prior[0];
  if (!template || intervalCount < 1 || intervalCount > 96 || intervalMinutes < 1) return [];

  const startMs = Date.parse(startAt);
  if (!Number.isFinite(startMs)) return [];
  return Array.from({ length: intervalCount }, (_, index) => {
    const intervalStart = new Date(startMs + index * intervalMinutes * 60_000).toISOString();
    const intervalEnd = new Date(startMs + (index + 1) * intervalMinutes * 60_000).toISOString();
    const source = prior[index] ?? prior.at(-1) ?? template;
    return {
      ...source,
      interval_start: intervalStart,
      interval_end: intervalEnd,
      intensity_value: source.intensity_value,
    };
  });
}

export function scenarioSignature(scenario: Scenario): string {
  return JSON.stringify(scenario);
}
