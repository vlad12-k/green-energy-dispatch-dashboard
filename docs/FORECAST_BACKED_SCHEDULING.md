# Forecast-backed EV-depot scheduling core

## Scope

Phase B2a adds the domain-only scheduling capability. Phase B2b adds a separate forecast-backed API integration candidate on the feature branch; that candidate is **not deployed** and does not change the current Render service. The endpoint reuses the validated Phase B1 NESO timeline and B2a scheduler without changing the existing synthetic API, persisting results, or connecting to chargers or market systems.

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

## Phase B2b forecast-evaluation API candidate (not deployed)

The separate advisory-only endpoint is `POST /api/v1/simulations/ev-depot/forecast-evaluate`. Phase A is the deployed live NESO display (`GET /api/v1/energy/carbon-intensity`) and remains separate from scheduling. Phase B1 supplies the validated 24-hour forecast timeline; Phase B2a supplies the forecast-backed domain contracts and deterministic scheduler; Phase B2b connects those components in this backend-only candidate. The React dashboard does not call this endpoint, and it is **not deployed**. No Render configuration, release tag, worker, production database, charger, or market system is changed or used.

### Request contract

The JSON body contains a required `scenario` using the existing `DepotScenario` fields and may contain `neso_region_id` only as an optional assertion of the configured region. The request is strict (`extra="forbid"`): clients cannot supply raw forecast values, URLs, retrieval or decision timestamps, provenance, or scheduling results. Scenario timestamps must be ISO-8601 UTC values. The HTTP adapter bounds the scenario to 30-minute intervals, at most 48 intervals, at most 50 vehicles, identifiers no longer than 120 characters, and finite numeric inputs no greater than 1,000,000,000. Each vehicle window must fit within the scenario horizon and its charging limit must respect the depot limit.

If supplied, `neso_region_id` must equal the server-configured `NESO_REGION_ID`; it never chooses geography. The scheduling start must be at or after the next eligible UTC half-hour boundary at request time, and must still be no earlier than the server decision time captured after forecast retrieval. A scenario that violates either time condition receives HTTP 422 before scheduling.

### Trusted forecast and scheduling flow

The endpoint calls the configured `CachedNesoCarbonIntensityReader` directly; it does not make an HTTP request to the application's own B1 route. The existing NESO client enforces the official HTTPS host allowlist, disables redirects, applies bounded timeouts and response size, and validates the response into a `CarbonIntensityForecastTimeline`. The B2a scheduler then revalidates region, official source URL, `gCO2/kWh` units, forecast-only interval provenance, exact contiguous UTC half-hour alignment, freshness and full scheduling-horizon coverage. Cached timelines are checked against each request's complete horizon; a cache hit that no longer covers the scenario fails closed. No synthetic fallback is used.

The result preserves the upstream timeline's server-observed retrieval timestamp. The server captures `decision_time` after retrieval and requires retrieval to be no later than that decision. NESO does not provide independently verified original issue-time evidence here: `retrieved_at` proves when this service obtained the timeline, not when NESO first issued it, and cannot establish a historical as-of forecast.

### Response and failure semantics

- HTTP 200 with `status: "available"` means validated forecast evidence was available and both deterministic strategies ran. This includes schedules where either or both strategies do **not** demonstrate complete service; in that case `comparison_eligible` is false, the response gives a reason, and no reduction value is reported.
- HTTP 503 with `status: "unavailable"` means forecast evidence was missing, stale, invalid, unavailable, or the upstream request failed. The response has no schedules or carbon proxy, includes a typed unavailable reason/detail, and does not fall back to synthetic inputs. Raw upstream bodies, exception strings and credentials are not returned.
- HTTP 422 reports invalid client scenario fields, region mismatch, or a scheduling start that is too early. The route returns sanitised validation details without echoing rejected values.
- HTTP 405 rejects every method except POST and advertises `Allow: POST`.

The response model is `ForecastBackedSchedulingResult`: it retains `scenario_id`, `status`, typed `unavailable_reason`/`unavailable_detail`, `input_provenance`, both strategy results, comparison eligibility/reasons, optional absolute/percentage proxy differences, `carbon_proxy_unit`, and the literal advisory/forecast/measured/real-world-claim flags. An unavailable result has no provenance, schedules, or proxy difference. Available but incomplete schedules keep both strategy diagnostics while comparison values remain `null` and an explicit reason is supplied.

All carbon values are `gCO2` forecast-weighted energy proxies, not measured emissions or verified savings. A baseline-versus-forecast-aware difference is present only when both strategies complete equivalent service requirements. The bounded B2a heuristic remains a heuristic, not a global optimum or proof of infeasibility. Successful and handled error responses use `Cache-Control: no-store`.

### Public-demo limits and verification boundary

In simulation-only mode, the public guard admits only the exact forecast-evaluation POST path (alongside the existing synthetic POST and read-only NESO GET routes). It enforces the configured request-body cap, a separate process-local forecast-evaluation rate bucket (default 5 requests/minute, maximum 60), and the existing shared NESO bucket (default 60 requests/minute) so this endpoint and NESO GETs cannot independently consume more than the shared per-process allowance. Accepted forecast POSTs count against both buckets, including cache hits. Buckets are shared by all visitors within one process, reset on restart and are not coordinated across replicas. This is resource control, **not authentication, per-user authorization, or distributed abuse protection**; the public demo has no authenticated operator identity. Interactive docs and OpenAPI remain disabled in simulation-only mode.

Automated tests use mocked NESO transports and deterministic fixtures only; they make no live NESO requests. The API integration is stateless and does not persist results, run background jobs, or issue physical commands. PostgreSQL is not required by this route. Passing tests verifies local behavior, not forecast accuracy, upstream availability, deployment readiness, charger safety, or production operation.
