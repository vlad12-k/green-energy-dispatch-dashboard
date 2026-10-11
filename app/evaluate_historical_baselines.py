"""Evaluate chronological persistence and seasonal-naive reference forecasts."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.historical_dataset import (
    assess_availability,
    chronological_split,
    evaluate_baselines,
    load_dataset,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region-id", type=int, default=settings.neso_region_id)
    parser.add_argument("--train-fraction", type=float, default=0.8)
    parser.add_argument(
        "--cutoff", help="UTC ISO-8601 timestamp for the train/test boundary"
    )
    parser.add_argument(
        "--horizons",
        default="1,2,4,48",
        help="Comma-separated half-hour interval horizons",
    )
    parser.add_argument("--seasonal-periods", type=int, default=48)
    parser.add_argument("--minimum-training-observations", type=int, default=96)
    args = parser.parse_args(argv)
    if settings.database_url is None:
        parser.error("Set DATABASE_URL to an already migrated PostgreSQL database")
    if args.region_id <= 0:
        parser.error("--region-id must be positive")
    try:
        horizons = tuple(int(item) for item in args.horizons.split(","))
        cutoff = (
            datetime.fromisoformat(args.cutoff.replace("Z", "+00:00"))
            if args.cutoff
            else None
        )
    except ValueError as exc:
        parser.error(str(exc))
    if cutoff is not None and (cutoff.tzinfo is None or cutoff.utcoffset() is None):
        parser.error("--cutoff must include a timezone")

    engine = create_engine(settings.database_url.get_secret_value(), pool_pre_ping=True)
    try:
        with Session(engine) as session:
            dataset = load_dataset(session, args.region_id)
    finally:
        engine.dispose()
    availability = assess_availability(dataset)
    split = chronological_split(
        dataset.actual_revision_evidence,
        train_fraction=args.train_fraction,
        cutoff=cutoff,
    )
    report = evaluate_baselines(
        split.train,
        split.test,
        horizons=horizons,
        seasonal_periods=args.seasonal_periods,
        minimum_training_observations=args.minimum_training_observations,
        dataset_origin=f"postgresql region {args.region_id}; dataset_sha256 {dataset.dataset_sha256}",
    )
    output = {
        "dataset_sha256": dataset.dataset_sha256,
        "availability": _jsonable(availability.__dict__),
        "evaluation": _jsonable(report.__dict__),
    }
    print(json.dumps(output, sort_keys=True, indent=2))
    return 0 if report.sufficient_data else 2


def _jsonable(value):
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


if __name__ == "__main__":
    sys.exit(main())
