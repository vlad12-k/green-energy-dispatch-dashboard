"""Build an auditable regional CSV dataset and availability JSON report."""

from __future__ import annotations

import argparse
import csv
import json
import sys
import tempfile
from collections.abc import Iterable
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.historical_dataset import (
    DatasetObservation,
    assess_availability,
    load_dataset,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region-id", type=int, default=settings.neso_region_id)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--minimum-contiguous-intervals", type=int, default=96)
    args = parser.parse_args(argv)
    if settings.database_url is None:
        parser.error("Set DATABASE_URL to an already migrated PostgreSQL database")
    if args.region_id <= 0:
        parser.error("--region-id must be positive")
    engine = create_engine(settings.database_url.get_secret_value(), pool_pre_ping=True)
    try:
        with Session(engine) as session:
            dataset = load_dataset(session, args.region_id)
            availability = assess_availability(
                dataset,
                minimum_contiguous_intervals=args.minimum_contiguous_intervals,
            )
    finally:
        engine.dispose()

    _write_csv((*dataset.observations, *dataset.forecast_observations), args.output)

    manifest = {
        "region_id": dataset.region_id,
        "units": dataset.units,
        "units_by_source": dataset.units_by_source,
        "unit_counts": dataset.unit_counts,
        "dataset_sha256": dataset.dataset_sha256,
        "observation_count": len(dataset.observations)
        + len(dataset.forecast_observations),
        "actual_observation_count": len(dataset.observations),
        "forecast_observation_count": dataset.forecast_count,
        "rejected_observation_count": dataset.rejected_observation_count,
        "duplicate_observation_count": dataset.duplicate_observation_count,
        "conflicting_revision_count": dataset.conflicting_revision_count,
        "missing_interval_count": len(dataset.missing_intervals),
        "selection_rule": dataset.selection_rule,
        "availability": {
            **availability.__dict__,
            "first_interval_start": _iso_or_none(availability.first_interval_start),
            "last_interval_end": _iso_or_none(availability.last_interval_end),
            "units": dataset.units,
            "units_by_source": dataset.units_by_source,
            "unit_counts": dataset.unit_counts,
        },
        "csv_path": str(args.output),
    }
    print(json.dumps(manifest, sort_keys=True, indent=2, default=_json_default))
    return 0


def _write_csv(rows: Iterable[DatasetObservation], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fields = (
        "region_id",
        "region_name",
        "observed_at",
        "valid_until",
        "retrieved_at",
        "measurement_type",
        "intensity_gco2_per_kwh",
        "units",
        "source_api",
        "source_url",
        "payload_sha256",
        "raw_payload_id",
        "accepted",
    )
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            newline="",
            encoding="utf-8",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as output:
            temporary_path = Path(output.name)
            writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            export_rows = sorted(
                rows,
                key=lambda row: (
                    row.observed_at,
                    row.measurement_type,
                    row.retrieved_at,
                    row.payload_sha256,
                    row.raw_payload_id,
                    row.region_id,
                    row.region_name,
                    row.valid_until,
                    row.intensity_gco2_per_kwh,
                    row.source_api,
                    row.source_url,
                    row.accepted,
                ),
            )
            for row in export_rows:
                writer.writerow(_observation_record(row))
        temporary_path.replace(output_path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _observation_record(row) -> dict[str, object]:
    return {
        "region_id": row.region_id,
        "region_name": row.region_name,
        "observed_at": _iso_or_none(row.observed_at),
        "valid_until": _iso_or_none(row.valid_until),
        "retrieved_at": _iso_or_none(row.retrieved_at),
        "measurement_type": row.measurement_type,
        "intensity_gco2_per_kwh": row.intensity_gco2_per_kwh,
        "units": row.units,
        "source_api": row.source_api,
        "source_url": row.source_url,
        "payload_sha256": row.payload_sha256,
        "raw_payload_id": row.raw_payload_id,
        "accepted": row.accepted,
    }


def _iso_or_none(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z") if value else None


def _json_default(value):
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, datetime):
        return _iso_or_none(value)
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


if __name__ == "__main__":
    sys.exit(main())
