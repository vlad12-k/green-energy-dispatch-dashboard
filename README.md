# Green Energy Dispatch Dashboard

A portfolio-grade **UK grid-aware dispatch simulator**. It demonstrates a practical AI/energy platform boundary: ingest verified grid signals, produce explainable forecasts, and apply a transparent carbon policy before any simulated dispatch recommendation is shown.

> **Safety boundary:** this project is not connected to an energy market, broker, balancing mechanism, or physical asset. It never executes financial trades or energy dispatches. Every decision is a simulation designed to demonstrate engineering, observability, and governance patterns.

## Why this project

It is designed for AI Engineering, MLOps, Data Engineering, and Digital Solutions roles in the UK energy and climate-tech market. The finished product will show:

- typed FastAPI APIs and automated tests;
- carbon-aware, explainable policy controls;
- reproducible data ingestion and provenance;
- forecast evaluation rather than unsupported prediction claims;
- containerised delivery, CI, monitoring, and a small operator dashboard.

## Current vertical slice

The initial backend slice is working now:

- `GET /health` — service health/readiness metadata;
- `POST /api/v1/energy/carbon-gate` — returns an **allow**, **defer**, or **block** recommendation for a supplied carbon-intensity measurement;
- request validation rejects invalid negative intensity values;
- tests cover all three policy branches and input validation.

Policy thresholds are intentionally explicit and currently illustrative:

| Carbon intensity | Simulated recommendation |
|---:|---|
| ≤150 gCO₂e/kWh | `allow` |
| >150 and ≤300 gCO₂e/kWh | `defer` |
| >300 gCO₂e/kWh | `block` |

They are not operational trading rules and will become configurable only after a documented data/validation phase.

## Architecture

```text
UK grid/intensity data ──> ingestion & validation ──> PostgreSQL/time-series store
                                                        │
Forecast evaluation/training ──> versioned model ──────┼──> FastAPI policy/API ──> dashboard
                                                        │              │
                                                provenance/metrics <───┴──> Prometheus/Grafana
```

Read the full design, system boundaries, and delivery roadmap in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Tech stack

| Concern | Technology |
|---|---|
| API | Python 3.12, FastAPI, Pydantic v2 |
| Testing & quality | pytest, httpx, Ruff, GitHub Actions |
| Data / ML roadmap | Pandas, scikit-learn, PyTorch |
| Production roadmap | PostgreSQL/TimescaleDB, Docker, Prometheus, Grafana |
| Web UI roadmap | React, TypeScript, Tailwind CSS |

## Quick start

```bash
cd /Users/maximum/GreenEnergyTradingDashboard
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

Open API docs at `http://127.0.0.1:8000/docs`.

Evaluate the policy:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/energy/carbon-gate \
  -H 'content-type: application/json' \
  -d '{"carbon_intensity_gco2_per_kwh":120}'
```

Expected response:

```json
{
  "decision": "allow",
  "reason": "Carbon intensity is within the low-carbon dispatch threshold.",
  "carbon_intensity_gco2_per_kwh": 120.0
}
```

## Quality checks

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
```

## Roadmap

1. **Data foundation:** documented source adapter, freshness checks, immutable raw evidence, and a historical replay fixture.
2. **Forecasting:** baseline model, time-series split, error metrics, model cards, and only then an LSTM comparison.
3. **Platform:** PostgreSQL/TimescaleDB, Prometheus/Grafana, Docker Compose, and failure-mode tests.
4. **Operator experience:** React dashboard with provenance, confidence, policy explanation, and scenario replay.
5. **Deployment:** staging environment, CI/CD, security scanning, and production runbook.

## License

MIT — see [`LICENSE`](LICENSE).