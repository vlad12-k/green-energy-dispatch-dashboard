"""Replay a persisted NESO source payload without network access."""
from __future__ import annotations

import argparse
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import settings
from app.persistence.models import RawPayload
from app.persistence.repository import replay_raw_payload


def main() -> None:
    """Load one raw record and emit its deterministic parser result."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw_payload_id", type=int, help="Database ID of the stored raw payload")
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
            reading = replay_raw_payload(raw)
        print(json.dumps({
            "payload_sha256": raw.payload_sha256,
            "intensity_gco2_per_kwh": reading.intensity_gco2_per_kwh,
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
        }, sort_keys=True))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
