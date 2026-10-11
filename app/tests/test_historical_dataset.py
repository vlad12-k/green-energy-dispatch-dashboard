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
from app.persistence.historical_forecast_repository import (
    persist_historical_acquisition,
)
from app.services.neso_carbon_intensity import SOURCE_API
from app.services.neso_historical import DATASET_API, parse_historical_response
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
    source_api: str = SOURCE_API,
) -> DatasetObservation:
    return DatasetObservation(
        region_id=13,
        region_name="London",
        observed_at=start,
        valid_until=start + timedelta(minutes=30),
        retrieved_at=retrieved or start + timedelta(minutes=30),
        measurement_type=kind,
        intensity_gco2_per_kwh=value,
        source_api=source_api,
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


def test_evaluation_reports_naive_observation_timestamps_as_unavailable() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    naive = replace(
        observation(start, 10),
        observed_at=datetime(2026, 1, 1),
        valid_until=datetime(2026, 1, 1, 0, 30),
        retrieved_at=datetime(2026, 1, 1, 0, 30),
    )

    report = evaluate_baselines(
        [naive],
        [observation(start + timedelta(hours=1), 20)],
        horizons=(1, 4),
    )

    assert report.sufficient_data is False
    assert report.results == ()
    assert len(report.unavailable_evaluations) == 4
    assert all("timezone" in item.reason for item in report.unavailable_evaluations)


def test_evaluation_reports_overlapping_train_test_periods_as_unavailable() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    shared = observation(start, 10)

    report = evaluate_baselines([shared], [shared], horizons=(1, 2))

    assert report.sufficient_data is False
    assert report.results == ()
    assert len(report.unavailable_evaluations) == 4
    assert all("overlap" in item.reason for item in report.unavailable_evaluations)


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


def test_seasonal_and_persistence_baselines_use_canonical_revision_tie_breaks() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    tied_at = start + timedelta(hours=2, minutes=30)
    retrieved = start + timedelta(hours=3)
    revision_a = observation(tied_at, 50, retrieved=retrieved, payload_hash="a" * 64)
    revision_c = observation(tied_at, 20, retrieved=retrieved, payload_hash="c" * 64)
    train = [
        *(observation(start + i * timedelta(minutes=30), i * 10) for i in range(5)),
        revision_a,
        revision_c,
    ]
    test = [
        observation(start + timedelta(hours=3), 66),
        observation(start + timedelta(hours=3, minutes=30), 75),
    ]

    dataset = build_dataset((*train, *test), region_id=13)
    report = evaluate_baselines(
        train,
        test,
        horizons=(1,),
        seasonal_periods=2,
        minimum_training_observations=3,
    )

    assert dataset.observations[5].payload_sha256 == "c" * 64
    assert dataset.observations[5].intensity_gco2_per_kwh == 20
    assert {result.method: result.mae for result in report.results} == {
        "persistence": 55,
        "seasonal_naive": 55,
    }


def test_report_marks_each_unavailable_horizon_and_method_while_preserving_results() -> (
    None
):
    start = datetime(2026, 1, 1, tzinfo=UTC)
    train = [observation(start + i * timedelta(minutes=30), i) for i in range(3)]
    test = [observation(start + i * timedelta(minutes=30), 10 + i) for i in range(3, 7)]

    report = evaluate_baselines(
        train,
        test,
        horizons=(1, 10),
        seasonal_periods=4,
        minimum_training_observations=2,
    )

    assert report.sufficient_data is False
    assert report.requested_horizons == (1, 10)
    assert report.requested_methods == ("persistence", "seasonal_naive")
    assert report.evaluated_horizons == (1,)
    assert report.evaluated_methods == ("persistence",)
    assert [(result.method, result.horizon_intervals) for result in report.results] == [
        ("persistence", 1)
    ]
    unavailable = {
        (item.method, item.horizon_intervals): item.reason
        for item in report.unavailable_evaluations
    }
    assert set(unavailable) == {
        ("seasonal_naive", 1),
        ("persistence", 10),
        ("seasonal_naive", 10),
    }
    assert all(unavailable.values())


def test_one_sample_metrics_are_retained_but_not_reported_as_sufficient() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    train = [observation(start + i * timedelta(minutes=30), i) for i in range(8)]
    test = [observation(start + i * timedelta(minutes=30), 10 + i) for i in (8, 9)]

    report = evaluate_baselines(
        train,
        test,
        horizons=(1,),
        seasonal_periods=4,
        minimum_training_observations=4,
    )

    assert report.sufficient_data is False
    assert len(report.results) == 2
    assert report.evaluated_horizons == ()
    assert len(report.insufficient_sample_evaluations) == 2
    assert all(
        result.sample_count == 1 for result in report.insufficient_sample_evaluations
    )
    assert all(
        result.minimum_sample_count == 2
        for result in report.insufficient_sample_evaluations
    )

    configured = evaluate_baselines(
        train,
        test,
        horizons=(1,),
        seasonal_periods=4,
        minimum_training_observations=4,
        minimum_evaluation_samples=1,
    )
    assert configured.sufficient_data is True


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
    assert dataset.observations[0].units == "gCO2e/kWh"
    assert dataset.forecast_count == 1
    assert dataset.forecast_observations[0].units == "gCO2e/kWh"
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
    assert evaluation_output["evaluation"]["minimum_evaluation_samples"] == 2
    assert evaluation_output["evaluation"]["requested_horizons"] == [1, 2, 4, 48]
    assert evaluation_output["evaluation"]["requested_methods"] == [
        "persistence",
        "seasonal_naive",
    ]
    assert evaluation_output["evaluation"]["evaluated_horizons"] == []
    assert evaluation_output["evaluation"]["evaluated_methods"] == []
    unavailable = evaluation_output["evaluation"]["unavailable_evaluations"]
    assert len(unavailable) == 8
    assert all(result["reason"] for result in unavailable)
    assert evaluation_output["evaluation"]["insufficient_sample_evaluations"] == []


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


def test_postgres_mixed_source_cli_exports_per_observation_units(
    postgres_engine, monkeypatch, capsys, tmp_path
) -> None:
    from app.core.config import settings

    live_retrieved_at = datetime(2026, 1, 1, 11, 30, tzinfo=UTC)
    live_payload = {
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
    archive_start = datetime(2026, 1, 1, 12, tzinfo=UTC)
    archive_payload = {
        "success": True,
        "result": {
            "resource_id": "c16b0e19-c02a-44a8-ba05-4db2c0545a2a",
            "fields": [{"id": "_id"}, {"id": "datetime"}, {"id": "London"}],
            "records": [{"_id": 91, "datetime": "2026-01-01T12:00:00", "London": 55}],
        },
    }
    archive_source_url = (
        "https://api.neso.energy/api/3/action/datastore_search_sql?sql=bounded"
    )
    archive_report = parse_historical_response(
        archive_payload,
        region_id=13,
        requested_start=archive_start,
        requested_end=archive_start + timedelta(minutes=30),
        acquired_at=datetime(2026, 1, 2, tzinfo=UTC),
        source_url=archive_source_url,
    )

    with Session(postgres_engine) as session:
        repository = CarbonRepository(session)
        with session.begin():
            raw = repository.store_payload(
                live_payload,
                region_id=13,
                source_url="https://api.carbonintensity.org.uk/regional/regionid/13",
                retrieved_at=live_retrieved_at,
                validation_fresh=True,
                validation_error=None,
                source_api=SOURCE_API,
            )
            repository.store_observation(raw, region_id=13)
            persist_historical_acquisition(session, archive_report, archive_payload)
        from app.services.historical_dataset import load_dataset

        dataset = load_dataset(session, 13)

    assert dataset.units is None
    assert dataset.unit_counts == {"gCO2/kWh": 1, "gCO2e/kWh": 1}
    assert dataset.forecast_observations[0].units == "gCO2/kWh"
    assert dataset.observations[0].units == "gCO2e/kWh"

    monkeypatch.setattr(settings, "database_url", SecretStr("postgresql://unused"))
    monkeypatch.setattr(
        dataset_builder, "create_engine", lambda *_args, **_kwargs: postgres_engine
    )
    output_path = tmp_path / "mixed-units.csv"
    assert build_dataset_cli(["--region-id", "13", "--output", str(output_path)]) == 0
    manifest = json.loads(capsys.readouterr().out)
    with output_path.open(newline="", encoding="utf-8") as stream:
        exported = list(csv.DictReader(stream))

    assert manifest["units"] is None
    assert manifest["unit_counts"] == dataset.unit_counts
    assert manifest["units_by_source"] == dataset.units_by_source
    assert manifest["availability"]["units"] is None
    assert manifest["availability"]["unit_counts"] == dataset.unit_counts
    assert manifest["availability"]["units_by_source"] == dataset.units_by_source
    assert {row["units"] for row in exported} == {"gCO2/kWh", "gCO2e/kWh"}


def test_mixed_source_dataset_preserves_units_and_deterministic_hashes(
    tmp_path,
) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    live_actual = observation(start, 40)
    archived_forecast = observation(
        start + timedelta(minutes=30),
        42,
        kind="forecast",
        source_api=DATASET_API,
    )

    dataset = build_dataset([live_actual, archived_forecast], region_id=13)
    reordered = build_dataset([archived_forecast, live_actual], region_id=13)

    assert live_actual.units == "gCO2e/kWh"
    assert archived_forecast.units == "gCO2/kWh"
    assert dataset.units is None
    assert dataset.unit_counts == {"gCO2/kWh": 1, "gCO2e/kWh": 1}
    assert dataset.dataset_sha256 == reordered.dataset_sha256
    body = json.loads(dataset.to_json())
    assert body["units"] is None
    assert body["unit_counts"] == dataset.unit_counts
    assert body["units_by_source"] == {
        DATASET_API: "gCO2/kWh",
        SOURCE_API: "gCO2e/kWh",
    }
    assert {
        row["units"] for row in body["observations"] + body["forecast_observations"]
    } == {"gCO2/kWh", "gCO2e/kWh"}

    output = tmp_path / "mixed.csv"
    dataset_builder._write_csv(
        (*dataset.observations, *dataset.forecast_observations), output
    )
    with output.open(newline="", encoding="utf-8") as stream:
        exported = list(csv.DictReader(stream))
    assert {row["units"] for row in exported} == {"gCO2/kWh", "gCO2e/kWh"}


def test_dataset_rejects_actual_revisions_with_incompatible_units() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    live = observation(start, 40)
    archive = observation(start, 42, source_api=DATASET_API)

    with pytest.raises(ValueError, match="different intensity units"):
        build_dataset([live, archive], region_id=13)


def test_dataset_rejects_sources_without_a_documented_unit_contract() -> None:
    with pytest.raises(ValueError, match="no intensity unit contract"):
        observation(
            datetime(2026, 1, 1, tzinfo=UTC),
            40,
            source_api="unregistered-carbon-source",
        )


def test_baseline_evaluation_does_not_compare_different_intensity_units() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    train = [observation(start + i * timedelta(minutes=30), 40 + i) for i in range(4)]
    test = [
        observation(
            start + 4 * timedelta(minutes=30),
            48,
            source_api=DATASET_API,
        ),
        observation(
            start + 5 * timedelta(minutes=30),
            50,
            source_api=DATASET_API,
        ),
    ]

    report = evaluate_baselines(
        train,
        test,
        horizons=(1,),
        seasonal_periods=4,
        minimum_training_observations=4,
    )

    assert report.sufficient_data is False
    assert report.results == ()
    assert report.units is None
    assert "different intensity units" in (report.insufficient_reason or "")


def test_baseline_report_units_follow_actuals_not_archived_forecasts() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    train = [observation(start + i * timedelta(minutes=30), 40 + i) for i in range(4)]
    test = [
        observation(start + (4 + i) * timedelta(minutes=30), 48 + i) for i in range(2)
    ]
    test.append(
        observation(
            start + 6 * timedelta(minutes=30),
            50,
            kind="forecast",
            source_api=DATASET_API,
        )
    )

    report = evaluate_baselines(
        train,
        test,
        horizons=(1,),
        seasonal_periods=4,
        minimum_training_observations=4,
    )

    assert report.units == "gCO2e/kWh"
