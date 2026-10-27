# Green Energy Dispatch Dashboard

> **Live demo:** [Green Energy Dispatch](https://green-energy-dispatch-dashboard.onrender.com) — a public, synthetic-only EV-depot scheduling demonstration.

Green Energy Dispatch is a UK grid-aware energy operations **portfolio prototype**. The hosted demo serves a production-built React/Vite dashboard and FastAPI from one origin. Visitors can edit a synthetic depot scenario and compare Python-computed schedules. No live NESO signal feeds this scheduler.

> **Safety boundary:** the demo is advisory and price-free. It is not connected to an electricity market, balancing mechanism, charger, or other physical asset; it never places trades or issues control commands. Carbon-proxy values are scenario calculations, not measured or verified emissions reductions.

## Current capabilities

- `GET /health` — application health response.
- `POST /api/v1/simulations/ev-depot/evaluate` — stateless synthetic evaluation of the existing **Immediate Baseline** and **Carbon-Aware Heuristic**, with input provenance and equal-service comparison eligibility.
- React/TypeScript operator dashboard — scenario editing, bounded JSON import/export, API-backed results, schedule/load/intensity visualisations, validation feedback, and text/table alternatives.
- In the public simulation-only mode, other `/api/*` routes and interactive API documentation are disabled. The public smoke test verified the documented NESO routes and docs paths return 404.
- The broader repository also contains a separate read-only NESO adapter, optional PostgreSQL evidence storage/replay, and an opt-in collector. These are local/optional capabilities; they are not inputs to the hosted scheduler, and the public demo does not persist submitted scenarios.
- No user authentication or authorisation is implemented. Do not submit personal, operational, or confidential data.

## Architecture and roadmap

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the system diagram, component boundaries, deployment evidence, and roadmap. [`docs/OPERATOR_DASHBOARD.md`](docs/OPERATOR_DASHBOARD.md) explains the scenario workflow and result interpretation; [`docs/EV_DEPOT_SIMULATION.md`](docs/EV_DEPOT_SIMULATION.md) documents the scheduler and proxy semantics. The optional local persistence and replay workflow is covered by [`docs/DATABASE_AND_REPLAY.md`](docs/DATABASE_AND_REPLAY.md), and the forecast archive boundary by [`docs/NESO_HISTORICAL_ACQUISITION.md`](docs/NESO_HISTORICAL_ACQUISITION.md). The local NESO/PostgreSQL capabilities and the public demo are deliberately described separately. Production operations—including an availability target, monitoring, alerting, backups, and live asset or market integration—are not established by this portfolio demo.

The v0.1.0 release notes are in [`docs/releases/v0.1.0.md`](docs/releases/v0.1.0.md), with the technical case study at [`docs/case-studies/green-energy-dispatch-v0.1.0.md`](docs/case-studies/green-energy-dispatch-v0.1.0.md). Current release and tag state is tracked on the repository's GitHub Releases and Tags pages, not asserted by these documents.

## Quick start

```bash
git clone https://github.com/vlad12-k/green-energy-dispatch-dashboard.git
cd green-energy-dispatch-dashboard
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

This starts the API only. The synthetic dashboard needs no `.env`, database, cloud credentials, or NESO access. Defaults are conservative (`APP_ENV=production`, `DEBUG=false`); `/docs`, `/redoc`, and `/openapi.json` are disabled. Enable debug documentation only in a trusted local development environment, never in the public demo.

## Local operator dashboard

Requirements: Python 3.12 or newer and Node.js 22.12 or newer with npm. No database, cloud account, NESO access, or credentials are needed for the synthetic dashboard.

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

Open <http://127.0.0.1:5173>. Vite proxies `/api` and `/health` to `http://127.0.0.1:8000`; requests stay same-origin without permissive CORS. Load the synthetic example, edit the vehicle energy requirement or carbon profile, run the simulation, inspect both returned schedules, and export the scenario JSON. The dashboard does not call the NESO endpoint. See [`docs/OPERATOR_DASHBOARD.md`](docs/OPERATOR_DASHBOARD.md) for the operator guide, API contract, and implementation boundaries.

See [`docs/design/ev-depot-dispatch-workbench.md`](docs/design/ev-depot-dispatch-workbench.md) for the current visual architecture, selected design reference, screenshots, local QA evidence, and release limitations.

The API base is same-origin by default (`VITE_API_BASE_URL` is empty). Only set it to a same-origin API prefix when changing the local proxy configuration; cross-origin access is intentionally not enabled.

## Verified test and CI evidence

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

## Separate local NESO adapter

The full local application also provides a read-only NESO route (default GB region ID 13). It is not part of the public simulation-only API and is not connected to the EV-depot scheduler. To exercise it locally:

```bash
curl http://127.0.0.1:8000/api/v1/energy/carbon-intensity
```

Configure `NESO_REGION_ID`, `NESO_MAX_AGE_SECONDS`, and `NESO_TIMEOUT_SECONDS` as needed. The response includes `measurement_type` (`actual` or `forecast`), `interval_status` (`upcoming`, `in_progress`, or `completed`), interval bounds, retrieval time, age from interval start, time since interval end, a freshness flag, source API, and source URL. Completed intervals are not automatically rejected: recent actual readings may remain fresh; a completed forecast is returned with `is_fresh=false`. Future forecasts are supported up to 48 hours. NESO does not provide a distinct publication timestamp in this response, so time since interval end is not an exact measure of publication delay. Readings beyond `NESO_MAX_AGE_SECONDS` still fail closed.

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