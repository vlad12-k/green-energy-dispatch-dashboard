# PostgreSQL evidence storage and replay

This milestone adds an optional local PostgreSQL store for raw NESO regional responses and normalised carbon-intensity observations, plus a separate opt-in collector process. The existing HTTP routes remain stateless and continue to work when `DATABASE_URL` is unset. The collector is not started by FastAPI, and database migrations remain explicit.

## Local setup

Requirements: Docker with Compose v2 and Python 3.12.

1. Copy `.env.example` to `.env`, add a strong local-only `POSTGRES_PASSWORD`, and set `DATABASE_URL` to `postgresql+psycopg://ged:<URL-encoded-password>@db:5432/ged` using the same password. Never use these local credentials for a hosted or production database.
2. Start only the opt-in database service with the separate overlay: `docker compose -f docker-compose.yml -f docker-compose.database.yml --profile database up -d db`. Configuration fails fast if `POSTGRES_PASSWORD` is unset. PostgreSQL binds to `127.0.0.1:54329` for local development and is not exposed on external interfaces; Compose services can use the internal `db:5432` address. The default API Compose file remains usable without the database overlay. Existing API container hardening remains in place.
3. Apply schema changes explicitly from the repository root: `DATABASE_URL='postgresql+psycopg://ged:URL_ENCODED_PASSWORD@127.0.0.1:54329/ged' alembic upgrade head`. Replace `URL_ENCODED_PASSWORD` with the URL-encoded local password. Migrations are never run automatically at application startup.
4. For a one-shot host-side ingestion, use a host-reachable local URL: `DATABASE_URL='postgresql+psycopg://ged:URL_ENCODED_PASSWORD@127.0.0.1:54329/ged' python -m app.ingest_neso`. The command makes one request and emits a JSON result with insertion status and provenance. It is not run automatically by the API. From a Compose container, use `db:5432` instead of the loopback URL.
5. Replay a stored raw payload by its database ID without any NESO request: `DATABASE_URL='postgresql+psycopg://ged:URL_ENCODED_PASSWORD@127.0.0.1:54329/ged' python -m app.replay_neso 1`. Replace `1` with the existing raw-payload ID.
6. Run the API when needed with `docker compose up --build api`. The database can remain stopped if persistence is not in use.
7. Stop the database with `docker compose -f docker-compose.yml -f docker-compose.database.yml --profile database down`. Persistent local data remains in the named `postgres_data` volume. To remove that local data deliberately, add `--volumes`; this is destructive and is not part of normal cleanup.

The PostgreSQL image is development-only. This project does not provide production database hosting, backup/restore, high availability, retention, access-management, or operational monitoring.

## Scheduled collector

The worker reuses `NesoCarbonIntensityClient`, the regional parser, `ingest_neso`, and the immutable PostgreSQL persistence path. It starts immediately, performs one cycle at a time, then waits the configured interval after that cycle completes. A process-local lock prevents overlapping cycles; a PostgreSQL session advisory lock prevents a second worker for the same region from running concurrently. It shuts down on `SIGINT`/`SIGTERM`; an active request is allowed to finish within the configured HTTP timeout, and shutdown interrupts a backoff or polling wait.

The worker is an explicit command (`python -m app.collect_neso run`) and requires `COLLECTOR_ENABLED=true`. The default is `false`; FastAPI startup never starts a collector. It also requires the collector migration to have been applied already. The worker does not run Alembic or mutate the schema at startup.

### Configuration and retry policy

| Variable | Default | Constraints / meaning |
|---|---:|---|
| `COLLECTOR_ENABLED` | `false` | Must be explicitly set to `true` to run the worker. |
| `COLLECTOR_INTERVAL_SECONDS` | `1800` | Fixed delay after each completed cycle; minimum 60 seconds. |
| `COLLECTOR_MAX_ATTEMPTS` | `3` | Total HTTP attempts per cycle, including the first request. |
| `COLLECTOR_BACKOFF_BASE_SECONDS` | `5` | Exponential backoff base. |
| `COLLECTOR_BACKOFF_MAX_SECONDS` | `60` | Maximum retry delay; must be at least the base. |
| `NESO_TIMEOUT_SECONDS` | `5` | Bounded timeout for each upstream attempt. |
| `NESO_MAX_AGE_SECONDS` | `3600` | Existing source freshness limit; rejected responses are not retried. |

Network timeouts/request errors, HTTP 429, and HTTP 5xx responses are retryable. Other HTTP statuses, malformed/invalid source data, validation failures, and database persistence failures are not retried. Exponential in-cycle delays are bounded. For a valid `Retry-After` within the configured cap, the worker waits at least that long before retrying. If the server deadline exceeds the cap, the worker makes no in-cycle retry and persists the attempt's header and completion time; every later cycle waits until the resolved deadline before issuing another request. The latest completed HTTP attempt determines the active cooldown, so a later successful request supersedes older Retry-After metadata. The cooldown uses persisted attempt metadata and therefore survives worker restarts. Numeric delta-seconds are accepted only as non-negative decimal integers; HTTP-date values are also supported. Cooldown waits are interruptible by graceful shutdown; an interrupted wait creates no HTTP attempt. Each HTTP attempt is recorded in `neso_collector_attempts` before the request is issued and then updated with its result; each cycle is stored in `neso_collector_runs`. These operational records use independent short transactions; source evidence and its normalised observation retain their existing atomic transaction.

### Local Compose demonstration

Use the same `.env`, loopback-only PostgreSQL service, and explicit migration commands from [Local setup](#local-setup). Set the collector parameters in `.env` if you need non-default values. Start and inspect the standalone worker as follows:

```bash
COLLECTOR_ENABLED=true docker compose -f docker-compose.yml -f docker-compose.database.yml --profile collector up -d collector
docker compose -f docker-compose.yml -f docker-compose.database.yml --profile collector logs --follow collector
docker compose -f docker-compose.yml -f docker-compose.database.yml --profile collector exec collector python -m app.collect_neso status
docker compose -f docker-compose.yml -f docker-compose.database.yml --profile collector stop collector
```

The `status` command emits JSON containing whether a worker currently holds the database lock, the most recent run and individual HTTP attempts, the latest attempt time, latest successful ingestion time, source retrieval and interval bounds, actual/forecast classification, freshness, payload hash, evidence/observation insert outcomes, duplicate-interval indicator, missing interval count, consecutive failed cycles, and the latest error category. The JSON command is read-only; it does not retry collection or run migrations. Structured JSON logs include run/attempt IDs, outcomes, retry delays, duration, freshness, persistence outcome, and error category; credentials and database URLs are not logged.

### Data-quality limits and recovery

The regional endpoint returns a limited latest response (the official API documents regional forecasts up to two days ahead). Polling captures only what that endpoint returns at each retrieval time; it is not historical backfill and cannot guarantee a complete time series. When two successful interval boundaries leave one or more complete intervals unobserved, the worker reports the gap in status. It does not invent values or fill gaps. A repeated interval can still have a new raw evidence row because retrieval context is distinct; status exposes both `duplicate_interval` and raw/normalised insertion outcomes. Forecasts remain labelled `forecast`, including completed forecasts whose `source_fresh` is false.

For a source or validation failure, inspect the latest error and attempt history before restarting. A validation rejection is persisted with its raw JSON, SHA-256, retrieval time, and rejection reason; it is not converted into a successful observation. A database failure ends that collection cycle without retrying the write or silently claiming success; inspect PostgreSQL and the worker logs, restore database availability, then allow a later scheduled cycle or restart the collector. If graceful shutdown interrupts a retry backoff, the failed HTTP attempt remains recorded but its collection run stays `running` (incomplete) and is not counted as a completed failed cycle. An abrupt stop during a request may leave its attempt `in_progress` and the run `running`. `status` shows `collector_running=false` after the advisory lock is released. Restarting creates a new run and does not rewrite either interrupted record.

The collector is a local portfolio demonstration, not a production scheduler or uptime guarantee. No backfill, missed-run catch-up, automatic alerting, retention policy, database backup/restore, multi-region orchestration, or hosted deployment is implemented. Polling every 30 minutes is a conservative demonstration default aligned to the source's half-hour interval shape; it is configurable and does not imply complete historical coverage.

## Ingestion and idempotency

Ingestion is an explicit, one-shot operation. It makes one NESO request, captures the decoded source payload and retrieval timestamp, validates it with the existing regional parser, and persists the immutable raw evidence and normalised observation atomically. Rejected but JSON-serializable responses are preserved with a failed validation outcome and no normalised observation; persistence errors roll back all writes from that ingestion. Source, validation, and persistence failures have distinct error categories.

The payload is stored as JSONB for querying and as canonical UTF-8 JSON bytes for byte-level integrity verification. SHA-256 is calculated over those canonical bytes (sorted object keys, compact separators, UTF-8, no NaN/Infinity). This preserves the parsed JSON evidence, not the exact HTTP wire representation or original whitespace. Repeated submissions of the same source payload, region, and retrieval timestamp reuse the existing immutable raw row; a later retrieval is retained as a separate evidence event. Normalised rows have a database uniqueness constraint. Conflicting historical evidence is not updated in place.

Observations carry UTC-aware interval and retrieval timestamps, region, source and URL provenance, source hash through their raw-payload relationship, freshness/interval classification, actual-versus-forecast type, and schema version. A forecast remains explicitly classified as a forecast and is never converted to an actual observation.

## Offline replay

Replay reads a saved raw payload and retrieval context from the database through `replay_raw_payload`; it performs no network request. It verifies SHA-256 and that the JSONB value matches the preserved canonical bytes, then calls the same parser with the original region, source URL, and retrieval time. The parser's freshness bound is widened only for replay so that historically accepted evidence can be deterministically re-evaluated. Evidence with a stored `validation_error` is refused with that rejection reason; `validation_fresh=false` alone is not treated as rejection because an accepted completed forecast can be non-fresh. Replay does not invent absent intervals or reconstruct data that NESO did not return.

## Tests and migrations

Install development dependencies with `python -m pip install -r requirements-dev.txt`. Set `TEST_DATABASE_URL` to a disposable PostgreSQL database; each integration test applies the real Alembic migration to a uniquely named temporary schema, then drops that schema. Apply migrations to a separate database with `DATABASE_URL=... alembic upgrade head`, and check model/migration parity with `DATABASE_URL=... alembic check`. Run `TEST_DATABASE_URL=... python -m pytest -q`. Database integration tests require PostgreSQL and are not replaced by SQLite. CI provisions PostgreSQL, applies/checks migrations, then runs the integration suite against isolated schemas.

Migrations are versioned under `migrations/versions`. Review each migration before applying it to any persistent database. Never point development or CI commands at a production database.
