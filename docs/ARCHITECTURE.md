# Architecture and product roadmap

## Product boundary

Green Energy Dispatch is an **intelligent energy operations platform** under staged development. The delivered slice reads NESO Carbon Intensity data and evaluates a simulated, explainable dispatch policy. It is not connected to an electricity market, balancing mechanism, or physical asset; it never places trades or issues control commands.

## System context

```text
                         ┌── FastAPI read-only adapter ──> API clients
NESO Carbon Intensity ───┤
API (HTTPS, read-only)   └── standalone opt-in collector ──> existing parser / persistence
                                                               │
                               optional PostgreSQL evidence store ◄┘

Future: forecast evaluation → energy optimisation → operator dashboard → production operations
```

## Implemented now

| Component | Responsibility | State |
|---|---|---|
| FastAPI | `/health`, `/api/v1/energy/carbon-intensity`, `/api/v1/energy/carbon-gate` | Implemented |
| NESO adapter | HTTPS regional request, parsing, provenance, freshness, timeouts, upstream error handling | Implemented, read-only |
| Carbon policy | Transparent simulated `allow`/`defer`/`block` thresholds | Implemented; illustrative only |
| PostgreSQL evidence store | Immutable raw NESO JSON evidence, SHA-256, normalised observations, explicit Alembic migrations, one-shot ingestion and offline replay | Implemented as optional foundation |
| NESO collector | Standalone opt-in regional polling, bounded retries, durable run/attempt status, gap reporting, JSON logs and graceful stop | Implemented for local demonstration; not a production scheduler |
| Historical datasets and baselines | Deterministic regional CSV/manifest export; chronological persistence and seasonal-naive evaluation with as-of-time checks | Framework implemented; no genuine historical evaluation is currently possible from the inspected project database |
| Energy optimisation | Schedule flexible loads subject to constraints and objectives | Not implemented |
| React operator UI | Show live signal age, source, forecast/actual distinction, and recommendation | Not implemented |
| Production hosting | Deployment, SLOs, alerting, backups, runbooks | Not implemented |

## Scheduled data operations

The collector is a separate `python -m app.collect_neso run` process; FastAPI lifespan does not create a scheduler. It is disabled unless `COLLECTOR_ENABLED=true`, polls one configured region immediately and then waits a fixed interval (default 30 minutes) after each completed cycle. Sequential execution and a per-region PostgreSQL advisory lock prevent overlapping or duplicate local workers. `SIGINT`/`SIGTERM` stops the poll loop and interrupts waits/backoffs; active requests finish within the bounded NESO timeout.

Transient network failures, HTTP 429 and HTTP 5xx responses use a bounded configurable retry policy. `Retry-After` is honoured for in-cycle retries; when its deadline exceeds the per-cycle backoff cap, the worker defers later requests until the persisted deadline, including after restart. Cooldown waits are interruptible by graceful shutdown. Validation and persistence failures are not retried. Each request attempt and each poll cycle is stored in a small versioned operational schema; `python -m app.collect_neso status` reports the current advisory-lock state, latest attempt/success, source retrieval/interval, actual/forecast and freshness fields, duplicate/new evidence outcomes, consecutive failed cycles, and interval gaps. Operational state is not a replacement for logs or an alerting system.

The regional endpoint provides a limited latest response, not a complete historical export. The collector stores only evidence actually returned, reports complete gaps between successful interval boundaries, and never fills missing periods. A repeated interval may have newly retained retrieval evidence; status distinguishes raw evidence insertion, normalised observation insertion, and duplicate interval. The 30-minute default matches the interval granularity used by this feed; it does not guarantee complete coverage or uptime. See [`DATABASE_AND_REPLAY.md`](DATABASE_AND_REPLAY.md) for exact local commands, failure recovery and limitations.

## Historical datasets and baseline evaluation

The project-local PostgreSQL database inspected for this milestone was `ged` on the local development service. It had only `alembic_version` at revision `0001_neso_evidence`; `raw_payloads` and `carbon_observations` did not exist, so it exposed **zero historical observations** (zero actuals and zero forecasts) and no real-data period for forecast evaluation. This is a time-of-inspection snapshot, not a claim about other databases or later collection runs. Fixtures used in automated tests are synthetic, explicitly test-only examples and their metrics are not forecast-performance evidence.

The historical dataset builder reads validated, normalised observations joined to their raw-payload provenance. It exports one requested region, converts interval and retrieval timestamps to UTC, retains gCO2e/kWh, source API/URL, raw-payload ID and SHA-256, and marks actual and forecast rows separately. Rejected payloads are counted but never emitted as accepted dataset rows. Canonical actual selection, persistence and seasonal-naive source selection share the same revision precedence: latest eligible retrieval time, then lexicographically greatest payload SHA-256, then greatest raw-payload ID. The canonical JSON hash also retains all accepted actual revision evidence for as-of-time evaluation. Forecast records are retained separately by retrieval. The manifest reports duplicate/conflicting actual revisions, missing UTC half-hour bins, actual/forecast coverage and contiguous actual spans. Missing intervals are not interpolated.

The evaluation CLI uses a chronological fraction split or an explicit timezone-aware cutoff, splitting all revisions of an interval together. Fraction splits keep at least one unique interval on each side when two or more are available. It reports persistence and seasonal-naive baselines for configured half-hour horizons, MAE/RMSE, sample counts, target period and skipped inputs. Evaluation is rolling-origin within the holdout: forecast origins earlier than the first test interval are excluded, so the first `horizon` test targets are warm-up rather than scored samples. An earlier test-period actual may be used at a later test forecast origin, but only when its interval has ended and its retrieval time is no later than that origin. The latest eligible revision is chosen for each source interval using the same retrieval/hash/raw-ID precedence as canonical dataset selection; future actuals and future retrievals cannot be used. The JSON report lists requested and adequately evaluated horizons/methods and an unavailable reason for each method/horizon pair without valid samples. The evaluated horizon and method lists are independent summaries: each includes a value when at least one corresponding pair meets the sample threshold; use the pair-level result, unavailable and insufficient-sample entries for exact coverage. Metrics are retained when computed, including those below the configurable minimum evaluation sample count (default two), and identified in `insufficient_sample_evaluations`. `sufficient_data` is true only when every requested pair meets that sample threshold and the training/test requirements are met. These explainable baselines are an evaluation framework, not a trained production model.

Use `python -m app.build_historical_dataset --region-id 13 --output artifacts/region-13.csv` to create the CSV and JSON manifest, and `python -m app.evaluate_historical_baselines --region-id 13 --train-fraction 0.8 --horizons 1,2,4,48` to evaluate if the stored actuals support it. Both commands require `DATABASE_URL` for a database already migrated to the current schema. The inspected local database was not migrated past `0001`, so these commands require upgrading it before use. Integration tests use disposable PostgreSQL schemas with real Alembic migrations and labelled fixtures; they do not call the live NESO API.

## Data contract and provenance

Each response includes region ID, measurement kind (`actual`/`forecast`), interval status (`upcoming`/`in_progress`/`completed`), interval bounds, retrieval time, age from interval start, lag since interval end, freshness flag, source API name, and exact request URL. Optional persistence stores both canonical raw JSON evidence and normalised values, linked by source-payload provenance and SHA-256. Raw evidence is immutable; repeated submissions are idempotent. Replay verifies stored bytes and reuses the existing parser offline with original retrieval-time context. See [`DATABASE_AND_REPLAY.md`](DATABASE_AND_REPLAY.md) for details and limits.

Freshness is bounded by `NESO_MAX_AGE_SECONDS` measured from interval start. A completed interval is not automatically stale: a recent `actual` may be returned as the latest estimate within that age limit. A completed `forecast` is returned explicitly with `interval_status=completed` and `is_fresh=false`; future forecasts are identified as `upcoming`. Readings older than the configured age limit remain rejected. `interval_lag_seconds` measures time after the interval ended; the API response does not expose a separate publication timestamp, so this is not a measured publication delay. The source may have publication latency that cannot be independently quantified from this payload. Persistence retains canonical JSON evidence, not the original HTTP wire bytes/headers.

Default region ID is 13. Confirm/change it for the intended GB operating region before relying on location-specific results. The adapter does not infer a location from user identity or IP.

## Security and runtime assumptions

- NESO access is read-only over HTTPS; redirects are disabled.
- Production defaults use `APP_ENV=production`, `DEBUG=false`; debug is rejected in production.
- Interactive API documentation is disabled unless debug is explicitly enabled in a non-production environment.
- Upstream network timeout and freshness thresholds are bounded settings.
- API and collector containers use a non-root UID, read-only filesystem, dropped capabilities, and `no-new-privileges`; the API image has a health check. PostgreSQL binds to loopback only in the local Compose overlay.
- Optional development PostgreSQL is isolated behind a Compose profile and bound to loopback only; database migrations are explicit CLI operations.
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

1. **Data foundation (implemented):** optional PostgreSQL storage of raw payloads and normalised observations with source hash, retrieval time, interval, freshness result, schema version, explicit migrations, one-shot idempotent ingestion, and offline deterministic replay.
2. **Data operations (local collector implemented):** opt-in scheduled polling, bounded retry/backoff, 429 handling, per-attempt status, freshness and interval-gap reporting, and graceful shutdown. Production-grade scheduling, backfill, alerting, retention, and backup/restore remain planned.
3. **Forecasting (partially implemented):** a provenance-aware dataset and chronological persistence/seasonal-naive evaluation framework are available; meaningful real-data evaluation, further error/calibration evidence, model cards and monitored inference remain future work. Add neural models only when measured baselines justify them.
4. **Energy optimisation:** define a real load/asset-flexibility simulation, constraints and objective; run scenario/backtest evaluation and record explainable decisions. No live actuation.
5. **React dashboard:** current and historical regional signals, clear actual/forecast/stale labels, provenance, policy settings and scenario replay; accessible responsive design.
6. **Production readiness:** hardened container, SBOM, pinned/updated dependencies, CI gates, staging, IaC, secret handling, SLOs, alerts, runbooks, and security review.

## Acceptance evidence

A completed stage should ship: automated tests, reproducible sample/replay, architecture decision record for material choices, observed API contract, failure-mode behaviour, security scan output, and documentation that distinguishes implemented functionality from roadmap items.
