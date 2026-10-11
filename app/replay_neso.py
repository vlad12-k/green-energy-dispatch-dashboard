"""Replay a persisted NESO source payload without network access."""

from __future__ import annotations

import argparse
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import settings
from app.persistence.historical_forecast_repository import (
    replay_historical_forecast_record,
)
from app.persistence.models import RawPayload
from app.persistence.repository import replay_raw_payload
from app.services.neso_historical import DATASET_API


def main() -> None:
    """Load one raw record and emit its deterministic parser result."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "raw_payload_id", type=int, help="Database ID of the stored raw payload"
    )
    parser.add_argument(
        "--source-record-id",
        type=int,
        help="required for a historical DataStore page; identifies the source row to replay",
    )
    args = parser.parse_args()
    if args.raw_payload_id < 1:
        parser.error("raw_payload_id must be positive")
    if settings.database_url is None:
        raise SystemExit("DATABASE_URL must be configured to replay NESO evidence")

    engine = create_engine(settings.database_url.get_secret_value(), pool_pre_ping=True)
    try:
        with Session(engine) as session:
            raw = session.get(RawPayload, args.raw_payload_id)
            if raw is None:
                raise SystemExit("No raw NESO payload exists with that ID")
            if raw.source_api == DATASET_API:
                if args.source_record_id is None:
                    parser.error(
                        "--source-record-id is required for historical forecast archive payloads"
                    )
                reading = replay_historical_forecast_record(
                    raw,
                    source_record_id=args.source_record_id,
                )
            else:
                if args.source_record_id is not None:
                    parser.error(
                        "--source-record-id is only valid for historical forecast archive payloads"
                    )
                reading = replay_raw_payload(raw)
        print(
            json.dumps(
                {
                    "payload_sha256": raw.payload_sha256,
                    "intensity_gco2_per_kwh": reading.intensity_gco2_per_kwh,
                    "units": reading.units,
                    "measurement_type": reading.measurement_type,
                    "region_id": reading.region_id,
                    "region_name": reading.region_name,
                    "observed_at": reading.observed_at.isoformat(),
                    "valid_until": reading.valid_until.isoformat(),
                    "retrieved_at": reading.retrieved_at.isoformat(),
                    "interval_status": reading.interval_status,
                    "is_fresh": reading.is_fresh,
                    "source_api": reading.source_api,
                    "source_url": reading.source_url,
                    "source_record_id": args.source_record_id,
                },
                sort_keys=True,
            )
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
