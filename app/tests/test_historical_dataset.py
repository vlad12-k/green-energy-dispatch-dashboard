from datetime import UTC, datetime, timedelta
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import csv
import json
import threading

import pytest
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.services.historical_dataset import (
    DatasetObservation,
    assess_availability,
    build_dataset,
    evaluate_baselines,
    chronological_split,
)
from app.persistence.repository import CarbonRepository
from app.services.neso_carbon_intensity import SOURCE_API
import app.build_historical_dataset as dataset_builder
from app.build_historical_dataset import main as build_dataset_cli
from app.evaluate_historical_baselines import main as evaluate_baselines_cli


def observation(
    start: datetime,
    value: float,
    *,
    retrieved: datetime | None = None,
    kind: str = "actual",
    payload_hash: str | None = None,
    accepted: bool = True,
) -> DatasetObservation:
    return DatasetObservation(
        region_id=13,
        region_name="London",
        observed_at=start,
        valid_until=start + timedelta(minutes=30),
        retrieved_at=retrieved or start + timedelta(minutes=30),
        measurement_type=kind,
        intensity_gco2_per_kwh=value,
        source_api="neso-carbon-intensity-api",
        source_url="https://api.carbonintensity.org.uk/regional/regionid/13",
        payload_sha256=payload_hash or f"{int(value):064x}",
        raw_payload_id=int(value) + 1,
        accepted=accepted,
    )


def test_dataset_selects_latest_actual_revision_and_preserves_retrieval_provenance() -> (
    None
):
    start = datetime(2026, 1, 1, tzinfo=UTC)
    older = observation(
        start, 40, retrieved=start + timedelta(hours=1), payload_hash="a" * 64
    )
    newer = observation(
        start, 42, retrieved=start + timedelta(hours=2), payload_hash="b" * 64
    )
    forecast = observation(
        start, 55, retrieved=start + timedelta(minutes=1), kind="forecast"
    )
    rejected = observation(start + timedelta(minutes=30), 30, accepted=False)

    dataset = build_dataset([older, newer, forecast, rejected], region_id=13)

    assert [
        (row.intensity_gco2_per_kwh, row.measurement_type)
        for row in dataset.observations
    ] == [(42, "actual")]
    assert dataset.observations[0].payload_sha256 == "b" * 64
    assert dataset.observations[0].retrieved_at == start + timedelta(hours=2)
    assert dataset.conflicting_revision_count == 1
    assert dataset.rejected_observation_count == 1
    assert dataset.forecast_count == 1
    assert dataset.forecast_observations == (forecast,)
    assert len(dataset.actual_revision_evidence) == 2
    assert '"payload_sha256":"' + "b" * 64 + '"' in dataset.to_json()


def test_dataset_is_deterministic_flags_gaps_and_availability_reports_contiguous_span() -> (
    None
):
    start = datetime(2026, 1, 1, tzinfo=UTC)
    records = [
        observation(start, 40),
        observation(start + timedelta(hours=1), 50),
        observation(start + timedelta(minutes=30), 44, kind="forecast"),
    ]

    left = build_dataset(records, region_id=13)
    right = build_dataset(reversed(records), region_id=13)
    report = assess_availability(left)

    assert left.to_json() == right.to_json()
    assert left.missing_intervals == (start + timedelta(minutes=30),)
    assert report.actual_observation_count == 2
    assert report.forecast_observation_count == 1
    assert report.forecast_coverage_start == start + timedelta(minutes=30)
    assert report.forecast_coverage_end == start + timedelta(hours=1)
    assert report.contiguous_actual_segment_count == 2
    assert report.contiguous_actual_intervals == 2
    assert report.suitable_for_evaluation is False
    assert report.longest_contiguous_actual_intervals == 1
    assert [
        (period.start_at, period.end_at, period.interval_count)
        for period in report.contiguous_actual_periods
    ] == [
        (start, start + timedelta(minutes=30), 1),
        (start + timedelta(hours=1), start + timedelta(hours=1, minutes=30), 1),
    ]


def test_availability_counts_actual_contiguous_segments_not_missing_bins() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    dataset = build_dataset(
        [
            observation(start, 40),
            observation(start + timedelta(hours=3), 50),
            observation(start + timedelta(hours=6), 60),
        ],
        region_id=13,
    )

    report = assess_availability(dataset)

    assert report.missing_interval_count == 10
    assert report.contiguous_actual_segment_count == 3


def test_dataset_rejects_intervals_not_aligned_to_configured_utc_grid() -> None:
    row = observation(datetime(2026, 1, 1, 0, 5, tzinfo=UTC), 40)

    with pytest.raises(ValueError, match="interval grid"):
        build_dataset([row], region_id=13)


def test_dataset_rejects_mixed_or_naive_region_timestamps() -> None:
    with pytest.raises(ValueError, match="region"):
        build_dataset([observation(datetime(2026, 1, 1, tzinfo=UTC), 10)], region_id=14)

    naive = observation(datetime(2026, 1, 1, tzinfo=UTC), 10)
    object.__setattr__(naive, "observed_at", datetime(2026, 1, 1))
    with pytest.raises(ValueError, match="timezone"):
        build_dataset([naive], region_id=13)


def test_chronological_split_and_persistence_baseline_prevent_future_leakage() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    actuals = [
        observation(start + i * timedelta(minutes=30), 100 + i) for i in range(8)
    ]
    split = chronological_split(actuals, train_fraction=0.5)

    report = evaluate_baselines(
        split.train,
        split.test,
        horizons=(1,),
        seasonal_periods=4,
        minimum_training_observations=3,
    )

    assert split.train[-1].observed_at < split.test[0].observed_at
    persistence = next(row for row in report.results if row.method == "persistence")
    assert persistence.sample_count == 3
    assert persistence.mae == 2.0
    assert persistence.first_target_at == split.test[0].observed_at + timedelta(
        minutes=30
    )
    assert report.dataset_origin == "provided observations"


def test_fraction_split_keeps_both_partitions_nonempty_for_small_valid_dataset() -> (
    None
):
    start = datetime(2026, 1, 1, tzinfo=UTC)
    data = [observation(start + i * timedelta(minutes=30), i) for i in range(4)]

    split = chronological_split(data, train_fraction=0.1)

    assert len({row.observed_at for row in split.train}) == 1
    assert len({row.observed_at for row in split.test}) == 3


def test_horizon_origins_before_holdout_period_do_not_produce_metrics() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    actuals = [observation(start + i * timedelta(minutes=30), i) for i in range(6)]

    report = evaluate_baselines(
        actuals[:4],
        actuals[4:],
        horizons=(2,),
        minimum_training_observations=4,
    )

    assert report.sufficient_data is False
    assert report.results == ()
    assert report.insufficient_reason is not None


def test_persistence_excludes_an_actual_interval_not_finished_at_forecast_origin() -> (
    None
):
    start = datetime(2026, 1, 1, tzinfo=UTC)
    train = [
        observation(start, 1),
        observation(start + timedelta(minutes=30), 2),
        observation(
            start + timedelta(hours=1),
            99,
            retrieved=start + timedelta(hours=1, minutes=31),
        ),
    ]
    test = [
        observation(start + timedelta(hours=1, minutes=30), 100),
        observation(start + timedelta(hours=2), 100),
    ]

    report = evaluate_baselines(
        train,
        test,
        horizons=(1,),
        minimum_training_observations=2,
    )

    persistence = next(row for row in report.results if row.method == "persistence")
    assert persistence.sample_count == 1
    assert persistence.mae == 98


def test_evaluation_rejects_actual_rows_with_non_half_hour_durations() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    data = [
        replace(
            observation(start + i * timedelta(minutes=30), 10 + i),
            valid_until=start + i * timedelta(minutes=30) + timedelta(minutes=15),
        )
        for i in range(3)
    ]

    report = evaluate_baselines(
        data[:2],
        data[2:],
        horizons=(1,),
        minimum_training_observations=2,
    )

    assert report.sufficient_data is False
    assert report.results == ()
    assert report.insufficient_reason is not None
    assert "half-hourly" in report.insufficient_reason


def test_availability_rejects_dataset_with_inconsistent_interval_duration() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    dataset = build_dataset([observation(start, 10)], region_id=13)
    malformed = replace(
        dataset,
        observations=(
            replace(dataset.observations[0], valid_until=start + timedelta(minutes=15)),
        ),
    )

    with pytest.raises(ValueError, match="interval duration"):
        assess_availability(malformed)


def test_evaluation_reports_insufficient_when_training_history_contains_a_gap() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    train = [
        observation(start, 10),
        observation(start + timedelta(hours=1), 20),
    ]
    test = [observation(start + timedelta(hours=1, minutes=30), 30)]

    report = evaluate_baselines(
        train,
        test,
        horizons=(1,),
        minimum_training_observations=2,
    )

    assert report.sufficient_data is False
    assert report.results == ()
    assert report.insufficient_reason is not None
    assert "contiguous" in report.insufficient_reason


def test_evaluation_reports_insufficient_for_gap_at_train_test_boundary() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    train = [observation(start, 10), observation(start + timedelta(minutes=30), 20)]
    test = [observation(start + timedelta(minutes=90), 30)]

    report = evaluate_baselines(
        train,
        test,
        horizons=(1,),
        minimum_training_observations=2,
    )

    assert report.sufficient_data is False
    assert report.results == ()
    assert report.insufficient_reason is not None
    assert "contiguous" in report.insufficient_reason


def test_chronological_split_keeps_revisions_together_and_splits_unique_intervals() -> (
    None
):
    start = datetime(2026, 1, 1, tzinfo=UTC)
    records = [
        observation(start, 10, retrieved=start + timedelta(hours=1)),
        observation(
            start,
            12,
            retrieved=start + timedelta(hours=2),
            payload_hash="c" * 64,
        ),
        *(observation(start + i * timedelta(minutes=30), 20 + i) for i in range(1, 4)),
    ]

    split = chronological_split(records, train_fraction=0.5)

    assert len(split.train) == 3
    assert len({row.observed_at for row in split.train}) == 2
    assert [row.intensity_gco2_per_kwh for row in split.train] == [10, 12, 21]
    assert len(split.test) == 2
    assert split.split_at == start + timedelta(hours=1)


def test_evaluation_uses_latest_available_revision_without_duplicate_samples() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    train = [
        observation(start, 1, retrieved=start + timedelta(minutes=10)),
        observation(
            start,
            2,
            retrieved=start + timedelta(minutes=70),
            payload_hash="c" * 64,
        ),
    ]
    test = [observation(start + i * timedelta(minutes=30), 10 + i) for i in range(1, 4)]

    report = evaluate_baselines(
        train,
        test,
        horizons=(2,),
        minimum_training_observations=1,
    )

    persistence = next(row for row in report.results if row.method == "persistence")
    assert persistence.sample_count == 1
    assert persistence.first_target_at == start + timedelta(minutes=90)
    assert persistence.mae == 12


def test_seasonal_naive_uses_only_available_prior_season_and_skips_gaps() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    train = [observation(start + i * timedelta(minutes=30), i * 10) for i in range(8)]
    test = [
        observation(start + 8 * timedelta(minutes=30), 40),
        observation(start + 9 * timedelta(minutes=30), 50),
    ]

    report = evaluate_baselines(
        train,
        test,
        horizons=(1,),
        seasonal_periods=4,
        minimum_training_observations=4,
    )

    seasonal = next(row for row in report.results if row.method == "seasonal_naive")
    assert seasonal.sample_count == 1
    assert seasonal.first_target_at == test[1].observed_at
    assert seasonal.mae == 0
    assert seasonal.skipped_missing_input_count == 0


def test_insufficient_availability_does_not_claim_contiguous_model_period() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    data = [
        observation(start, 40),
        observation(start + timedelta(hours=1), 50),
        observation(start + timedelta(hours=2), 60),
    ]

    split = chronological_split(data, train_fraction=2 / 3)
    report = evaluate_baselines(
        split.train,
        split.test,
        horizons=(1,),
        minimum_training_observations=2,
    )

    assert report.sufficient_data is False
    assert report.results == ()
    assert report.insufficient_reason


def test_evaluation_is_explicitly_insufficient_when_real_actuals_are_too_few() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    data = [observation(start, 25)]
    split = chronological_split(data)

    report = evaluate_baselines(
        split.train,
        split.test,
        horizons=(1,),
        minimum_training_observations=2,
    )

    assert report.sufficient_data is False
    assert report.results == ()
    assert report.insufficient_reason


def test_postgres_repository_exports_only_validated_normalised_evidence(
    postgres_engine,
) -> None:
    retrieved = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    actual_payload = {
        "data": [
            {
                "regionid": 13,
                "shortname": "London",
                "data": [
                    {
                        "from": "2026-01-01T11:00:00Z",
                        "to": "2026-01-01T11:30:00Z",
                        "intensity": {"actual": 40, "forecast": 42, "index": "low"},
                    }
                ],
            }
        ],
    }
    forecast_payload = {
        "data": [
            {
                "regionid": 13,
                "shortname": "London",
                "data": [
                    {
                        "from": "2026-01-01T11:30:00Z",
                        "to": "2026-01-01T12:00:00Z",
                        "intensity": {"forecast": 48, "index": "low"},
                    }
                ],
            }
        ],
    }
    rejected_payload = {"data": [{"regionid": 13, "shortname": "London", "data": []}]}

    with Session(postgres_engine) as session:
        repository = CarbonRepository(session)
        for payload, freshness, error in (
            (actual_payload, True, None),
            (forecast_payload, True, None),
            (rejected_payload, False, "invalid regional intervals"),
        ):
            with session.begin():
                raw = repository.store_payload(
                    payload,
                    region_id=13,
                    source_url="https://api.carbonintensity.org.uk/regional/regionid/13",
                    retrieved_at=retrieved,
                    validation_fresh=freshness,
                    validation_error=error,
                    source_api=SOURCE_API,
                )
                if error is None:
                    repository.store_observation(raw, region_id=13)

        from app.services.historical_dataset import load_dataset

        dataset = load_dataset(session, 13)

    assert len(dataset.observations) == 1
    assert dataset.observations[0].measurement_type == "actual"
    assert dataset.observations[0].payload_sha256
    assert dataset.forecast_count == 1
    assert dataset.rejected_observation_count == 1


def test_postgres_clis_report_empty_dataset_without_claiming_evaluation(
    postgres_engine, monkeypatch, capsys, tmp_path
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "database_url", SecretStr("postgresql://unused"))
    monkeypatch.setattr(
        "app.build_historical_dataset.create_engine",
        lambda *_args, **_kwargs: postgres_engine,
    )
    monkeypatch.setattr(
        "app.evaluate_historical_baselines.create_engine",
        lambda *_args, **_kwargs: postgres_engine,
    )
    csv_path = tmp_path / "exports" / "region-13.csv"

    assert build_dataset_cli(["--region-id", "13", "--output", str(csv_path)]) == 0
    build_result = json.loads(capsys.readouterr().out)
    assert build_result["observation_count"] == 0
    assert build_result["availability"]["suitable_for_evaluation"] is False
    assert csv_path.read_text(encoding="utf-8").splitlines()[0].startswith("region_id,")

    result = evaluate_baselines_cli(["--region-id", "13"])
    evaluation_output = json.loads(capsys.readouterr().out)
    assert result == 2
    assert evaluation_output["availability"]["actual_observation_count"] == 0
    assert evaluation_output["evaluation"]["sufficient_data"] is False
    assert evaluation_output["evaluation"]["results"] == []


def test_concurrent_csv_exports_to_one_path_are_atomic_and_distinct(
    tmp_path, monkeypatch
) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    first_rows = [
        observation(start + i * timedelta(minutes=30), i + 1) for i in range(2)
    ]
    second_rows = [
        observation(start + i * timedelta(minutes=30), i + 101) for i in range(2)
    ]
    output_path = tmp_path / "same.csv"
    barrier = threading.Barrier(2)
    writer_state = threading.local()
    original_writerow = csv.DictWriter.writerow

    def synchronized_writerow(writer, rowdict):
        if not getattr(writer_state, "started", False):
            writer_state.started = True
            barrier.wait(timeout=5)
        return original_writerow(writer, rowdict)

    monkeypatch.setattr(csv.DictWriter, "writerow", synchronized_writerow)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(
            pool.map(
                lambda rows: dataset_builder._write_csv(rows, output_path),
                (first_rows, second_rows),
            )
        )

    with output_path.open(newline="", encoding="utf-8") as output:
        values = {
            float(row["intensity_gco2_per_kwh"]) for row in csv.DictReader(output)
        }
    assert values in ({1.0, 2.0}, {101.0, 102.0})
    assert list(tmp_path.glob(".same.csv.*.tmp")) == []
