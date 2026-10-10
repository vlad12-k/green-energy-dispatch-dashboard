# Architecture and product roadmap

## Product boundary

Green Energy Dispatch is an **intelligent energy operations platform** under staged development. The delivered slice reads NESO Carbon Intensity data and evaluates a simulated, explainable dispatch policy. It is not connected to an electricity market, balancing mechanism, or physical asset; it never places trades or issues control commands.

## System context

```text
NESO Carbon Intensity API
         │ HTTPS read-only
         ▼
FastAPI adapter ──> schema / provenance / freshness validation
         │                              │
         ├── simulated policy           └──> future evidence store (PostgreSQL)
         │
         └──> API client / future React operator dashboard

Future: historical replay → forecast evaluation → energy optimisation → observability
```

## Implemented now

| Component | Responsibility | State |
|---|---|---|
| FastAPI | `/health`, `/api/v1/energy/carbon-intensity`, `/api/v1/energy/carbon-gate` | Implemented |
| NESO adapter | HTTPS regional request, parsing, provenance, freshness, timeouts, upstream error handling | Implemented, read-only |
| Carbon policy | Transparent simulated `allow`/`defer`/`block` thresholds | Implemented; illustrative only |
| PostgreSQL / time-series | Persist observations, provenance, and later operational events | Not implemented |
| Forecast/optimisation | Evaluate baseline forecasts and schedule flexible loads | Not implemented |
| React operator UI | Show live signal age, source, forecast/actual distinction, and recommendation | Not implemented |
| Production hosting | Deployment, SLOs, alerting, backups, runbooks | Not implemented |

## Data contract and provenance

The regional API response exposes an interval (`from`, `to`) and intensity values (`forecast`, `actual`, `index`). The adapter selects `actual` when available; otherwise it returns `forecast` explicitly. Each response includes region ID, measurement kind, interval bounds, retrieval time, age, freshness, source API name, and exact request URL.

Freshness is accepted only if the interval has not expired, its start is not in the future, and its age is within `NESO_MAX_AGE_SECONDS`. Failures are surfaced as service unavailable; no synthetic or last-known-good reading is mislabeled as current. The existing project does not yet persist raw upstream payloads; immutable evidence retention belongs in the next data-platform stage.

Default region ID is 13. Confirm/change it for the intended GB operating region before relying on location-specific results. The adapter does not infer a location from user identity or IP.

## Security and runtime assumptions

- NESO access is read-only over HTTPS; redirects are disabled.
- Production defaults use `APP_ENV=production`, `DEBUG=false`; debug is rejected in production.
- Interactive API documentation is disabled unless debug is explicitly enabled in a non-production environment.
- Upstream network timeout and freshness thresholds are bounded settings.
- Docker runtime uses a non-root UID and a health check.
- CI runs tests, lint, dependency audit, secret scanning, and container vulnerability scanning.
- No secrets are required for the NESO endpoint.

## NESO Carbon Intensity API

The service requests the NESO documented regional endpoint `/regional/regionid/{regionid}` over HTTPS. The feed includes interval bounds and actual/forecast intensity values. This is a read-only carbon-intensity feed; it is not a price forecast, a dispatch instruction, or a control signal. See the [official NESO Carbon Intensity API documentation](https://carbon-intensity.github.io/api-definitions/).

## Illustrative simulated carbon policy

```text
≤150 gCO₂e/kWh      allow
>150 and ≤300       defer
>300                block
```

This is a software demonstration, not an energy-market rule, safety controller, or validated commercial recommendation. Keep policy thresholds configurable/versioned and do not connect actuation without separate engineering, legal, safety, and operational review.

## Decisions

### Provenance and freshness before optimisation
Data correctness and source traceability precede forecasts. A stale upstream response must not be silently represented as real-time.

### Baseline before complex ML
Establish historical, leakage-safe baselines and operating metrics before comparing more complex forecasting models. Model complexity is justified by measured improvement, not appearance.

### Separate recommendation from execution
The platform can explain a recommendation without having authority to perform a physical or financial action. This boundary remains explicit in APIs and UI.

## Staged product roadmap

1. **Data foundation:** persist raw payloads and normalised observations with source hash, retrieval time, interval, freshness result, and schema version in PostgreSQL; migrations and deterministic replay.
2. **Data operations:** scheduled ingestion, idempotency, retries/backoff, rate-limit handling, health/metrics, stale-source alerting, backup/restore test.
3. **Forecasting:** historical dataset and temporal validation; seasonal-naïve baseline, error and calibration metrics, model card, monitored inference. Add neural models only when results justify them.
4. **Energy optimisation:** define a real load/asset-flexibility simulation, constraints and objective; run scenario/backtest evaluation and record explainable decisions. No live actuation.
5. **React dashboard:** current and historical regional signals, clear actual/forecast/stale labels, provenance, policy settings and scenario replay; accessible responsive design.
6. **Production readiness:** hardened container, SBOM, pinned/updated dependencies, CI gates, staging, IaC, secret handling, SLOs, alerts, runbooks, and security review.

## Acceptance evidence

A completed stage should ship: automated tests, reproducible sample/replay, architecture decision record for material choices, observed API contract, failure-mode behavior, security scan output, and documentation that distinguishes implemented functionality from roadmap items.
