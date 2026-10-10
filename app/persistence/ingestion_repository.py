"""PostgreSQL repository bridging ingestion records into immutable evidence."""
from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from typing import Any, Iterator

from sqlalchemy.orm import Session

from app.persistence.repository import CarbonRepository


class SqlAlchemyNesoRepository:
    """Adapt the ingestion transaction protocol to SQLAlchemy persistence."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = CarbonRepository(session)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self.session.begin():
            yield

    def insert_reading(self, record: Mapping[str, Any]) -> bool:
        raw = self.repository.store_payload(
            record["raw_payload"],
            region_id=int(record["region_id"]),
            source_url=str(record["source_url"]),
            retrieved_at=record["retrieved_at"],
            validation_fresh=bool(record["is_fresh"]),
            source_api=str(record["source_api"]),
        )
        _observation, inserted = self.repository.store_observation_with_status(
            raw, region_id=int(record["region_id"])
        )
        return inserted

    def insert_invalid_payload(self, record: Mapping[str, Any]) -> None:
        """Preserve rejected source evidence without creating an observation."""
        self.repository.store_payload(
            record["raw_payload"],
            region_id=int(record["region_id"]),
            source_url=str(record["source_url"]),
            retrieved_at=record["retrieved_at"],
            validation_fresh=False,
            validation_error=str(record["validation_error"]),
            source_api=str(record["source_api"]),
        )
