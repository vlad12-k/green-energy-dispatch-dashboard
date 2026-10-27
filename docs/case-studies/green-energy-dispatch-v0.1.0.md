# Technical case study: a bounded, synthetic EV-depot dispatch comparison

**Project:** Green Energy Dispatch Dashboard

**Evidence checkpoint:** application source commit `55ae4f274848ac3642e1e0bf804feba7f07b8659`

**Status:** portfolio case study for the v0.1.0 release; publication status is tracked by GitHub, not asserted here.

## Problem and constraints

An EV depot can shift charging within vehicle-availability windows and depot power limits. This prototype makes that trade-off inspectable by comparing an immediate-charging reference with a carbon-aware heuristic on the same explicitly synthetic scenario.

The requirements are intentionally bounded:

- Accept synthetic carbon-intensity profiles only; do not imply a historic or live NESO signal was used by the scheduler.
- Preserve each strategy's per-vehicle energy obligations and clearly withhold equal-service comparison when either schedule does not satisfy them.
- Return enough allocation, power, constraint, and provenance detail for users to inspect how the result was produced.
- Keep the public demonstration stateless, price-free, unauthenticated, and disconnected from hardware and electricity markets.
- Preserve the repository's separate read-only NESO, optional PostgreSQL evidence/replay, and collector capabilities without making them dependencies of the hosted simulation.

## System architecture

The public Docker service serves the production-built React/Vite application and FastAPI from one origin. FastAPI serves the application shell, static assets, and SPA history fallback; the dashboard posts a scenario to `POST /api/v1/simulations/ev-depot/evaluate`. The request is validated, passed to the Python scheduling implementation, and returned with strategy results, provenance, and comparison status. The application does not call NESO or PostgreSQL from this evaluation path.

The repository's read-only NESO adapter, optional PostgreSQL evidence store/replay, and standalone collector form a separate local data path. They are not connected to the public scheduler. See the [architecture overview](../ARCHITECTURE.md), [simulation contract](../EV_DEPOT_SIMULATION.md), and [deployment runbook](../deployment/staging-runbook.md).

## Scheduling strategies and equal-service comparison

### Immediate Baseline

The existing reference implementation processes intervals chronologically and allocates available capacity in vehicle-ID order, reusing capacity as requests complete. It respects vehicle availability, per-vehicle charger power, depot power, and required energy.

### Carbon-Aware Heuristic

`carbon_aware_edf_then_bounded_carbon_shift_v1` first builds a complete service schedule using chronological earliest-departure-first ordering, with vehicle ID as a deterministic tie-breaker. Only after a complete schedule is found, it makes up to two deterministic passes to move energy from higher- to lower-intensity segments when the vehicle is available and both charger and depot capacity permit. Each move preserves that vehicle's delivered energy.

This is a bounded heuristic, not a global optimiser. If it cannot demonstrate a complete schedule, the API reports that condition without claiming the scenario is infeasible.

### Comparison rule

For each strategy, the proxy is the sum of allocated segment energy multiplied by the supplied interval intensity. The signed difference is **Immediate Baseline proxy minus Carbon-Aware Heuristic proxy**. Comparison is eligible only when both schedules satisfy every vehicle's energy requirement within the strict comparison tolerance. A lower proxy from incomplete service is never described as a benefit. Proxy outputs retain the input unit (`gCO2` or `gCO2e`); they are not measured emissions.

The final bounded live smoke scenario was comparison-eligible and returned a baseline proxy of 2,875, a carbon-aware proxy of 950, and a signed difference of 1,925 `gCO2` proxy units. The smoke script modified the checked-in synthetic fixture to request 8.5 kWh and set its second interval to 50 gCO2/kWh. This verifies deterministic application behaviour for that synthetic request only; it is not a claim about a real depot or actual carbon savings.

## Frontend interaction and results

The React dashboard provides a synthetic example, scenario editing, bounded JSON import/export, an explicit run action, and API-backed results. It presents the two schedules, segment allocations, depot load, constraint checks, per-strategy proxies, provenance, and equal-service comparison eligibility. Text and tables complement the visualisations. Editing inputs invalidates or cancels stale in-flight results so an older response cannot replace the current scenario's result.

The implementation and local workflow are described in the [operator guide](../OPERATOR_DASHBOARD.md). Existing images in `docs/screenshots/` outside the release-evidence directory are documented local Vite/FastAPI and Chrome/Playwright evidence; they are not captures of the Render service. The public Safari workflow was exercised on 2026-10-27 at 1440×900 and 390×844; see the [acceptance record](../releases/v0.1.0-safari-acceptance.md) and its four linked live captures. Pointer activation, visible focus-indicator state, current deployed SHA, and provider settings remain unverified.

## API design and validation

The HTTP boundary accepts ISO-8601 timestamp strings and delegates domain constraints to strict Pydantic models. The profile must align with scenario intervals and use one supported intensity unit, explicit synthetic source metadata, and the `simulation-only` geography. External actuals, forecasts, estimates, and observations are not accepted by this scheduler contract.

Input bounds include at most 96 intervals and 50 vehicles, plus finite/non-negative numeric values and configured numerical caps. In simulation-only deployment mode, middleware allowlists the single evaluation route and method, caps request bodies at 65,536 bytes, and applies a 60-request rolling limit per process. That limiter is deliberately simple: it is not per-IP, distributed across replicas, or a substitute for an edge abuse-control service. Interactive API documentation and the separate live-energy routes are disabled in the public mode.

## Testing, CI, and deployment

Post-merge GitHub Actions run [40193307594](https://github.com/vlad12-k/green-energy-dispatch-dashboard/actions/runs/40193307594) passed `quality`, `frontend-quality`, `container`, and `secret-scan` on source commit `55ae4f274848ac3642e1e0bf804feba7f07b8659`:

- Backend quality used Python 3.12 and a PostgreSQL 17 service for dependency checks, Ruff, strict `pip-audit`, Alembic upgrade/check, and the pytest suite with an 85% coverage threshold.
- Frontend quality used Node.js 22 for npm audit, lint, type-check, unit tests, build, and Playwright E2E on Chromium.
- Container CI built the image, smoke-tested it read-only with a 512 MB / 0.1 CPU limit, then ran Trivy report/gate steps for CRITICAL/HIGH findings.
- The secret scan used Gitleaks with full Git history available.

Separately, `python scripts/smoke_staging.py --base-url https://green-energy-dispatch-dashboard.onrender.com` exited `0` from a clean detached checkout at the source commit listed above. It checked health, the built app/assets and SPA fallback, a synthetic evaluation, provenance/no-real-world-claim flags, and 404 responses for selected live-energy and docs routes. This is an HTTP smoke, not a load test or a browser acceptance test.

Owner-provided Render logs explicitly record checkout of application commit `55ae4f274848ac3642e1e0bf804feba7f07b8659` from `main`, a successful Docker build/startup, HTTP 200 for `/health` and `GET /`, and Live status. This is evidence of the logged deployment event, not an independent readback of the current active Dashboard SHA or proof that no later deploy occurred. The current Compute Plan and Auto-Deploy setting have not been confirmed. The checked-in `render.yaml` is a Free/manual target descriptor for a differently named service; it is not provider-state evidence and must not be synced to create another resource.

## Engineering trade-offs and limitations

- **Single-origin Docker service:** simplifies browser/API integration and avoids permissive CORS; it also couples the static frontend and API release cadence.
- **Stateless synthetic evaluator:** keeps the public path small, reproducible, and free of persistence/upstream dependencies; it cannot demonstrate historical-data-driven operation or retain user work.
- **Bounded heuristic:** preserves deterministic service-first behaviour and offers an explainable carbon-proxy comparison without claiming global optimality; it may miss better schedules.
- **Public unauthenticated demo:** lowers visitor friction but is unsuitable for private data and relies on bounded request handling, not identity-based access control.
- **Render plan and capacity:** the owner-provided log records deployment of `55ae4f274848ac3642e1e0bf804feba7f07b8659`; current Dashboard SHA, plan, and Auto-Deploy still need independent confirmation. If the service is on a Free plan, cold starts, ephemeral storage, and shared quotas may constrain availability. No load, sustained concurrency, or peak-memory test has been run against the public service.
- **Evidence limits:** Safari desktop/mobile workflow and screenshots are documented; pointer activation, visible focus-ring styling, a screen-reader audit, and an independent all-severity vulnerability scan of a future release artifact are not claimed.

No user adoption, customer results, commercial impact, actual carbon savings, or ML-model performance is claimed. Real NESO data and forecast-backed scheduling are planned for v0.2.0 on a separate branch from the final v0.1.0 release main; they are not included here.

## Live Safari acceptance evidence

The public URL was exercised in Apple Safari 26.6.2 on 2026-10-27. The synthetic example was loaded, an input changed, and the simulation run; both schedules, equal-service comparison, carbon-proxy units, charts, accessible tables, navigation, and simulation-only notices were inspected. The responsive layout was checked at CSS viewports of 1440×900 and 390×844. See the [acceptance record](../releases/v0.1.0-safari-acceptance.md) for scenario values, exact browser metadata, and limitations. Captures: [desktop inputs](../screenshots/release-v0.1.0/safari-live-desktop-inputs-1440x900.png), [desktop results](../screenshots/release-v0.1.0/safari-live-desktop-results-1440x900.png), [mobile inputs](../screenshots/release-v0.1.0/safari-live-mobile-inputs-390x844.png), and [mobile results](../screenshots/release-v0.1.0/safari-live-mobile-results-390x844.png). The WebDriver session was isolated from the owner's authenticated browser; the captures do not prove the current deployment SHA, plan, or Auto-Deploy state. Pointer activation and the visible focus-ring state were not verified, so this workflow evidence alone does not complete the browser acceptance gate.

## Next approval

The Safari workflow and four requested captures are documented above. Before publishing v0.1.0, verify pointer activation and the visible focus indicator in a focused Safari window, and read back the existing service's current status, deployed SHA, Free plan, and Auto-Deploy setting in the Dashboard; then merge the evidence-only documentation change after its required GitHub Actions checks pass. This case study does not assert the current GitHub PR, tag, or release state or authorise a Render setting change or deployment.
