# Operator dashboard: local guide

## Purpose and safety boundary

The Operator Dashboard is a locally runnable interface for editing and evaluating **synthetic EV-depot scenarios** and, in the v0.2.0 Phase A candidate, inspecting a separate live NESO carbon-intensity reading. The synthetic scenario is submitted to `POST /api/v1/simulations/ev-depot/evaluate`; FastAPI validates the request and calls the existing Python scheduler. The browser does not reproduce scheduling or carbon-proxy calculations. The live section uses its own read-only `GET /api/v1/energy/carbon-intensity` request and never sends live values into the scheduler.

The synthetic simulation is advisory and price-free. It has no database writes, live NESO inputs, charger or hardware connection, electricity trading, real-world emissions claim, or global-optimality guarantee. `complete_schedule_not_demonstrated` means that the selected heuristic did not produce a complete schedule; it is not a proof of infeasibility. The new live panel is display-only: its separate read-only endpoint is not connected to either scheduler strategy.

The published v0.1.0 public demo remains simulation-only and exposes only its existing synthetic evaluation route. The v0.2.0 Phase A branch adds the read-only NESO endpoint to the application candidate, but it has not been deployed. In the Docker deployment, production-built Vite assets and FastAPI are served from one origin; the demo has no user authentication and is not a production operations service.

## Requirements and startup

- Python 3.12 or newer with the repository runtime requirements installed.
- Node.js 22.12 or newer and npm.
- No database, cloud account, or external UI service.
- No credentials are needed. Synthetic scheduling works offline; the live panel requires outbound HTTPS from FastAPI to the public NESO API and degrades to an explicit unavailable state when that request fails.

From the repository root, start the API:

```bash
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

In a second terminal:

```bash
cd frontend
npm ci
npm run dev
```

Open <http://127.0.0.1:5173>. Vite serves the frontend and proxies `/api` and `/health` to the loopback API at port 8000. The browser sees a same-origin request; FastAPI does not enable wildcard CORS. Both processes bind to loopback for local use.

## Operator workflow

1. Select **Load synthetic example**. The initial example is also loaded on first page view and is labelled simulation-only.
2. Edit the scenario identifier, UTC start, interval length/count, depot limit, vehicle windows and energy/power limits, or per-interval synthetic carbon intensity.
3. Add/remove vehicles, or import a local JSON file conforming to the EV-depot scenario contract. Client-side import limits files to 256 KiB; authoritative scenario validation remains in FastAPI.
4. Select **Run simulation**. The dashboard submits the current scenario to the real API. Editing during an in-flight request cancels/invalidates it; stale responses cannot replace newer input state.
5. Review the **Immediate Baseline** and **Carbon-Aware Heuristic** schedules, allocation segments, interval-average and peak depot power, required/delivered/unmet energy, constraint checks and comparison eligibility.
6. Select **Export JSON** to download the current scenario inputs. Exports contain no result data or credentials.

The **Live carbon intensity** panel loads independently when the page opens and refreshes every 60 seconds; use its **Refresh** control for a manual request. It labels region, intensity and raw reported units, actual/forecast source, observed interval, retrieval time, and age/freshness. A stale completed forecast or a failed refresh is explicitly marked; when no valid reading exists, the panel shows unavailable without substituting a value. This read-only section does not alter synthetic form values or either scheduler strategy.

After any edit, prior results are hidden until that exact input version has been evaluated. API validation errors are rendered next to the associated fields when FastAPI reports a field location; server-side failures are presented without exposing internal exception details.

## Reading the results

- Vehicle allocation bars represent actual response `segment_start`/`segment_end` and per-vehicle allocated energy. Their tables provide exact segment times, energy, average power, and intensity.
- Depot charts distinguish interval-average power, segment peak power, and the depot capacity reference. Average power is not presented as instantaneous power.
- Carbon-intensity profile chart and table use the edited synthetic inputs; they are not NESO observations or forecasts.
- Per-strategy proxies retain the input unit (`gCO2` or `gCO2e`). The signed comparison is **Immediate Baseline proxy − Carbon-Aware Heuristic proxy**: positive, zero, and negative values are all retained.
- A comparison is eligible only if both schedules fulfil the same complete vehicle energy requirements. When it is not eligible, the reason is shown; a lower proxy from reduced delivery is never described as a benefit.
- Constraints and status use text as well as colour. Expand the data tables for numerical alternatives to the charts.
- Energy, power, and proxy values use up to six decimal places; very small or very large values use scientific notation, and true zero remains `0`. A non-zero shortfall is therefore not rounded into an exact zero.
- Schedule charts choose a responsive set of at most five UTC time ticks. Their focusable, scrollable viewport keeps large fleets manageable; accessible interval tables retain every interval's UTC bounds, and allocation tables retain every returned segment and its timestamps.

## API contract and OpenAPI types

`POST /api/v1/simulations/ev-depot/evaluate` accepts the existing `CarbonAwareDepotScenario` fields and returns `CarbonAwareSimulationResult`. The HTTP input adapter accepts ISO-8601 timestamp strings and delegates all other domain constraints to the existing strict Pydantic scenario model. The response includes the existing strategies, detailed segment allocations, load and constraint results, provenance, and comparison fields.

The API enforces a maximum of 96 intervals and 50 vehicles, and retains the existing synthetic-only provenance rules. Invalid inputs return FastAPI's structured HTTP 422 validation response. The endpoint is synchronous, bounded, stateless, and does not fetch NESO data or access persistence.

The independent `GET /api/v1/energy/carbon-intensity` contract returns one validated reading for the configured `NESO_REGION_ID` (default 13), including raw NESO `gCO2/kWh` units, actual/forecast provenance, observed interval, retrieval timestamp, age, freshness and source URL. It takes no request body or caller-controlled region/URL, does not persist data, and has no connection to the simulation request path. It reuses the existing client, with `NESO_API_BASE_URL` restricted to `https://api.carbonintensity.org.uk`, `NESO_TIMEOUT_SECONDS` bounded to 30 seconds, a maximum 64 KiB upstream response and an at-most-30-second freshness-aware process cache. The public API and browser client mark unavailable/error states explicitly; stale/error responses are not publicly cached.

TypeScript schema definitions are generated from the FastAPI OpenAPI document:

```bash
cd frontend
npm run generate:api
```

This imports the local Python application to generate an ignored intermediate OpenAPI JSON file, then runs the locked `openapi-typescript` generator into `src/api/schema.d.ts`. The schema file is committed so IDEs and tests can use the contract; `npm run build` regenerates it before type-checking.

The default API URL is the same-origin relative path `/api`. `VITE_API_BASE_URL` is an optional prefix for a same-origin configuration. Cross-origin deployment is not configured and must not be enabled by adding permissive CORS defaults.

## Development checks

From `frontend/`:

```bash
npm run lint
npm run typecheck
npm test
npm run build
npm run test:e2e
```

Vitest/Testing Library cover editing, import, example reset, validation feedback, API error handling, result rendering, stale-result prevention, the read-only carbon-intensity client, live freshness/provenance/error states, and separation from synthetic requests. Playwright checks the initial keyboard tab order and visible focus outline, starts the real FastAPI application and Vite, then edits inputs, calls the genuine scheduler endpoint, checks returned proxy values, exports JSON, and checks the mobile viewport for horizontal overflow. The E2E test is not API-mocked and does not substitute for the requested Apple Safari acceptance.

Backend tests:

```bash
python -m pytest -q app/tests/test_ev_depot_simulations.py
```

The full repository test suite also includes database integration tests and requires a disposable PostgreSQL test database as described by the existing test configuration; the operator simulation endpoint itself requires no database.

## Screenshot

Captured from the current dispatch workbench, running Vite frontend, and FastAPI endpoint after changing the synthetic request to 8.5 kWh and the second intensity to 50 gCO2/kWh. The displayed values are the actual simulation response for those inputs. The scenario editor dock, strategy-comparison canvas, and lower per-strategy schedule band are described in the [workbench design and QA record](design/ev-depot-dispatch-workbench.md).

![Current dispatch workbench after a real synthetic simulation](screenshots/ev-depot-workbench-after-1440x900.png)

This image is an existing local Vite/FastAPI synthetic-simulation capture. It predates the v0.2.0 live-data section and does not show the new panel; it is not a capture of the Render demo or Apple Safari acceptance evidence.

## Implemented versus future work

Implemented: scenario form, bounded local JSON import/export, synthetic input provenance, real scheduler API integration, separate read-only NESO client/API integration for one configured region, live panel with provenance/freshness and stale/unavailable states, result tables/charts, validation/error handling, stale-result prevention, responsive styling, automated component/client/regression checks, and a limited simulation-only Docker application candidate serving the built frontend from FastAPI. The v0.2.0 candidate has not been deployed; the published v0.1.0 service remains unchanged.

Not implemented: live NESO-to-scheduler integration, cross-origin deployment, user accounts or authentication, production-grade availability/monitoring/backup operations, live hardware control, electricity trading, cost/tariff evaluation, infeasibility proof beyond existing evaluator semantics, or global schedule optimisation. Neither the public v0.1.0 demo nor this local candidate establishes production readiness.
