"""Tests for strict request validation and the NESO API adapter."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import ValidationError

from app.api.v1.energy import CarbonGateRequest
from app.services.neso_carbon_intensity import (
    NesoAPIError,
    NesoCarbonIntensityClient,
    parse_regional_response,
)


def _payload(*, start: datetime, end: datetime, actual: int | None = 79) -> dict:
    intensity: dict[str, int | str] = {"forecast": 83, "index": "low"}
    if actual is not None:
        intensity["actual"] = actual
    return {
        "data": [{
            "regionid": 13,
            "dnoregion": "UKPN London",
            "shortname": "London",
            "data": [{
                "from": start.isoformat(),
                "to": end.isoformat(),
                "intensity": intensity,
            }],
        }]
    }


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_carbon_gate_rejects_non_finite_numbers(value: str) -> None:
    with pytest.raises(ValidationError):
        CarbonGateRequest.model_validate_json(
            '{"carbon_intensity_gco2_per_kwh": ' + value + "}"
        )


def test_carbon_gate_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        CarbonGateRequest.model_validate(
            {"carbon_intensity_gco2_per_kwh": 20, "ignored": True}
        )


def test_neso_parser_rejects_stale_measurement() -> None:
    now = datetime.now(UTC)
    start = now - timedelta(hours=2)

    with pytest.raises(ValueError, match="stale"):
        parse_regional_response(
            _payload(start=start, end=start + timedelta(minutes=30)),
            region_id=13,
            now=now,
        )


def test_neso_parser_selects_actual_and_preserves_provenance() -> None:
    now = datetime.now(UTC)
    start = now - timedelta(minutes=8)
    url = "https://api.carbonintensity.org.uk/regional/regionid/13"

    reading = parse_regional_response(
        _payload(start=start, end=start + timedelta(minutes=30)),
        region_id=13,
        now=now,
        source_url=url,
    )

    assert reading.intensity_gco2_per_kwh == 79
    assert reading.measurement_type == "actual"
    assert reading.region_id == 13
    assert reading.region_name == "London"
    assert reading.source_api == "neso-carbon-intensity-api"
    assert reading.source_url == url
    assert reading.observed_at == start
    assert reading.valid_until == start + timedelta(minutes=30)
    assert reading.is_fresh is True
    assert reading.age_seconds == pytest.approx(480, abs=1)


def test_neso_parser_falls_back_to_forecast_when_actual_is_absent() -> None:
    now = datetime.now(UTC)
    start = now - timedelta(minutes=5)
    reading = parse_regional_response(
        _payload(start=start, end=start + timedelta(minutes=30), actual=None),
        region_id=13,
        now=now,
    )

    assert reading.intensity_gco2_per_kwh == 83
    assert reading.measurement_type == "forecast"
    assert reading.interval_status == "in_progress"
    assert reading.interval_lag_seconds == 0


def test_recent_completed_actual_remains_fresh_with_explicit_interval_lag() -> None:
    now = datetime.now(UTC)
    start = now - timedelta(minutes=40)
    end = start + timedelta(minutes=30)

    reading = parse_regional_response(
        _payload(start=start, end=end), region_id=13, now=now
    )

    assert reading.measurement_type == "actual"
    assert reading.interval_status == "completed"
    assert reading.interval_lag_seconds == pytest.approx(600, abs=1)
    assert reading.is_fresh is True


def test_recent_completed_forecast_is_explicitly_historical_not_current() -> None:
    now = datetime.now(UTC)
    start = now - timedelta(minutes=40)
    end = start + timedelta(minutes=30)

    reading = parse_regional_response(
        _payload(start=start, end=end, actual=None), region_id=13, now=now
    )

    assert reading.measurement_type == "forecast"
    assert reading.interval_status == "completed"
    assert reading.is_fresh is False
    assert reading.interval_lag_seconds == pytest.approx(600, abs=1)


def test_future_forecast_is_accepted_but_actual_cannot_be_future_dated() -> None:
    now = datetime.now(UTC)
    start = now + timedelta(minutes=10)
    end = start + timedelta(minutes=30)

    forecast = parse_regional_response(
        _payload(start=start, end=end, actual=None), region_id=13, now=now
    )
    assert forecast.measurement_type == "forecast"
    assert forecast.interval_status == "upcoming"

    far_future = now + timedelta(hours=49)
    with pytest.raises(ValueError, match="48-hour horizon"):
        parse_regional_response(
            _payload(
                start=far_future,
                end=far_future + timedelta(minutes=30),
                actual=None,
            ),
            region_id=13,
            now=now,
        )

    with pytest.raises(ValueError, match="actual measurement starts in the future"):
        parse_regional_response(
            _payload(start=start, end=end), region_id=13, now=now
        )


@pytest.mark.parametrize("bad_value", [-1, float("nan"), float("inf")])
def test_neso_parser_rejects_invalid_intensity(bad_value: float) -> None:
    now = datetime.now(UTC)
    start = now - timedelta(minutes=5)
    payload = _payload(start=start, end=start + timedelta(minutes=30))
    payload["data"][0]["data"][0]["intensity"]["actual"] = bad_value

    with pytest.raises(ValueError, match="finite and non-negative"):
        parse_regional_response(payload, region_id=13, now=now)


def test_neso_parser_rejects_wrong_region() -> None:
    now = datetime.now(UTC)
    start = now - timedelta(minutes=5)
    payload = _payload(start=start, end=start + timedelta(minutes=30))
    payload["data"][0]["regionid"] = 12

    with pytest.raises(ValueError, match="required regional"):
        parse_regional_response(payload, region_id=13, now=now)


def test_neso_parser_rejects_future_or_expired_interval() -> None:
    now = datetime.now(UTC)
    future_start = now + timedelta(minutes=2)
    with pytest.raises(ValueError, match="future"):
        parse_regional_response(
            _payload(start=future_start, end=future_start + timedelta(minutes=30)),
            region_id=13,
            now=now,
        )

    old_start = now - timedelta(hours=2)
    with pytest.raises(ValueError, match="stale"):
        parse_regional_response(
            _payload(start=old_start, end=old_start + timedelta(minutes=30)),
            region_id=13,
            now=now,
        )


@pytest.mark.asyncio
async def test_neso_client_fetches_regional_api_contract_via_injected_transport() -> None:
    now = datetime.now(UTC)
    start = now - timedelta(minutes=5)
    payload = _payload(start=start, end=start + timedelta(minutes=30))
    request_urls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        request_urls.append(str(request.url))
        return httpx.Response(200, json=payload)

    client = NesoCarbonIntensityClient(transport=httpx.MockTransport(handler))
    reading = await client.fetch_latest()

    assert reading.region_id == 13
    assert request_urls == [
        "https://api.carbonintensity.org.uk/regional/regionid/13"
    ]


@pytest.mark.asyncio
async def test_neso_client_maps_upstream_http_errors_without_fabricating_data() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "maintenance"})

    client = NesoCarbonIntensityClient(transport=httpx.MockTransport(handler))
    with pytest.raises(NesoAPIError, match="HTTP 503"):
        await client.fetch_latest()


@pytest.mark.asyncio
async def test_neso_client_maps_timeout_to_service_error() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("upstream timeout")

    client = NesoCarbonIntensityClient(transport=httpx.MockTransport(handler))
    with pytest.raises(NesoAPIError, match="timed out"):
        await client.fetch_latest()


@pytest.mark.asyncio
async def test_neso_client_rejects_invalid_json() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json")

    client = NesoCarbonIntensityClient(transport=httpx.MockTransport(handler))
    with pytest.raises(NesoAPIError, match="invalid JSON"):
        await client.fetch_latest()

