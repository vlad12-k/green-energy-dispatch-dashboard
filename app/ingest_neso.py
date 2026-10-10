"""Explicit one-shot NESO evidence ingestion command."""
from __future__ import annotations

import json

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import settings
from app.persistence.ingestion_repository import SqlAlchemyNesoRepository
from app.services.neso_carbon_intensity import NesoCarbonIntensityClient
from app.services.neso_ingestion import ingest_neso


def main() -> None:
    """Fetch one NESO response and persist it when configured to do so."""
    if settings.database_url is None:
        raise SystemExit("DATABASE_URL must be configured to ingest NESO evidence")

    engine = create_engine(settings.database_url.get_secret_value(), pool_pre_ping=True)
    client = NesoCarbonIntensityClient(
        base_url=settings.neso_api_base_url,
        region_id=settings.neso_region_id,
        max_age_seconds=settings.neso_max_age_seconds,
        timeout_seconds=settings.neso_timeout_seconds,
    )
    try:
        with Session(engine) as session:
            result = ingest_neso(client, SqlAlchemyNesoRepository(session))
        print(json.dumps({
            "status": "inserted" if result.inserted else "already_present",
            "region_id": result.reading.region_id,
            "measurement_type": result.reading.measurement_type,
            "observed_at": result.reading.observed_at.isoformat(),
            "retrieved_at": result.reading.retrieved_at.isoformat(),
            "payload_sha256": result.payload_sha256,
        }, sort_keys=True))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
