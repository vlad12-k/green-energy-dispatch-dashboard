# Green Energy Dispatch Dashboard

> **Live demo:** [Green Energy Dispatch](https://green-energy-dispatch-dashboard.onrender.com) — a public, synthetic-only EV-depot scheduling demonstration.

Green Energy Dispatch is a UK energy-software **portfolio prototype**. The deployed v0.1.0 demo serves a production-built React/Vite dashboard and FastAPI from one origin. This v0.2.0 Phase A branch adds a separate, read-only live NESO display; the synthetic EV-depot scheduler remains unchanged and never consumes that signal. Carbon-proxy values remain scenario calculations, not measured or verified emissions reductions.

> **Safety boundary:** the demo is advisory and price-free. It is not connected to an electricity market, balancing mechanism, charger, or other physical asset; it never places trades or issues control commands. Carbon-proxy values are scenario calculations, not measured or verified emissions reductions.

## Current capabilities

- `GET /health` — application health response.
- `POST /api/v1/simulations/ev-depot/evaluate` — stateless synthetic evaluation of the existing **Immediate Baseline** and **Carbon-Aware Heuristic**, with input provenance and equal-service comparison eligibility.
- React/TypeScript operator dashboard — scenario editing, bounded JSON import/export, API-backed results, schedule/load/intensity visualisations, validation feedback, and text/table alternatives.
- In the v0.2.0 Phase A application candidate, simulation-only mode allows the synthetic `POST` route and a read-only `GET /api/v1/energy/carbon-intensity` route for the configured region. Other `/api/*` routes and interactive documentation remain disabled; the public v0.1.0 deployment is not changed by this branch.
- The optional PostgreSQL evidence store/replay and collector remain separate from the dashboard and scheduler. The live GET is cached, validates upstream data, and never writes to the database or changes synthetic schedule inputs.
- No user authentication or authorisation is implemented. Do not submit personal, operational, or confidential data.

## Architecture and roadmap

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the system diagram, component boundaries, deployment evidence, and roadmap. [`docs/OPERATOR_DASHBOARD.md`](docs/OPERATOR_DASHBOARD.md) explains the scenario workflow and result interpretation; [`docs/EV_DEPOT_SIMULATION.md`](docs/EV_DEPOT_SIMULATION.md) documents the scheduler and proxy semantics. The optional local persistence and replay workflow is covered by [`docs/DATABASE_AND_REPLAY.md`](docs/DATABASE_AND_REPLAY.md), and the forecast archive boundary by [`docs/NESO_HISTORICAL_ACQUISITION.md`](docs/NESO_HISTORICAL_ACQUISITION.md). The local NESO/PostgreSQL capabilities and the public demo are deliberately described separately. Production operations—including an availability target, monitoring, alerting, backups, and live asset or market integration—are not established by this portfolio demo.

The v0.1.0 release notes are in [`docs/releases/v0.1.0.md`](docs/releases/v0.1.0.md), with the technical case study at [`docs/case-studies/green-energy-dispatch-v0.1.0.md`](docs/case-studies/green-energy-dispatch-v0.1.0.md). Current release and tag state is tracked on the repository's GitHub Releases and Tags pages, not asserted by these documents.

The public Safari workflow and captures from 2026-10-27 at 1440×900 and 390×844 are documented in [`docs/releases/v0.1.0-safari-acceptance.md`](docs/releases/v0.1.0-safari-acceptance.md). Captures: [desktop inputs](docs/screenshots/release-v0.1.0/safari-live-desktop-inputs-1440x900.png), [desktop results](docs/screenshots/release-v0.1.0/safari-live-desktop-results-1440x900.png), [mobile inputs](docs/screenshots/release-v0.1.0/safari-live-mobile-inputs-390x844.png), and [mobile results](docs/screenshots/release-v0.1.0/safari-live-mobile-results-390x844.png). Desktop mouse activation was owner-verified on 27 October 2026 at 16:58 UK time; visible keyboard focus, mobile mouse interaction, and current Render deployment metadata remain unverified.

## Quick start

```bash
git clone https://github.com/vlad12-k/green-energy-dispatch-dashboard.git
cd green-energy-dispatch-dashboard
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

This starts the API only. Synthetic scheduling requires no `.env`, database, cloud credentials, or NESO access; the separate live panel needs only outbound HTTPS to the public NESO API, and shows an explicit unavailable state if that feed cannot be reached. Defaults are conservative (`APP_ENV=production`, `DEBUG=false`); `/docs`, `/redoc`, and `/openapi.json` are disabled. Enable debug documentation only in a trusted local development environment, never in a public deployment.

## Local operator dashboard

Requirements: Python 3.12 or newer and Node.js 22.12 or newer with npm. No database, cloud account, or credentials are needed; the backend uses the public NESO API without credentials when network access is available.

Start the API from the repository root:

```bash
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

In a second terminal, install the locked frontend dependencies and start Vite:

```bash
cd frontend
npm ci
npm run dev
```

Open <http://127.0.0.1:5173>. Vite proxies `/api` and `/health` to `http://127.0.0.1:8000`; requests stay same-origin without permissive CORS. Load and edit the synthetic scenario, run the simulation, and inspect both returned schedules. The separate live panel displays the configured NESO region but never feeds either scheduling strategy; if the public upstream is unreachable, the page clearly reports that live data is unavailable while synthetic evaluation remains usable. See [`docs/OPERATOR_DASHBOARD.md`](docs/OPERATOR_DASHBOARD.md) for the operator guide, API contract, and implementation boundaries.

See [`docs/design/ev-depot-dispatch-workbench.md`](docs/design/ev-depot-dispatch-workbench.md) for the current visual architecture, selected design reference, screenshots, local QA evidence, and release limitations.

The API base is same-origin by default (`VITE_API_BASE_URL` is empty). Only set it to a same-origin API prefix when changing the local proxy configuration; cross-origin access is intentionally not enabled.

## v0.1.0 verified test and CI evidence (historical)

The following records the published v0.1.0 state and predates the v0.2.0 Phase A candidate. It is retained as release evidence and does not describe a deployment of this branch.

Post-merge GitHub Actions run [40193307594](https://github.com/vlad12-k/green-energy-dispatch-dashboard/actions/runs/40193307594) passed all four jobs on application commit `55ae4f274848ac3642e1e0bf804feba7f07b8659`:

- `quality` — Python 3.12, PostgreSQL 17 test service, Ruff, dependency audit, Alembic checks, and the backend test suite with an 85% coverage gate.
- `frontend-quality` — Node.js 22, npm audit at HIGH, lint, type-check, unit tests, production build, and Playwright end-to-end tests. The workflow's browser automation uses Chromium; this is not Safari visual acceptance.
- `container` — Docker build, constrained simulation-only container smoke (512 MB RAM, 0.1 CPU), and Trivy checks scoped to CRITICAL/HIGH vulnerabilities. This is not an all-severity zero-vulnerability claim or a load test.
- `secret-scan` — Gitleaks action with full Git history available.

The requested live smoke was also run from a clean detached checkout at that same source SHA using Python 3.14.9:

```bash
python scripts/smoke_staging.py --base-url https://green-energy-dispatch-dashboard.onrender.com
```

It exited `0`: health was `ok`; the root and a deep SPA route returned the built app; three same-origin assets were served; the synthetic evaluation was comparison-eligible; and the tested live-data/docs routes were unavailable. The response values for that synthetic scenario were 2,875 baseline and 950 carbon-aware proxy units, a 1,925 `gCO2`-proxy-unit difference. Provenance remained `synthetic_scenario` / `simulation-only`, and the response explicitly made no real-world emissions claim. These are deterministic scenario proxies, not measured carbon savings.

The owner-provided deployment log explicitly records checkout of application commit `55ae4f274848ac3642e1e0bf804feba7f07b8659` from `main` and reports Live status. This is deployment-log evidence, not an independent readback of the service's current Dashboard state. The current Compute Plan and Auto-Deploy setting have not been confirmed here; the smoke test verifies endpoint behaviour, not provider-side settings. See [`docs/deployment/staging-runbook.md`](docs/deployment/staging-runbook.md) for the verification boundary.

The Docker build produces `frontend/dist` and the FastAPI application serves the built frontend when `SERVE_FRONTEND=true`; the public demo therefore uses one origin. In local development, Vite remains a separate server and proxies API requests. TypeScript API types are generated from FastAPI's OpenAPI model during the frontend build; interactive docs remain disabled in simulation-only runtime mode.

## Read-only NESO API and dashboard section

The v0.2.0 Phase A candidate exposes a public, read-only `GET /api/v1/energy/carbon-intensity` route for the configured NESO region (default GB region ID 13). The React panel shows the region, intensity, upstream unit, actual/forecast provenance, observed interval, retrieval time, age and freshness. It is not connected to either scheduling strategy, and the published v0.1.0 service is unchanged until any separately approved deployment.

For a local read-only request:

```bash
curl http://127.0.0.1:8000/api/v1/energy/carbon-intensity
```

The backend accepts only the official HTTPS host `api.carbonintensity.org.uk`, disables redirects, applies a bounded upstream timeout (default 5 seconds; maximum 30), caps decoded JSON at 64 KiB, and validates the region, timestamp, value and response shape. The in-process cache is at most 30 seconds and never extends a reading beyond its freshness/forecast interval boundary. Fresh responses may be cached for 30 seconds; stale and error responses use `Cache-Control: no-store`. The public route uses a separate 60-request/minute process-local bucket from the synthetic evaluator; this shared per-process limit is not per-IP or distributed.

Configure `NESO_API_BASE_URL`, `NESO_REGION_ID`, `NESO_MAX_AGE_SECONDS`, and `NESO_TIMEOUT_SECONDS` as needed. The response contains `measurement_type` (`actual` or `forecast`), `interval_status` (`upcoming`, `in_progress`, or `completed`), interval bounds, retrieval time, age from interval start, interval lag, a freshness flag, source API, and source URL. The public API reports the NESO-documented unit `gCO2/kWh` without conversion; the separate persisted-observation unit mapping remains unchanged. Completed forecasts are explicitly marked stale and are not publicly cached. A completed recent actual may remain fresh within the configured max age. Future forecasts are supported up to 48 hours. NESO does not provide a distinct publication timestamp in this response, so time since interval end is not an exact measure of publication delay. Unreachable or invalid upstream data is reported as unavailable; the synthetic scheduler remains usable.

## Scheduled NESO collection (opt-in)

The standalone worker is an optional local capability, disabled by default and not started by FastAPI. It polls one configured region every 30 minutes by default, records each HTTP attempt and collection outcome in PostgreSQL, and exposes its persisted status as JSON. It is not part of the public demo. The NESO regional endpoint returns a limited latest response, so repeated polling is **not** a complete historical time series; detected interval gaps are reported, never filled with fabricated observations.

For a local demonstration, copy `.env.example` to `.env`, set a unique local `POSTGRES_PASSWORD`, and set `DATABASE_URL` to the Compose address `postgresql+psycopg://ged:<URL-encoded-password>@db:5432/ged`. Start PostgreSQL, apply the reviewed migration explicitly, then start the collector:

```bash
COLLECTOR_ENABLED=true docker compose -f docker-compose.yml -f docker-compose.database.yml --profile collector up -d db
DATABASE_URL='postgresql+psycopg://ged:URL_ENCODED_PASSWORD@127.0.0.1:54329/ged' alembic upgrade head
COLLECTOR_ENABLED=true docker compose -f docker-compose.yml -f docker-compose.database.yml --profile collector up -d collector
docker compose -f docker-compose.yml -f docker-compose.database.yml --profile collector exec collector python -m app.collect_neso status
docker compose -f docker-compose.yml -f docker-compose.database.yml --profile collector logs --follow collector
docker compose -f docker-compose.yml -f docker-compose.database.yml --profile collector stop collector
```

Configure `COLLECTOR_INTERVAL_SECONDS` (minimum 60; default 1800), `COLLECTOR_MAX_ATTEMPTS` (default 3, including the initial request), `COLLECTOR_BACKOFF_BASE_SECONDS` (default 5), and `COLLECTOR_BACKOFF_MAX_SECONDS` (default 60) in `.env` as needed. Transient network errors, HTTP 429, and 5xx responses may be retried. `Retry-After` is honoured for in-cycle retries; if its deadline exceeds the per-cycle backoff cap, later polling remains paused until that deadline, including after a worker restart. Validation and database failures are not retried. See the database guide for status fields, recovery behaviour, and limitations. PostgreSQL remains loopback-only; the worker inherits the non-root, read-only, capability-dropped container hardening.

## Backend, database, and security development checks

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q --cov=app --cov-fail-under=85
ruff check .
pip-audit -r requirements-dev.txt
```

The full backend suite requires an explicitly isolated disposable PostgreSQL test database, as in CI; never point integration tests at a shared or live database. Run frontend checks from `frontend/` with the scripts listed in [`docs/OPERATOR_DASHBOARD.md`](docs/OPERATOR_DASHBOARD.md).

Docker Compose builds and runs the API as a non-root user:

```bash
docker compose up --build
```

PostgreSQL is an opt-in development service defined in a separate Compose file, bound to loopback only. Configure local-only credentials in `.env` before starting it with `docker compose -f docker-compose.yml -f docker-compose.database.yml --profile database up -d db`. Apply reviewed migrations explicitly from the host; the existing API remains usable without PostgreSQL. See the database guide for exact migration, ingestion/replay, and test commands.

## License

MIT — see [`LICENSE`](LICENSE).