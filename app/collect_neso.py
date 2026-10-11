"""Standalone entry point for the opt-in NESO regional collector."""
from __future__ import annotations

import argparse
import json
import signal
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from threading import Event

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import setup_logging
from app.persistence.collector_repository import NesoCollectorStateRepository
from app.persistence.ingestion_repository import SqlAlchemyNesoRepository
from app.services.neso_carbon_intensity import NesoCarbonIntensityClient
from app.services.neso_collector import CollectorConfig, NesoCollector
from app.services.neso_ingestion import ingest_neso

_LOCK_NAMESPACE = 0x474544


def _database_engine() -> Engine:
    if settings.database_url is None:
        raise SystemExit("Set DATABASE_URL before running or inspecting the collector.")
    return create_engine(settings.database_url.get_secret_value(), pool_pre_ping=True)


def _check_schema(engine: Engine) -> None:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1 FROM neso_collector_runs LIMIT 0"))
            connection.execute(text("SELECT 1 FROM neso_collector_attempts LIMIT 0"))
    except SQLAlchemyError as exc:
        raise SystemExit(
            "Collector database is unavailable or its explicit Alembic migrations are not applied."
        ) from exc


def _try_advisory_lock(connection: Connection, region_id: int) -> bool:
    acquired = connection.execute(
        text("SELECT pg_try_advisory_lock(:namespace, :region_id)"),
        {"namespace": _LOCK_NAMESPACE, "region_id": region_id},
    ).scalar_one()
    connection.commit()
    return bool(acquired)


def _release_advisory_lock(connection: Connection, region_id: int) -> None:
    connection.execute(
        text("SELECT pg_advisory_unlock(:namespace, :region_id)"),
        {"namespace": _LOCK_NAMESPACE, "region_id": region_id},
    )
    connection.commit()


@contextmanager
def _collector_lock(engine: Engine, region_id: int) -> Iterator[Connection]:
    with engine.connect() as connection:
        if not _try_advisory_lock(connection, region_id):
            raise SystemExit(f"A NESO collector already holds the lock for region {region_id}.")
        try:
            yield connection
        finally:
            _release_advisory_lock(connection, region_id)


def _collector_is_running(engine: Engine, region_id: int) -> bool:
    with engine.connect() as connection:
        acquired = _try_advisory_lock(connection, region_id)
        if not acquired:
            return True
        _release_advisory_lock(connection, region_id)
        return False


def _run_collector(engine: Engine) -> None:
    if not settings.collector_enabled:
        raise SystemExit("Collector is opt-in; set COLLECTOR_ENABLED=true to run it.")
    _check_schema(engine)
    setup_logging(json_output=True)
    client = NesoCarbonIntensityClient(
        base_url=settings.neso_api_base_url,
        region_id=settings.neso_region_id,
        max_age_seconds=settings.neso_max_age_seconds,
        timeout_seconds=settings.neso_timeout_seconds,
    )
    state_store = NesoCollectorStateRepository(engine)

    def ingest_once():
        with Session(engine) as session:
            return ingest_neso(client, SqlAlchemyNesoRepository(session))

    collector = NesoCollector(
        config=CollectorConfig(
            region_id=settings.neso_region_id,
            poll_interval_seconds=settings.collector_interval_seconds,
            max_attempts=settings.collector_max_attempts,
            retry_base_seconds=settings.collector_backoff_base_seconds,
            retry_max_seconds=settings.collector_backoff_max_seconds,
        ),
        ingest_once=ingest_once,
        state_store=state_store,
    )
    stop_event = Event()
    previous_handlers = {
        sig: signal.signal(sig, lambda _signum, _frame: stop_event.set())
        for sig in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        with _collector_lock(engine, settings.neso_region_id):
            collector.run(stop_event)
    finally:
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)


def _print_status(engine: Engine) -> None:
    _check_schema(engine)
    status = NesoCollectorStateRepository(engine).status(settings.neso_region_id)
    status["collector_running"] = _collector_is_running(engine, settings.neso_region_id)
    print(json.dumps(status, sort_keys=True, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Opt-in NESO regional data collector")
    parser.add_argument("command", choices=("run", "status"))
    args = parser.parse_args(argv)
    if args.command == "run" and not settings.collector_enabled:
        raise SystemExit("Collector is opt-in; set COLLECTOR_ENABLED=true to run it.")
    engine = _database_engine()
    try:
        if args.command == "run":
            _run_collector(engine)
        else:
            _print_status(engine)
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
