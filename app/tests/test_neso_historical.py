from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
import json
import sys

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import replay_neso as replay_cli
import app.acquire_neso_historical as acquire_cli
from app.persistence.historical_forecast_repository import (
    persist_historical_acquisition,
    replay_historical_forecast_record,
)
from app.persistence.models import CarbonObservation, RawPayload
from app.services.neso_historical import (
    DATASET_API,
    DataPortalHistoricalClient,
    HistoricalAcquisition,
    HistoricalSourceError,
    parse_historical_response,
)


def _response(records: list[dict[str, object]]) -> dict[str, object]:
    return {
        "success": True,
        "result": {
            "resource_id": "c16b0e19-c02a-44a8-ba05-4db2c0545a2a",
            "fields": [
                {"id": "_id"},
                {"id": "datetime"},
                {"id": "London"},
            ],
            "records": records,
        },
    }


def _record(
    timestamp: str, value: int | float | None, record_id: int = 1
) -> dict[str, object]:
    return {"_id": record_id, "datetime": timestamp, "London": value}


def test_parser_preserves_forecast_only_classification_and_utc_provenance() -> None:
    start = datetime(2026, 7, 24, 11, tzinfo=UTC)
    acquired = datetime(2026, 10, 11, 12, tzinfo=UTC)
    parsed = parse_historical_response(
        _response([_record("2026-07-24T11:00:00", 99)]),
        region_id=13,
        requested_start=start,
        requested_end=start + timedelta(hours=1),
        acquired_at=acquired,
        source_url="https://api.neso.energy/api/3/action/datastore_search_sql",
    )

    assert parsed.source_api == DATASET_API
    assert parsed.actual_count == 0
    assert parsed.forecast_count == 1
    assert parsed.observations[0].measurement_type == "forecast"
    assert parsed.observations[0].observed_at == start
    assert parsed.observations[0].retrieved_at == acquired
    assert parsed.temporal_evaluation_supported is False
    assert parsed.units == "gCO2/kWh"
    assert parsed.missing_intervals == (start + timedelta(minutes=30),)
    assert parsed.coverage_fraction == 0.5
    assert parsed.coverage_complete is False


def test_parser_accepts_ckan_sql_echo_when_resource_id_is_not_returned() -> None:
    start = datetime(2026, 7, 24, 11, tzinfo=UTC)
    payload = _response([_record("2026-07-24T11:00:00", 99)])
    result = payload["result"]
    assert isinstance(result, dict)
    result.pop("resource_id")
    result["sql"] = (
        'SELECT _id, datetime, "London" FROM '
        '"c16b0e19-c02a-44a8-ba05-4db2c0545a2a" '
        "WHERE datetime >= '2026-07-24T11:00:00' "
        "AND datetime < '2026-07-24T11:30:00' ORDER BY datetime, _id"
    )

    parsed = parse_historical_response(
        payload,
        region_id=13,
        requested_start=start,
        requested_end=start + timedelta(minutes=30),
        acquired_at=datetime(2026, 10, 11, tzinfo=UTC),
        source_url="https://api.neso.energy/data",
    )
    assert parsed.forecast_count == 1


def test_parser_rejects_out_of_window_and_malformed_rows_with_reasons() -> None:
    start = datetime(2026, 7, 24, 11, tzinfo=UTC)
    parsed = parse_historical_response(
        _response(
            [
                _record("2026-07-24T10:30:00", 1),
                _record("2026-07-24T11:00:00", None, 2),
                {"_id": 3, "datetime": "not-a-time", "London": 3},
            ]
        ),
        region_id=13,
        requested_start=start,
        requested_end=start + timedelta(hours=1),
        acquired_at=datetime(2026, 10, 11, tzinfo=UTC),
        source_url="https://api.neso.energy/data",
    )

    assert parsed.forecast_count == 0
    assert len(parsed.rejected_records) == 3
    assert all(row.reason for row in parsed.rejected_records)
    assert parsed.missing_intervals == (start, start + timedelta(minutes=30))


def test_identical_duplicates_are_counted_and_conflicting_timestamp_is_rejected() -> (
    None
):
    start = datetime(2026, 7, 24, 11, tzinfo=UTC)
    parsed = parse_historical_response(
        _response(
            [
                _record("2026-07-24T11:00:00", 99, 4),
                _record("2026-07-24T11:00:00", 99, 5),
                _record("2026-07-24T11:30:00", 90, 6),
                _record("2026-07-24T11:30:00", 91, 7),
            ]
        ),
        region_id=13,
        requested_start=start,
        requested_end=start + timedelta(hours=1),
        acquired_at=datetime(2026, 10, 11, tzinfo=UTC),
        source_url="https://api.neso.energy/data",
    )

    assert parsed.duplicate_count == 1
    assert parsed.conflicting_count == 1
    assert parsed.forecast_count == 1
    assert parsed.observations[0].source_record_id == 5
    assert parsed.missing_intervals == (start + timedelta(minutes=30),)
    assert any("conflict" in row.reason for row in parsed.rejected_records)


def test_client_builds_bounded_region_query_and_hashes_payload() -> None:
    start = datetime(2026, 7, 24, 11, tzinfo=UTC)
    end = start + timedelta(hours=1)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_response([_record("2026-07-24T11:00:00", 99)]))

    client = DataPortalHistoricalClient(
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
        minimum_request_interval_seconds=0,
        clock=lambda: datetime(2026, 10, 11, 12, tzinfo=UTC),
    )
    acquired = client.acquire(
        region_id=13,
        requested_start=start,
        requested_end=end,
    )

    assert acquired.report.forecast_count == 1
    assert len(acquired.report.payload_sha256) == 64
    assert "datetime >= '2026-07-24T11:00:00'" in seen[0].url.params["sql"]
    assert "datetime < '2026-07-24T12:00:00'" in seen[0].url.params["sql"]
    assert '"London"' in seen[0].url.params["sql"]


def test_client_retries_rate_limit_after_requested_delay() -> None:
    start = datetime(2026, 7, 24, 11, tzinfo=UTC)
    calls = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(200, json=_response([_record("2026-07-24T11:00:00", 99)]))

    client = DataPortalHistoricalClient(
        transport=httpx.MockTransport(handler),
        sleep=delays.append,
        minimum_request_interval_seconds=0,
        clock=lambda: datetime(2026, 10, 11, 12, tzinfo=UTC),
    )
    acquired = client.acquire(
        region_id=13,
        requested_start=start,
        requested_end=start + timedelta(minutes=30),
    )

    assert acquired.report.forecast_count == 1
    assert calls == 2
    assert delays == [2]


def test_client_retries_http_date_retry_after() -> None:
    start = datetime(2026, 7, 24, 11, tzinfo=UTC)
    now = datetime(2026, 10, 11, 12, tzinfo=UTC)
    calls = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                429,
                headers={
                    "Retry-After": format_datetime(
                        now + timedelta(seconds=5), usegmt=True
                    )
                },
            )
        return httpx.Response(200, json=_response([_record("2026-07-24T11:00:00", 99)]))

    client = DataPortalHistoricalClient(
        transport=httpx.MockTransport(handler),
        sleep=delays.append,
        minimum_request_interval_seconds=0,
        clock=lambda: now,
    )
    client.acquire(
        region_id=13,
        requested_start=start,
        requested_end=start + timedelta(minutes=30),
    )

    assert calls == 2
    assert delays == [5]


def test_client_fails_closed_when_retry_after_exceeds_safe_wait() -> None:
    start = datetime(2026, 7, 24, 11, tzinfo=UTC)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"Retry-After": "600"})

    client = DataPortalHistoricalClient(
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
        minimum_request_interval_seconds=0,
        max_retry_after_seconds=5,
    )

    with pytest.raises(HistoricalSourceError, match="Retry-After"):
        client.acquire(
            region_id=13,
            requested_start=start,
            requested_end=start + timedelta(minutes=30),
        )
    assert calls == 1


def test_client_rejects_response_larger_than_configured_limit() -> None:
    start = datetime(2026, 7, 24, 11, tzinfo=UTC)
    body = json.dumps(_response([_record("2026-07-24T11:00:00", 99)])).encode()
    client = DataPortalHistoricalClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=body)
        ),
        sleep=lambda _: None,
        minimum_request_interval_seconds=0,
        max_response_bytes=10,
    )

    with pytest.raises(HistoricalSourceError, match="exceeded 10 bytes"):
        client.acquire(
            region_id=13,
            requested_start=start,
            requested_end=start + timedelta(minutes=30),
        )


def test_parser_rejects_unexpected_query_echo_bounds() -> None:
    start = datetime(2026, 7, 24, 11, tzinfo=UTC)
    payload = _response([_record("2026-07-24T11:00:00", 99)])
    result = payload["result"]
    assert isinstance(result, dict)
    result.pop("resource_id")
    result["sql"] = (
        'SELECT _id, datetime, "London" FROM '
        '"c16b0e19-c02a-44a8-ba05-4db2c0545a2a" '
        "WHERE datetime >= '2026-07-24T10:30:00' "
        "AND datetime < '2026-07-24T11:30:00' ORDER BY datetime, _id"
    )

    with pytest.raises(HistoricalSourceError, match="does not identify the requested"):
        parse_historical_response(
            payload,
            region_id=13,
            requested_start=start,
            requested_end=start + timedelta(minutes=30),
            acquired_at=datetime(2026, 10, 11, tzinfo=UTC),
            source_url="https://api.neso.energy/data",
        )


def test_client_rejects_unbounded_or_misaligned_windows_before_network() -> None:
    client = DataPortalHistoricalClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(500)),
        sleep=lambda _: None,
        minimum_request_interval_seconds=0,
    )
    start = datetime(2026, 1, 1, tzinfo=UTC)

    with pytest.raises(ValueError, match="14 days"):
        client.acquire(
            region_id=13,
            requested_start=start,
            requested_end=start + timedelta(days=15),
        )
    with pytest.raises(ValueError, match="half-hour"):
        client.acquire(
            region_id=13,
            requested_start=start + timedelta(minutes=5),
            requested_end=start + timedelta(minutes=35),
        )
    with pytest.raises(ValueError, match="region"):
        client.acquire(
            region_id=17,
            requested_start=start,
            requested_end=start + timedelta(minutes=30),
        )


def test_postgres_persists_replayable_forecasts_idempotently(
    postgres_engine, monkeypatch, capsys
) -> None:
    start = datetime(2026, 7, 24, 11, tzinfo=UTC)
    acquired_at = datetime(2026, 10, 11, 12, tzinfo=UTC)
    payload = _response(
        [
            _record("2026-07-24T11:00:00", 99, 501),
            _record("2026-07-24T11:30:00", 90, 502),
        ]
    )
    result = payload["result"]
    assert isinstance(result, dict)
    result.pop("resource_id")
    result["sql"] = (
        'SELECT _id, datetime, "London" FROM '
        '"c16b0e19-c02a-44a8-ba05-4db2c0545a2a" '
        "WHERE datetime >= '2026-07-24T11:00:00' "
        "AND datetime < '2026-07-24T12:00:00' ORDER BY datetime, _id"
    )
    source_url = str(
        httpx.URL(
            "https://api.neso.energy/api/3/action/datastore_search_sql",
            params={"sql": result["sql"]},
        )
    )
    acquisition = parse_historical_response(
        payload,
        region_id=13,
        requested_start=start,
        requested_end=start + timedelta(hours=1),
        acquired_at=acquired_at,
        source_url=source_url,
    )
    with Session(postgres_engine) as session, session.begin():
        with pytest.raises(ValueError, match="does not match"):
            persist_historical_acquisition(session, acquisition, _response([]))
    with Session(postgres_engine) as session, session.begin():
        first = persist_historical_acquisition(session, acquisition, payload)
    with Session(postgres_engine) as session, session.begin():
        second = persist_historical_acquisition(session, acquisition, payload)
    with Session(postgres_engine) as session:
        raw = session.get(RawPayload, first.raw_payload_id)
        observations = session.scalars(
            select(CarbonObservation).where(
                CarbonObservation.raw_payload_id == first.raw_payload_id
            )
        ).all()
        replayed = replay_historical_forecast_record(raw, source_record_id=501)
        from app.services.historical_dataset import load_dataset

        dataset = load_dataset(session, 13)

    assert first.inserted_observation_count == 2
    assert second.inserted_observation_count == 0
    assert second.raw_payload_id == first.raw_payload_id
    assert len(observations) == 2
    assert {row.measurement_type for row in observations} == {"forecast"}
    assert {row.retrieved_at for row in observations} == {acquired_at}
    assert raw.validation_fresh is False
    assert raw.validation_error is None
    assert replayed.measurement_type == "forecast"
    assert replayed.units == "gCO2/kWh"
    assert replayed.intensity_gco2_per_kwh == 99
    assert replayed.retrieved_at == acquired_at
    assert {row.units for row in dataset.forecast_observations} == {"gCO2/kWh"}

    monkeypatch.setattr(replay_cli.settings, "database_url", SecretStr("test"))
    monkeypatch.setattr(
        replay_cli, "create_engine", lambda *args, **kwargs: postgres_engine
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["replay_neso", str(first.raw_payload_id), "--source-record-id", "501"],
    )
    replay_cli.main()
    cli_output = json.loads(capsys.readouterr().out)
    assert cli_output["measurement_type"] == "forecast"
    assert cli_output["units"] == "gCO2/kWh"
    assert cli_output["source_record_id"] == 501


def test_cli_is_dry_run_by_default_and_reports_partial_coverage(
    monkeypatch, capsys
) -> None:
    start = datetime(2026, 7, 24, 11, tzinfo=UTC)
    acquired_at = datetime(2026, 10, 11, 12, tzinfo=UTC)
    payload = _response([_record("2026-07-24T11:00:00", 99)])
    report = parse_historical_response(
        payload,
        region_id=13,
        requested_start=start,
        requested_end=start + timedelta(hours=1),
        acquired_at=acquired_at,
        source_url="https://api.neso.energy/data",
    )
    monkeypatch.setattr(
        acquire_cli.DataPortalHistoricalClient,
        "acquire",
        lambda self, **kwargs: HistoricalAcquisition(report, payload),
    )
    monkeypatch.setattr(acquire_cli.settings, "database_url", None)
    monkeypatch.setattr(
        acquire_cli,
        "persist_historical_acquisition",
        lambda *args, **kwargs: pytest.fail("default CLI must not write to PostgreSQL"),
    )

    assert (
        acquire_cli.main(
            [
                "--from",
                start.isoformat(),
                "--to",
                (start + timedelta(hours=1)).isoformat(),
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)

    assert output["dry_run"] is True
    assert output["persisted"] is None
    assert output["report"]["actual_count"] == 0
    assert output["report"]["forecast_count"] == 1
    assert len(output["report"]["missing_intervals"]) == 1
    assert output["report"]["temporal_evaluation_supported"] is False


def test_cli_requires_explicit_database_configuration_before_fetch(monkeypatch) -> None:
    start = datetime(2026, 7, 24, 11, tzinfo=UTC)
    monkeypatch.setattr(acquire_cli.settings, "database_url", None)
    monkeypatch.setattr(
        acquire_cli.DataPortalHistoricalClient,
        "acquire",
        lambda *args, **kwargs: pytest.fail(
            "CLI must validate --persist before networking"
        ),
    )

    with pytest.raises(SystemExit, match="2"):
        acquire_cli.main(
            [
                "--from",
                start.isoformat(),
                "--to",
                (start + timedelta(minutes=30)).isoformat(),
                "--persist",
            ]
        )
