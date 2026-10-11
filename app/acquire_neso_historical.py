"""Acquire one bounded NESO regional forecast archive window.

The default is read-only: it prints a JSON report and does not touch PostgreSQL.
Use ``--persist`` only when explicit evidence-store writes are intended.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
import json
import sys
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import settings
from app.persistence.historical_forecast_repository import (
    HistoricalPersistenceResult,
    persist_historical_acquisition,
)
from app.services.neso_historical import (
    DATASET_API,
    DATASET_ID,
    DataPortalHistoricalClient,
    HistoricalSourceError,
    _validate_window,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region-id", type=int, default=13)
    parser.add_argument(
        "--from",
        dest="requested_start",
        required=True,
        help="UTC ISO-8601 start, inclusive",
    )
    parser.add_argument(
        "--to", dest="requested_end", required=True, help="UTC ISO-8601 end, exclusive"
    )
    parser.add_argument(
        "--persist",
        action="store_true",
        help="explicitly persist source evidence and forecast rows to PostgreSQL",
    )
    args = parser.parse_args(argv)
    try:
        start = _parse_datetime(args.requested_start, "--from")
        end = _parse_datetime(args.requested_end, "--to")
        _validate_window(args.region_id, start, end)
    except ValueError as exc:
        parser.error(str(exc))
    if args.persist and settings.database_url is None:
        parser.error(
            "--persist requires DATABASE_URL for an already migrated PostgreSQL database"
        )

    try:
        acquisition = DataPortalHistoricalClient().acquire(
            region_id=args.region_id,
            requested_start=start,
            requested_end=end,
        )
    except HistoricalSourceError as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "source_api": DATASET_API,
                    "source_dataset_id": DATASET_ID,
                    "region_id": args.region_id,
                    "requested_start": _iso(start),
                    "requested_end": _iso(end),
                    "error": str(exc),
                    "persisted": False,
                },
                sort_keys=True,
            )
        )
        return 2

    persisted: HistoricalPersistenceResult | None = None
    if args.persist:
        engine = create_engine(
            settings.database_url.get_secret_value(), pool_pre_ping=True
        )
        try:
            with Session(engine) as session, session.begin():
                persisted = persist_historical_acquisition(
                    session,
                    acquisition.report,
                    acquisition.payload,
                )
        finally:
            engine.dispose()

    output = {
        "status": "acquired",
        "dry_run": not args.persist,
        "persisted": asdict(persisted) if persisted is not None else None,
        "report": asdict(acquisition.report),
    }
    print(json.dumps(output, sort_keys=True, indent=2, default=_json_default))
    return 0


def _parse_datetime(value: str, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return parsed.astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _json_default(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, datetime):
        return _iso(value)
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


if __name__ == "__main__":
    sys.exit(main())
