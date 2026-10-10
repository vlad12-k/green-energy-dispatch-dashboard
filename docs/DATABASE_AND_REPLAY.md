# PostgreSQL evidence storage and replay

This milestone adds an optional local PostgreSQL store for raw NESO regional responses and normalized carbon-intensity observations. The existing HTTP routes remain stateless and continue to work when `DATABASE_URL` is unset. No scheduler, production hosting, or automatic database migration is provided.

## Local setup

Requirements: Docker with Compose v2 and Python 3.12.

1. Copy `.env.example` to `.env`, add a strong local-only `POSTGRES_PASSWORD`, and set `DATABASE_URL` to `postgresql+psycopg://ged:<URL-encoded-password>@db:5432/ged` using the same password. Never use these local credentials for a hosted or production database.
2. Start only the opt-in database service with the separate overlay: `docker compose -f docker-compose.yml -f docker-compose.database.yml --profile database up -d db`. Configuration fails fast if `POSTGRES_PASSWORD` is unset. PostgreSQL binds to `127.0.0.1:54329` for local development and is not exposed on external interfaces; Compose services can use the internal `db:5432` address. The default API Compose file remains usable without the database overlay. Existing API container hardening remains in place.
3. Apply schema changes explicitly from the repository root: `DATABASE_URL=postgresql+psycopg://ged:<URL-encoded-password>@127.0.0.1:54329/ged alembic upgrade head`. Migrations are never run automatically at application startup.
4. For a one-shot host-side ingestion, use a host-reachable local URL: `DATABASE_URL=postgresql+psycopg://ged:<URL-encoded-password>@127.0.0.1:54329/ged python -m app.ingest_neso`. The command makes one request and emits a JSON result with insertion status and provenance. It is not run automatically by the API. From a Compose container, use `db:5432` instead of the loopback URL.
5. Replay a stored raw payload by its database ID without any NESO request: `DATABASE_URL=postgresql+psycopg://ged:<URL-encoded-password>@127.0.0.1:54329/ged python -m app.replay_neso <raw-payload-id>`.
6. Run the API when needed with `docker compose up --build api`. The database can remain stopped if persistence is not in use.
7. Stop the database with `docker compose -f docker-compose.yml -f docker-compose.database.yml --profile database down`. Persistent local data remains in the named `postgres_data` volume. To remove that local data deliberately, add `--volumes`; this is destructive and is not part of normal cleanup.

The PostgreSQL image is development-only. This project does not provide production database hosting, backup/restore, high availability, retention, access-management, or operational monitoring.

## Ingestion and idempotency

Ingestion is an explicit, one-shot operation. It makes one NESO request, captures the decoded source payload and retrieval timestamp, validates it with the existing regional parser, and persists the immutable raw evidence and normalized observation atomically. Rejected but JSON-serializable responses are preserved with a failed validation outcome and no normalized observation; persistence errors roll back all writes from that ingestion. Source, validation, and persistence failures have distinct error categories.

The payload is stored as JSONB for querying and as canonical UTF-8 JSON bytes for byte-level integrity verification. SHA-256 is calculated over those canonical bytes (sorted object keys, compact separators, UTF-8, no NaN/Infinity). This preserves the parsed JSON evidence, not the exact HTTP wire representation or original whitespace. Repeated submissions of the same source payload, region, and retrieval timestamp reuse the existing immutable raw row; a later retrieval is retained as a separate evidence event. Normalized rows have a database uniqueness constraint. Conflicting historical evidence is not updated in place.

Observations carry UTC-aware interval and retrieval timestamps, region, source and URL provenance, source hash through their raw-payload relationship, freshness/interval classification, actual-versus-forecast type, and schema version. A forecast remains explicitly classified as a forecast and is never converted to an actual observation.

## Offline replay

Replay reads a saved raw payload and retrieval context from the database through `replay_raw_payload`; it performs no network request. It verifies SHA-256 and that the JSONB value matches the preserved canonical bytes, then calls the same parser with the original region, source URL, and retrieval time. The parser's freshness bound is widened only for replay so that historical evidence can be deterministically re-evaluated without falsely treating it as a current live reading. Replay does not invent absent intervals or reconstruct data that NESO did not return.

## Tests and migrations

Install development dependencies with `python -m pip install -r requirements-dev.txt`. Set `TEST_DATABASE_URL` to a disposable PostgreSQL database; each integration test applies the real Alembic migration to a uniquely named temporary schema, then drops that schema. Apply migrations to a separate database with `DATABASE_URL=... alembic upgrade head`, and check model/migration parity with `DATABASE_URL=... alembic check`. Run `TEST_DATABASE_URL=... python -m pytest -q`. Database integration tests require PostgreSQL and are not replaced by SQLite. CI provisions PostgreSQL, applies/checks migrations, then runs the integration suite against isolated schemas.

Migrations are versioned under `migrations/versions`. Review each migration before applying it to any persistent database. Never point development or CI commands at a production database.
