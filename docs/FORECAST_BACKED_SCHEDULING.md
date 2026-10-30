# Forecast-backed EV-depot scheduling core

## Scope

Phase B2a adds a domain-only scheduling capability on the feature branch. It does not add a public route, change the existing synthetic API, call the NESO service during scheduling, persist results, or connect to chargers or market systems. The current Render deployment is unchanged.

The existing `CarbonAwareDepotScenario`, `CarbonInputProvenance`, and `CarbonAwareSimulationResult` remain synthetic-only contracts. Forecast inputs use the separate `ForecastBackedDepotScenario` and results use `ForecastBackedSchedulingResult`; regional forecasts are never relabelled as synthetic observations.

## Domain flow

`ForecastBackedDepotScenario` contains:

- a validated `DepotScenario` with vehicle requirements, UTC availability windows, charger power and depot capacity;
- a required `ScenarioRegionMapping` that names the scenario ID and NESO region ID explicitly;
- an optional validated `CarbonIntensityForecastTimeline` (absence is represented as unavailable, not replaced);
- the scheduling `decision_time`, a bounded freshness limit (at most 3,600 seconds), and the declared intensity unit.

`ForecastBackedSchedulingResult` reports the input provenance, immediate baseline, forecast-aware schedule, energy delivered and unmet per vehicle, operational constraint checks, carbon proxies, comparison eligibility, and an explicit unavailable explanation. Its provenance identifies `regional_forecast`, `forecast`, the source API and URL, region, retrieval time, forecast and scheduling horizons, decision time, age, and `gCO2/kWh` unit. The result is explicitly advisory-only, forecast-based, non-measured, and makes no real-world emissions-reduction claim.

The shared scheduler consumes a small, provenance-neutral interval-value type only after the forecast adapter validates the source. This reuses the existing baseline and bounded carbon-shift algorithm without manufacturing a `CarbonAwareDepotScenario` or weakening synthetic provenance checks. The synthetic scheduler still maps its existing validated profile into that same internal algorithm input; its public result contract and expected behaviour remain unchanged.

## Input validation and point-in-time rules

The forecast adapter fails closed unless all of the following hold:

- the scenario-to-region mapping identifies the same scenario and its NESO region ID matches the timeline region (1–17);
- the timeline came from the expected NESO forecast source, has a non-empty region name, and carries the canonical official request URL;
- the declared unit and the timeline unit are exactly `gCO2/kWh`; no conversion is attempted;
- the timeline has 48 forecast-only intervals, each exactly 30 minutes, UTC half-hour aligned, ordered, contiguous, non-overlapping, finite and non-negative, with complete 24-hour coverage;
- the depot horizon is half-hourly, starts on a UTC half-hour boundary, and every requested scheduling interval is covered by the timeline;
- the decision time is timezone-aware UTC and no later than the scheduling horizon start;
- the forecast retrieval time is no later than the decision time and no more than the configured freshness limit before it;
- the depot remains within the existing bounded heuristic envelope (at most 48 intervals, 50 vehicles, and 1,000,000,000 for each bounded energy/power/carbon numeric value).

`retrieved_at` is the availability evidence used by this adapter; the upstream forecast does not expose an independently verifiable original publication/issue time. A retrospectively acquired forecast therefore cannot be used for a historical decision whose origin precedes its recorded retrieval time. Historical forecast archives without issue-time evidence are not as-of inputs for this capability.

Missing, stale, malformed, unit-inconsistent, region-inconsistent or future-retrieved forecasts return `status: "unavailable"`, a typed `unavailable_reason`, and an explanation that no comparison was produced. The adapter does not run either strategy or calculate a carbon proxy when forecast input is unavailable. A structurally invalid request may fail Pydantic validation before scheduling.

## Scheduling and comparison semantics

The immediate baseline and forecast-aware strategy use the same validated `DepotScenario`, service requirements, and one forecast profile. Both reports use that profile to calculate `carbon_intensity_weighted_energy_proxy` (gCO2/kWh × delivered kWh, reported as gCO2). Constraint checks and delivered/unmet energy are reported separately for each strategy.

A comparison is eligible only when both strategies complete every vehicle requirement. Otherwise the result contains an explicit comparison-unavailable reason and no reduction value; an incomplete heuristic result is not proof that the scenario is infeasible. The forecast-aware strategy is the existing deterministic EDF seed plus at most two bounded carbon-shift passes. It is a heuristic, not a globally optimal solution. A proxy difference is not a measured emissions outcome or a real-world reduction claim.

## Example integration boundary

A future server-side integration may obtain the existing B1 timeline using `NesoCarbonIntensityClient.fetch_forecast_timeline(...)`, construct the explicit mapping and forecast-backed request, then call `schedule_forecast_backed(...)`. It must record the decision time at or after successful retrieval and choose a scheduling start at or after that decision time, aligned to the next eligible UTC half-hour boundary. If the returned timeline does not cover the entire requested horizon, scheduling remains unavailable.

Do not expose this request as a public route in B2a. Phase B2b owns any API route, response-size/rate-limit policy, error-to-HTTP mapping, request-time forecast retrieval, and end-to-end route tests. B2b must keep user-provided region mapping within a trusted server-side boundary and must preserve the no-future-information rule for replay requests.

## Verification boundary

Automated unit tests build deterministic forecast fixtures through the existing parser and never call the live NESO API. PostgreSQL persistence and migrations are not part of this scheduling core. Passing these tests verifies domain behavior only; it does not establish forecast accuracy, upstream availability, deployment readiness, charger safety, or production operation.
