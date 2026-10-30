"""Deterministic contract tests for regional NESO forecast timelines."""

from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx
import pytest

from app.api.v1 import energy as energy_api
from app.core.config import Settings
from app.main import create_app
from app.services.neso_carbon_intensity import (
    CachedNesoCarbonIntensityReader,
    NesoCarbonIntensityClient,
    next_half_hour_start,
    parse_regional_forecast_response,
)


@pytest.fixture
def forecast_start() -> datetime:
    return datetime(2026, 10, 29, 23, 30, tzinfo=UTC)


@pytest.fixture
def valid_forecast_payload(forecast_start: datetime) -> dict[str, Any]:
    intervals = []
    for index in range(48):
        interval_start = forecast_start + timedelta(minutes=30 * index)
        intervals.append(
            {
                "from": interval_start.strftime("%Y-%m-%dT%H:%MZ"),
                "to": (interval_start + timedelta(minutes=30)).strftime(
                    "%Y-%m-%dT%H:%MZ"
                ),
                "intensity": {"forecast": 100 + index, "index": "low"},
            }
        )
    return {
        "data": [
            {
                "regionid": 13,
                "shortname": "London",
                "data": intervals,
            }
        ]
    }


def test_parser_returns_complete_forecast_only_timeline(
    forecast_start: datetime,
    valid_forecast_payload: dict[str, Any],
) -> None:
    retrieved_at = forecast_start - timedelta(minutes=5)
    source_url = (
        "https://api.carbonintensity.org.uk/regional/intensity/"
        "2026-10-29T23:30Z/fw24h/regionid/13"
    )

    timeline = parse_regional_forecast_response(
        valid_forecast_payload,
        region_id=13,
        requested_from=forecast_start,
        retrieved_at=retrieved_at,
        source_url=source_url,
    )

    assert timeline.region_id == 13
    assert timeline.region_name == "London"
    assert timeline.requested_from == forecast_start
    assert timeline.valid_until == forecast_start + timedelta(hours=24)
    assert timeline.retrieved_at == retrieved_at
    assert timeline.units == "gCO2/kWh"
    assert timeline.source_url == source_url
    assert len(timeline.intervals) == 48
    assert timeline.intervals[0].observed_at == forecast_start
    assert timeline.intervals[-1].valid_until == timeline.valid_until
    assert {interval.measurement_type for interval in timeline.intervals} == {"forecast"}


def test_parser_preserves_valid_unicode_region_shortnames(
    forecast_start: datetime,
    valid_forecast_payload: dict[str, Any],
) -> None:
    expected_name = "Réunion – 東京 🌍"
    valid_forecast_payload["data"][0]["shortname"] = expected_name

    timeline = parse_regional_forecast_response(
        valid_forecast_payload,
        region_id=13,
        requested_from=forecast_start,
        retrieved_at=forecast_start - timedelta(minutes=5),
    )

    assert timeline.region_name == expected_name


def test_parser_records_the_canonical_upstream_url_when_not_supplied(
    forecast_start: datetime,
    valid_forecast_payload: dict[str, Any],
) -> None:
    timeline = parse_regional_forecast_response(
        valid_forecast_payload,
        region_id=13,
        requested_from=forecast_start,
        retrieved_at=forecast_start - timedelta(minutes=5),
    )

    assert timeline.source_url == (
        "https://api.carbonintensity.org.uk/regional/intensity/"
        "2026-10-29T23:30Z/fw24h/regionid/13"
    )


def test_parser_rejects_a_missing_half_hour_interval(
    forecast_start: datetime,
    valid_forecast_payload: dict[str, Any],
) -> None:
    valid_forecast_payload["data"][0]["data"].pop(17)

    with pytest.raises(ValueError, match="missing forecast interval"):
        parse_regional_forecast_response(
            valid_forecast_payload,
            region_id=13,
            requested_from=forecast_start,
            retrieved_at=forecast_start - timedelta(minutes=5),
        )


def test_parser_rejects_duplicate_forecast_intervals(
    forecast_start: datetime,
    valid_forecast_payload: dict[str, Any],
) -> None:
    records = valid_forecast_payload["data"][0]["data"]
    records[18] = dict(records[17])

    with pytest.raises(ValueError, match="duplicate forecast interval"):
        parse_regional_forecast_response(
            valid_forecast_payload,
            region_id=13,
            requested_from=forecast_start,
            retrieved_at=forecast_start - timedelta(minutes=5),
        )


def test_parser_rejects_non_finite_forecast_intensity(
    forecast_start: datetime,
    valid_forecast_payload: dict[str, Any],
) -> None:
    valid_forecast_payload["data"][0]["data"][0]["intensity"]["forecast"] = float(
        "nan"
    )

    with pytest.raises(ValueError, match="finite and non-negative"):
        parse_regional_forecast_response(
            valid_forecast_payload,
            region_id=13,
            requested_from=forecast_start,
            retrieved_at=forecast_start - timedelta(minutes=5),
        )


def test_parser_rejects_outdated_forecast_intervals(
    forecast_start: datetime,
    valid_forecast_payload: dict[str, Any],
) -> None:
    outdated_start = forecast_start - timedelta(minutes=30)
    outdated_interval = valid_forecast_payload["data"][0]["data"][0]
    outdated_interval["from"] = outdated_start.strftime("%Y-%m-%dT%H:%MZ")
    outdated_interval["to"] = (outdated_start + timedelta(minutes=30)).strftime(
        "%Y-%m-%dT%H:%MZ"
    )

    with pytest.raises(ValueError, match="outdated"):
        parse_regional_forecast_response(
            valid_forecast_payload,
            region_id=13,
            requested_from=forecast_start,
            retrieved_at=forecast_start - timedelta(minutes=5),
        )


def test_parser_rejects_intervals_that_are_not_thirty_minutes(
    forecast_start: datetime,
    valid_forecast_payload: dict[str, Any],
) -> None:
    valid_forecast_payload["data"][0]["data"][0]["to"] = (
        forecast_start + timedelta(minutes=45)
    ).strftime("%Y-%m-%dT%H:%MZ")

    with pytest.raises(ValueError, match="30-minute"):
        parse_regional_forecast_response(
            valid_forecast_payload,
            region_id=13,
            requested_from=forecast_start,
            retrieved_at=forecast_start - timedelta(minutes=5),
        )


def test_parser_rejects_forecast_timestamps_outside_datetime_range(
    forecast_start: datetime,
    valid_forecast_payload: dict[str, Any],
) -> None:
    interval = valid_forecast_payload["data"][0]["data"][0]
    interval["from"] = "9999-12-31T23:30:00Z"
    interval["to"] = "9999-12-31T23:59:00Z"

    with pytest.raises(ValueError, match="datetime range"):
        parse_regional_forecast_response(
            valid_forecast_payload,
            region_id=13,
            requested_from=forecast_start,
            retrieved_at=forecast_start - timedelta(minutes=5),
        )


def test_parser_rejects_a_grid_gap_even_when_record_count_is_48(
    forecast_start: datetime,
    valid_forecast_payload: dict[str, Any],
) -> None:
    shifted_start = forecast_start + timedelta(hours=24)
    interval = valid_forecast_payload["data"][0]["data"][17]
    interval["from"] = shifted_start.strftime("%Y-%m-%dT%H:%MZ")
    interval["to"] = (shifted_start + timedelta(minutes=30)).strftime(
        "%Y-%m-%dT%H:%MZ"
    )

    with pytest.raises(ValueError, match="missing forecast interval"):
        parse_regional_forecast_response(
            valid_forecast_payload,
            region_id=13,
            requested_from=forecast_start,
            retrieved_at=forecast_start - timedelta(minutes=5),
        )


def test_parser_rejects_region_ids_outside_the_official_range(
    forecast_start: datetime,
    valid_forecast_payload: dict[str, Any],
) -> None:
    valid_forecast_payload["data"][0]["regionid"] = 18

    with pytest.raises(ValueError, match="between 1 and 17"):
        parse_regional_forecast_response(
            valid_forecast_payload,
            region_id=18,
            requested_from=forecast_start,
            retrieved_at=forecast_start - timedelta(minutes=5),
        )


def test_parser_rejects_non_integer_region_ids(
    forecast_start: datetime,
    valid_forecast_payload: dict[str, Any],
) -> None:
    with pytest.raises(ValueError, match="between 1 and 17"):
        parse_regional_forecast_response(
            valid_forecast_payload,
            region_id=cast(Any, 13.0),
            requested_from=forecast_start,
            retrieved_at=forecast_start - timedelta(minutes=5),
        )


def test_parser_rejects_actual_values_in_the_forecast_timeline(
    forecast_start: datetime,
    valid_forecast_payload: dict[str, Any],
) -> None:
    valid_forecast_payload["data"][0]["data"][0]["intensity"]["actual"] = 80

    with pytest.raises(ValueError, match="actual.*forecast"):
        parse_regional_forecast_response(
            valid_forecast_payload,
            region_id=13,
            requested_from=forecast_start,
            retrieved_at=forecast_start - timedelta(minutes=5),
        )


def test_forecast_window_start_rounds_up_to_a_utc_half_hour() -> None:
    now = datetime(2026, 3, 29, 0, 45, tzinfo=UTC)

    start = next_half_hour_start(now)

    assert start == datetime(2026, 3, 29, 1, 0, tzinfo=UTC)
    assert start + timedelta(hours=24) == datetime(
        2026, 3, 30, 1, 0, tzinfo=UTC
    )


def test_forecast_window_start_keeps_an_exact_utc_boundary() -> None:
    now = datetime(2026, 3, 30, 0, 0, tzinfo=UTC)

    assert next_half_hour_start(now) == now


def test_parser_rejects_a_requested_start_off_the_half_hour_grid(
    forecast_start: datetime,
    valid_forecast_payload: dict[str, Any],
) -> None:
    requested_from = forecast_start + timedelta(minutes=5)
    for index, interval in enumerate(valid_forecast_payload["data"][0]["data"]):
        shifted_start = requested_from + timedelta(minutes=30 * index)
        interval["from"] = shifted_start.strftime("%Y-%m-%dT%H:%MZ")
        interval["to"] = (shifted_start + timedelta(minutes=30)).strftime(
            "%Y-%m-%dT%H:%MZ"
        )

    with pytest.raises(ValueError, match="half-hour aligned"):
        parse_regional_forecast_response(
            valid_forecast_payload,
            region_id=13,
            requested_from=requested_from,
            retrieved_at=forecast_start,
        )


def test_parser_rejects_a_timeline_already_completed_at_retrieval(
    forecast_start: datetime,
    valid_forecast_payload: dict[str, Any],
) -> None:
    with pytest.raises(ValueError, match="outdated"):
        parse_regional_forecast_response(
            valid_forecast_payload,
            region_id=13,
            requested_from=forecast_start,
            retrieved_at=forecast_start + timedelta(hours=25),
        )


def test_parser_rejects_duplicate_region_entries(
    forecast_start: datetime,
    valid_forecast_payload: dict[str, Any],
) -> None:
    valid_forecast_payload["data"].append(
        dict(valid_forecast_payload["data"][0])
    )

    with pytest.raises(ValueError, match="duplicate region"):
        parse_regional_forecast_response(
            valid_forecast_payload,
            region_id=13,
            requested_from=forecast_start,
            retrieved_at=forecast_start - timedelta(minutes=5),
        )


def test_parser_rejects_unsupported_intensity_indexes(
    forecast_start: datetime,
    valid_forecast_payload: dict[str, Any],
) -> None:
    valid_forecast_payload["data"][0]["data"][0]["intensity"]["index"] = "unknown"

    with pytest.raises(ValueError, match="unsupported intensity index"):
        parse_regional_forecast_response(
            valid_forecast_payload,
            region_id=13,
            requested_from=forecast_start,
            retrieved_at=forecast_start - timedelta(minutes=5),
        )


@pytest.mark.asyncio
async def test_forecast_client_uses_official_regional_24_hour_endpoint(
    valid_forecast_payload: dict[str, Any],
) -> None:
    requested_from = next_half_hour_start(datetime.now(UTC)) + timedelta(hours=1)
    records = valid_forecast_payload["data"][0]["data"]
    for index, interval in enumerate(records):
        interval_start = requested_from + timedelta(minutes=30 * index)
        interval["from"] = interval_start.strftime("%Y-%m-%dT%H:%MZ")
        interval["to"] = (interval_start + timedelta(minutes=30)).strftime(
            "%Y-%m-%dT%H:%MZ"
        )
    requests: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=valid_forecast_payload)

    client = NesoCarbonIntensityClient(
        transport=httpx.MockTransport(respond),
    )

    timeline = await client.fetch_forecast_timeline(
        region_id=13,
        requested_from=requested_from,
    )

    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert str(requests[0].url) == (
        "https://api.carbonintensity.org.uk/regional/intensity/"
        f"{requested_from.strftime('%Y-%m-%dT%H:%MZ')}/fw24h/regionid/13"
    )
    assert timeline.source_url == str(requests[0].url)
    assert timeline.region_id == 13
    assert len(timeline.intervals) == 48


@pytest.mark.asyncio
async def test_forecast_cache_reuses_timeline_for_same_region_and_window(
    valid_forecast_payload: dict[str, Any],
) -> None:
    requested_from = next_half_hour_start(datetime.now(UTC)) + timedelta(hours=1)
    retrieved_at = datetime.now(UTC)
    records = valid_forecast_payload["data"][0]["data"]
    for index, interval in enumerate(records):
        interval_start = requested_from + timedelta(minutes=30 * index)
        interval["from"] = interval_start.strftime("%Y-%m-%dT%H:%MZ")
        interval["to"] = (interval_start + timedelta(minutes=30)).strftime(
            "%Y-%m-%dT%H:%MZ"
        )

    class CachedClient:
        max_age_seconds = 3600

        def __init__(self) -> None:
            self.calls = 0

        async def fetch_forecast_timeline(
            self,
            *,
            region_id: int,
            requested_from: datetime,
        ):
            self.calls += 1
            for index, interval in enumerate(valid_forecast_payload["data"][0]["data"]):
                interval_start = requested_from + timedelta(minutes=30 * index)
                interval["from"] = interval_start.strftime("%Y-%m-%dT%H:%MZ")
                interval["to"] = (interval_start + timedelta(minutes=30)).strftime(
                    "%Y-%m-%dT%H:%MZ"
                )
            return parse_regional_forecast_response(
                valid_forecast_payload,
                region_id=region_id,
                requested_from=requested_from,
                retrieved_at=retrieved_at,
            )

    monotonic_time = [100.0]
    client = CachedClient()
    reader = CachedNesoCarbonIntensityReader(
        client,  # type: ignore[arg-type]
        clock=lambda: monotonic_time[0],
    )

    first = await reader.fetch_forecast_timeline(
        region_id=13,
        requested_from=requested_from,
    )
    second = await reader.fetch_forecast_timeline(
        region_id=13,
        requested_from=requested_from,
    )
    next_window_start = requested_from + timedelta(minutes=30)
    third = await reader.fetch_forecast_timeline(
        region_id=13,
        requested_from=next_window_start,
    )
    monotonic_time[0] += 31.0
    fourth = await reader.fetch_forecast_timeline(
        region_id=13,
        requested_from=next_window_start,
    )

    assert first is second
    assert third.requested_from == next_window_start
    assert third is not fourth
    assert client.calls == 3


@pytest.mark.asyncio
async def test_public_forecast_api_is_get_only_and_returns_forecast_provenance(
    monkeypatch: pytest.MonkeyPatch,
    valid_forecast_payload: dict[str, Any],
) -> None:
    requested_from = next_half_hour_start(datetime.now(UTC)) + timedelta(hours=1)
    monkeypatch.setattr(
        energy_api,
        "next_half_hour_start",
        lambda _now: requested_from,
        raising=False,
    )
    records = valid_forecast_payload["data"][0]["data"]
    for index, interval in enumerate(records):
        interval_start = requested_from + timedelta(minutes=30 * index)
        interval["from"] = interval_start.strftime("%Y-%m-%dT%H:%MZ")
        interval["to"] = (interval_start + timedelta(minutes=30)).strftime(
            "%Y-%m-%dT%H:%MZ"
        )
    fetch_calls: list[tuple[int, datetime]] = []

    async def fetch_forecast(
        self: NesoCarbonIntensityClient,
        *,
        region_id: int,
        requested_from: datetime,
    ):
        fetch_calls.append((region_id, requested_from))
        endpoint_url = (
            f"{self.base_url}/regional/intensity/"
            f"{requested_from.strftime('%Y-%m-%dT%H:%MZ')}/fw24h/regionid/{region_id}"
        )
        return parse_regional_forecast_response(
            valid_forecast_payload,
            region_id=region_id,
            requested_from=requested_from,
            retrieved_at=datetime.now(UTC),
            source_url=endpoint_url,
        )

    monkeypatch.setattr(NesoCarbonIntensityClient, "fetch_forecast_timeline", fetch_forecast)
    application = create_app(Settings(simulation_only_mode=True))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://test",
    ) as api:
        first = await api.get(
            "/api/v1/energy/carbon-intensity/forecast?region_id=13"
        )
        second = await api.get(
            "/api/v1/energy/carbon-intensity/forecast?region_id=13"
        )
        invalid_region = await api.get(
            "/api/v1/energy/carbon-intensity/forecast?region_id=18"
        )
        write_attempt = await api.post(
            "/api/v1/energy/carbon-intensity/forecast?region_id=13"
        )
        body_attempt = await api.request(
            "GET",
            "/api/v1/energy/carbon-intensity/forecast?region_id=13",
            json={"unexpected": True},
        )

    assert first.status_code == 200
    assert first.headers["cache-control"] == "no-store"
    body = first.json()
    assert body["status"] == "available"
    assert body["measurement_type"] == "forecast"
    assert body["units"] == "gCO2/kWh"
    assert body["region_id"] == 13
    assert body["requested_from"].endswith("Z")
    assert body["retrieved_at"]
    assert body["source_api"] == "neso-carbon-intensity-api"
    assert body["source_url"].endswith("/fw24h/regionid/13")
    assert len(body["intervals"]) == 48
    assert {interval["measurement_type"] for interval in body["intervals"]} == {
        "forecast"
    }
    assert second.json()["retrieved_at"] == body["retrieved_at"]
    assert fetch_calls == [(13, requested_from)]
    assert invalid_region.status_code == 422
    assert write_attempt.status_code == 405
    assert write_attempt.headers["allow"] == "GET"
    assert body_attempt.status_code == 400


@pytest.mark.asyncio
async def test_public_forecast_api_uses_the_existing_request_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
    valid_forecast_payload: dict[str, Any],
) -> None:
    requested_from = next_half_hour_start(datetime.now(UTC)) + timedelta(hours=1)
    monkeypatch.setattr(
        energy_api,
        "next_half_hour_start",
        lambda _now: requested_from,
        raising=False,
    )
    records = valid_forecast_payload["data"][0]["data"]
    for index, interval in enumerate(records):
        interval_start = requested_from + timedelta(minutes=30 * index)
        interval["from"] = interval_start.strftime("%Y-%m-%dT%H:%MZ")
        interval["to"] = (interval_start + timedelta(minutes=30)).strftime(
            "%Y-%m-%dT%H:%MZ"
        )
    calls = 0

    async def fetch_forecast(
        _self: NesoCarbonIntensityClient,
        *,
        region_id: int,
        requested_from: datetime,
    ):
        nonlocal calls
        calls += 1
        return parse_regional_forecast_response(
            valid_forecast_payload,
            region_id=region_id,
            requested_from=requested_from,
            retrieved_at=datetime.now(UTC),
        )

    monkeypatch.setattr(NesoCarbonIntensityClient, "fetch_forecast_timeline", fetch_forecast)
    application = create_app(
        Settings(
            simulation_only_mode=True,
            api_rate_limit_requests_per_minute=1,
        )
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://test",
    ) as api:
        first = await api.get(
            "/api/v1/energy/carbon-intensity/forecast?region_id=13"
        )
        second = await api.get(
            "/api/v1/energy/carbon-intensity/forecast?region_id=13"
        )

    assert first.status_code == 200
    assert second.status_code == 429
    assert int(second.headers["retry-after"]) >= 1
    assert calls == 1


@pytest.mark.asyncio
async def test_public_forecast_api_returns_explicit_unavailable_state_on_upstream_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(503, headers={"Retry-After": "7"})

    client = NesoCarbonIntensityClient(transport=httpx.MockTransport(respond))
    monkeypatch.setattr("app.main.NesoCarbonIntensityClient", lambda **_kwargs: client)
    application = create_app(Settings(simulation_only_mode=True))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://test",
    ) as api:
        first = await api.get(
            "/api/v1/energy/carbon-intensity/forecast?region_id=13"
        )
        second = await api.get(
            "/api/v1/energy/carbon-intensity/forecast?region_id=13"
        )

    for response in (first, second):
        assert response.status_code == 503
        assert response.headers["cache-control"] == "no-store"
        assert "forecast unavailable" in response.json()["detail"]
    assert len(requests) == 2
    assert {request.method for request in requests} == {"GET"}
    assert all(request.url.path.endswith("/fw24h/regionid/13") for request in requests)


@pytest.mark.asyncio
async def test_public_forecast_api_fails_closed_for_missing_upstream_intervals(
    monkeypatch: pytest.MonkeyPatch,
    valid_forecast_payload: dict[str, Any],
) -> None:
    requested_from = next_half_hour_start(datetime.now(UTC)) + timedelta(hours=1)
    monkeypatch.setattr(
        energy_api,
        "next_half_hour_start",
        lambda _now: requested_from,
        raising=False,
    )
    records = valid_forecast_payload["data"][0]["data"]
    for index, interval in enumerate(records):
        interval_start = requested_from + timedelta(minutes=30 * index)
        interval["from"] = interval_start.strftime("%Y-%m-%dT%H:%MZ")
        interval["to"] = (interval_start + timedelta(minutes=30)).strftime(
            "%Y-%m-%dT%H:%MZ"
        )
    records.pop(21)
    requests: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=valid_forecast_payload)

    client = NesoCarbonIntensityClient(transport=httpx.MockTransport(respond))
    monkeypatch.setattr("app.main.NesoCarbonIntensityClient", lambda **_kwargs: client)
    application = create_app(Settings(simulation_only_mode=True))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://test",
    ) as api:
        first = await api.get(
            "/api/v1/energy/carbon-intensity/forecast?region_id=13"
        )
        second = await api.get(
            "/api/v1/energy/carbon-intensity/forecast?region_id=13"
        )

    for response in (first, second):
        assert response.status_code == 503
        assert response.headers["cache-control"] == "no-store"
        assert "missing forecast intervals" in response.json()["detail"]
    assert len(requests) == 2
