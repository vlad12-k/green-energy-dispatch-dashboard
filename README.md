# Green Energy Dispatch Dashboard

A UK grid-aware energy operations platform in active development. The current slice integrates the NESO Carbon Intensity API read-only, validates regional readings and freshness, preserves source/interval provenance, and evaluates an illustrative simulated dispatch policy.

> **Safety boundary:** no market, balancing mechanism, or physical device is connected. The service never places trades or issues control commands. Carbon thresholds are illustrative only and not operational guidance.

## Current capabilities

- `GET /health` — single service health endpoint.
- `GET /api/v1/energy/carbon-intensity` — live NESO regional intensity reading; returns actual when available, otherwise explicitly marks forecast. Stale, malformed, or unavailable upstream data fails closed with HTTP 503; no fabricated fallback is used.
- `POST /api/v1/energy/carbon-gate` — transparent simulated `allow`, `defer`, or `block` recommendation based on an explicitly illustrative policy.
- Pydantic validation rejects negative, non-finite, malformed, and unexpected request fields.
- Production defaults disable debug mode and interactive OpenAPI docs.

## Architecture and roadmap

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for system boundaries, current implementation status, data provenance, security assumptions, and staged product roadmap. PostgreSQL persistence, forecast evaluation, energy optimisation, React UI, and production hosting are not yet implemented; the repo does not claim otherwise.

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

Fetch the configured GB region (default NESO region ID 13):

```bash
curl http://127.0.0.1:8000/api/v1/energy/carbon-intensity
```

Configure `NESO_REGION_ID`, `NESO_MAX_AGE_SECONDS`, and `NESO_TIMEOUT_SECONDS` as needed. The response includes `measurement_type` (`actual` or `forecast`), `interval_status` (`upcoming`, `in_progress`, or `completed`), interval bounds, retrieval time, age from interval start, time since interval end, a freshness flag, source API, and source URL. Completed intervals are not automatically rejected: recent actual readings may remain fresh; a completed forecast is returned with `is_fresh=false`. Future forecasts are supported up to 48 hours. NESO does not provide a distinct publication timestamp in this response, so time since interval end is not an exact measure of publication delay. Readings beyond `NESO_MAX_AGE_SECONDS` still fail closed.

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

## License

MIT — see [`LICENSE`](LICENSE).