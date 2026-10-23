# Green Energy Dispatch Dashboard

A UK grid-aware energy operations platform in active development. The current application includes the read-only NESO Carbon Intensity adapter, a deterministic synthetic EV-depot scheduling simulation, scenario evaluation tools, and a local React operator dashboard. The dashboard sends validated synthetic scenarios to FastAPI and displays results from the existing Python scheduler; it does not control physical assets or make market decisions.

> **Safety boundary:** no market, balancing mechanism, or physical device is connected. The service never places trades or issues control commands. Carbon thresholds are illustrative only and not operational guidance.

## Current capabilities

- `GET /health` — single service health endpoint.
- `GET /api/v1/energy/carbon-intensity` — live NESO regional intensity reading; returns actual when available, otherwise explicitly marks forecast. Stale, malformed, or unavailable upstream data fails closed with HTTP 503; no fabricated fallback is used.
- `POST /api/v1/energy/carbon-gate` — transparent simulated `allow`, `defer`, or `block` recommendation based on an explicitly illustrative policy.
- `POST /api/v1/simulations/ev-depot/evaluate` — stateless, synthetic-only comparison of the existing **Immediate Baseline** and **Carbon-Aware Heuristic** Python strategies; no database or upstream data calls.
- React/TypeScript operator dashboard — local scenario editing, JSON import/export, real API evaluation, accessible schedule/load/intensity charts, validation feedback, and equal-service proxy explanations.
- Pydantic validation rejects negative, non-finite, malformed, and unexpected request fields.
- Production defaults disable debug mode and interactive OpenAPI docs.

## Architecture and roadmap

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for system boundaries, current implementation status, data provenance, security assumptions, and staged product roadmap. See [`docs/SECURITY.md`](docs/SECURITY.md) for container scan evidence and policy. Optional PostgreSQL storage of NESO source evidence and normalised observations, one-shot ingestion, offline replay, an opt-in standalone regional collector, historical dataset export, and simple chronological forecast baselines are implemented. Follow [`docs/DATABASE_AND_REPLAY.md`](docs/DATABASE_AND_REPLAY.md) for database setup and [`docs/NESO_HISTORICAL_ACQUISITION.md`](docs/NESO_HISTORICAL_ACQUISITION.md) for the official regional forecast archive contract and controlled acquisition workflow. The synthetic scheduling/evaluation workflow and local React operator dashboard are implemented; cloud hosting, live asset control, electricity-market participation, and production operations are not.

## Quick start

```bash
git clone https://github.com/vlad12-k/green-energy-dispatch-dashboard.git
cd green-energy-dispatch-dashboard
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
python -m uvicorn app.main:app
```

Defaults are production-safe (`APP_ENV=production`, `DEBUG=false`); `/docs`, `/redoc`, and `/openapi.json` are disabled. For trusted local development only, set `APP_ENV=development` and `DEBUG=true` in `.env`.

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

Open <http://127.0.0.1:5173>. Vite proxies `/api` and `/health` to `http://127.0.0.1:8000`; browser requests remain same-origin, so the API does not enable permissive CORS. Load the synthetic example, edit the vehicle energy requirement and carbon profile, run the simulation, inspect both returned schedules and export the scenario JSON. The dashboard does not call the NESO endpoint. See [`docs/OPERATOR_DASHBOARD.md`](docs/OPERATOR_DASHBOARD.md) for the operator guide, result interpretation, API contract, and implemented/future boundaries.

See [`docs/design/ev-depot-dispatch-workbench.md`](docs/design/ev-depot-dispatch-workbench.md) for the current visual architecture, selected design reference, screenshots, local QA evidence, and release limitations.

The API base is same-origin by default (`VITE_API_BASE_URL` is empty). Only set it to a same-origin API prefix when changing the local proxy configuration; cross-origin access is intentionally not enabled.

Frontend checks, build, and real FastAPI/browser integration test:

```bash
npm run lint
npm run typecheck
npm test
npm run build
npm run test:e2e
```

The production build regenerates TypeScript API types from FastAPI OpenAPI and writes static assets to `frontend/dist`. The API remains independently runnable; this milestone does not configure production hosting or serve the built frontend from FastAPI.

Fetch the configured GB region (default NESO region ID 13):

```bash
curl http://127.0.0.1:8000/api/v1/energy/carbon-intensity
```

Configure `NESO_REGION_ID`, `NESO_MAX_AGE_SECONDS`, and `NESO_TIMEOUT_SECONDS` as needed. The response includes `measurement_type` (`actual` or `forecast`), `interval_status` (`upcoming`, `in_progress`, or `completed`), interval bounds, retrieval time, age from interval start, time since interval end, a freshness flag, source API, and source URL. Completed intervals are not automatically rejected: recent actual readings may remain fresh; a completed forecast is returned with `is_fresh=false`. Future forecasts are supported up to 48 hours. NESO does not provide a distinct publication timestamp in this response, so time since interval end is not an exact measure of publication delay. Readings beyond `NESO_MAX_AGE_SECONDS` still fail closed.

## Scheduled NESO collection (opt-in)

The standalone worker is disabled by default and is not started by FastAPI. It polls one configured region every 30 minutes by default, records each HTTP attempt and collection outcome in PostgreSQL, and exposes its persisted status as JSON. The NESO regional endpoint returns a limited latest response, so repeated polling is **not** a complete historical time series; detected interval gaps are reported, never filled with fabricated observations.

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

## Development checks

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q --cov=app --cov-fail-under=85
ruff check .
pip-audit -r requirements-dev.txt
```

Docker Compose builds and runs the API as a non-root user:

```bash
docker compose up --build
```

PostgreSQL is an opt-in development service defined in a separate Compose file, bound to loopback only. Configure local-only credentials in `.env` before starting it with `docker compose -f docker-compose.yml -f docker-compose.database.yml --profile database up -d db`. Apply reviewed migrations explicitly from the host; the existing API remains usable without PostgreSQL. See the database guide for exact migration, ingestion/replay, and test commands.

## License

MIT — see [`LICENSE`](LICENSE).