# Technical case study: a bounded, synthetic EV-depot dispatch comparison

**Project:** Green Energy Dispatch Dashboard

**Evidence checkpoint:** application source commit `55ae4f274848ac3642e1e0bf804feba7f07b8659`

**Status:** portfolio case study prepared for review; no GitHub Release or tag has been published.

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

The implementation and local workflow are described in the [operator guide](../OPERATOR_DASHBOARD.md). Existing images in `docs/screenshots/` are documented local Vite/FastAPI and Chrome/Playwright evidence; they are not captures of the Render service or Safari. The hosted page was reached by the earlier HTTP smoke test, but a full Safari interaction and visual review has not been completed.

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

Owner-provided Render logs report a Live service checked out from `main`, a successful Docker build/startup, and HTTP 200 for `/health` and `GET /`. The supplied evidence does not identify the active deployed SHA. The existing service's Compute Plan and Auto-Deploy setting have not been independently verified in the provider Dashboard. The checked-in `render.yaml` is a Free/manual target descriptor for a differently named service; it is not provider-state evidence and must not be synced to create another resource.

## Engineering trade-offs and limitations

- **Single-origin Docker service:** simplifies browser/API integration and avoids permissive CORS; it also couples the static frontend and API release cadence.
- **Stateless synthetic evaluator:** keeps the public path small, reproducible, and free of persistence/upstream dependencies; it cannot demonstrate historical-data-driven operation or retain user work.
- **Bounded heuristic:** preserves deterministic service-first behaviour and offers an explainable carbon-proxy comparison without claiming global optimality; it may miss better schedules.
- **Public unauthenticated demo:** lowers visitor friction but is unsuitable for private data and relies on bounded request handling, not identity-based access control.
- **Render Free target:** can have cold starts, ephemeral local storage, shared quotas, and no demonstrated SLA. Current plan, deployed SHA, and Auto-Deploy settings need Dashboard verification. No load, sustained concurrency, or peak-memory test has been run against the public service.
- **Evidence limits:** no complete Safari desktop/mobile acceptance, screen-reader audit, or independent all-severity vulnerability scan of a future release artifact is claimed.

No user adoption, customer results, commercial impact, actual carbon savings, or ML-model performance is claimed.

## Visual evidence plan (not yet captured)

Capture new evidence only in Apple Safari against the live URL after an authorised browser session is available and the owner has confirmed the target service. Do not use Chrome for this acceptance. Keep the existing local Chrome/Playwright screenshots unchanged and labelled as local evidence.

Planned captures (paths are proposed; these files do not yet exist):

1. `docs/screenshots/release-v0.1.0/safari-live-desktop-inputs-1440x900.png` — live URL visible, simulation-only notice, default/example scenario and input controls.
2. `docs/screenshots/release-v0.1.0/safari-live-desktop-results-1440x900.png` — both strategy outputs, equal-service eligibility, synthetic provenance, proxy units, comparison, and schedule/load views.
3. `docs/screenshots/release-v0.1.0/safari-live-mobile-inputs-390x844.png` — labelled controls and run action at a mobile viewport, with no clipping.
4. `docs/screenshots/release-v0.1.0/safari-live-mobile-results-390x844.png` — readable comparison/results, table alternative, and no page-level horizontal overflow.

For each capture, record the exact URL, date, Safari version, viewport, and whether the source SHA was provider-verified. Use only synthetic data and avoid account or personal information. A screenshot supports a visual claim only; separately exercise the interaction and check browser errors. If capture or Safari access fails, leave the item pending rather than reusing a local image as live evidence.

## Next approval

Review the documentation-only Draft PR. Before publishing v0.1.0, the owner must verify the existing service's active SHA, Free plan, and Auto-Deploy setting; complete Safari desktop/mobile acceptance and approve any new live screenshots; then explicitly approve creating the `v0.1.0` tag and publishing a GitHub Release. No merge, tag, release, Render setting change, or deployment is authorised by this case study.
