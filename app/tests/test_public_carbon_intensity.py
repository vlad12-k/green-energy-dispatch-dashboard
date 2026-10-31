"""Contract tests for the public, read-only NESO dashboard endpoint."""

from datetime import UTC, datetime, timedelta

import asyncio
import gzip
import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.api.v1 import energy as energy_api
from app.core.config import Settings
from app.main import create_app
from app.services.neso_carbon_intensity import (
    CachedNesoCarbonIntensityReader,
    NesoAPIError,
    NesoCarbonIntensityClient,
    parse_regional_response,
)


def _payload(
    now: datetime,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    actual: int | None = 79,
) -> dict[str, object]:
    start = start or now - timedelta(minutes=5)
    end = end or start + timedelta(minutes=30)
    return {
        "data": [
            {
                "regionid": 13,
                "shortname": "London",
                "data": [
                    {
                        "from": start.isoformat(),
                        "to": end.isoformat(),
                        "intensity": {
                            "actual": actual,
                            "forecast": 83,
                            "index": "low",
                        },
                    }
                ],
            }
        ]
    }


@pytest.mark.parametrize(
    "base_url",
    [
        "https://example.test",
        "https://api.carbonintensity.org.uk.attacker.test",
        "https://user@api.carbonintensity.org.uk",
        "https://api.carbonintensity.org.uk/proxy",
        "https://api.carbonintensity.org.uk:8443",
    ],
)
def test_neso_configuration_rejects_non_allowlisted_endpoints(base_url: str) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(neso_api_base_url=base_url)


def test_forecast_preview_openapi_documents_upstream_unavailability() -> None:
    application = create_app(
        Settings(app_env="development", debug=True, simulation_only_mode=False)
    )

    openapi = application.openapi()
    responses = openapi["paths"][
        "/api/v1/energy/carbon-intensity/forecast"
    ]["get"]["responses"]
    assert responses["503"]["description"] == "NESO forecast is temporarily unavailable."
    schema = responses["503"]["content"]["application/json"]["schema"]
    error_schema = openapi["components"]["schemas"]["CarbonIntensityUnavailableResponse"]
    assert schema["$ref"] == "#/components/schemas/CarbonIntensityUnavailableResponse"
    assert error_schema["required"] == ["detail"]
    assert error_schema["properties"]["detail"]["type"] == "string"


@pytest.mark.asyncio
async def test_neso_client_rejects_oversized_upstream_response() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b" " * (64 * 1024 + 1))

    client = NesoCarbonIntensityClient(transport=httpx.MockTransport(handler))

    with pytest.raises(NesoAPIError, match="response exceeds the 64 KiB limit"):
        await client.fetch_raw()


@pytest.mark.asyncio
async def test_neso_client_rejects_compressed_expansion_before_decompression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expanded_body = b" " * (1024 * 1024)
    compressed_body = gzip.compress(expanded_body)
    request_encodings: list[str | None] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        request_encodings.append(request.headers.get("accept-encoding"))
        return httpx.Response(
            200,
            content=compressed_body,
            headers={"Content-Encoding": "gzip"},
        )

    async def forbidden_decompression(
        _response: httpx.Response,
        _chunk_size: int | None = None,
    ):
        raise AssertionError("compressed upstream bytes must not be decompressed")
        yield b""

    monkeypatch.setattr(httpx.Response, "aiter_bytes", forbidden_decompression)
    client = NesoCarbonIntensityClient(transport=httpx.MockTransport(handler))

    with pytest.raises(NesoAPIError, match="unsupported content encoding"):
        await client.fetch_raw()

    assert request_encodings == ["identity"]


@pytest.mark.asyncio
async def test_neso_client_enforces_a_total_request_deadline() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.05)
        return httpx.Response(503)

    client = NesoCarbonIntensityClient(
        timeout_seconds=0.01,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(NesoAPIError, match="request timed out"):
        await client.fetch_raw()


@pytest.mark.asyncio
async def test_cache_expires_before_a_reading_exceeds_its_freshness_bound() -> None:
    now = datetime.now(UTC)
    start = now - timedelta(seconds=3599)
    reading = parse_regional_response(
        _payload(
            now,
            start=start,
            end=start + timedelta(minutes=30),
        ),
        region_id=13,
        now=now,
        max_age_seconds=3600,
    )

    class CachedClient:
        max_age_seconds = 3600

        def __init__(self) -> None:
            self.calls = 0

        async def fetch_latest(self):
            self.calls += 1
            return reading

    client = CachedClient()
    monotonic_time = [100.0]
    cache = CachedNesoCarbonIntensityReader(
        client,  # type: ignore[arg-type]
        clock=lambda: monotonic_time[0],
    )

    await cache.fetch_latest()
    monotonic_time[0] += 2.0
    await cache.fetch_latest()

    assert client.calls == 2


@pytest.mark.asyncio
async def test_public_simulation_profile_exposes_only_read_only_cached_neso_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    payload = _payload(datetime.now(UTC))

    async def fetch_latest(self: NesoCarbonIntensityClient):
        nonlocal calls
        calls += 1
        return parse_regional_response(
            payload,
            region_id=self.region_id,
            now=datetime.now(UTC),
            max_age_seconds=self.max_age_seconds,
            source_url=self.endpoint_url,
        )

    monkeypatch.setattr(NesoCarbonIntensityClient, "fetch_latest", fetch_latest)
    application = create_app(
        application_settings=Settings(simulation_only_mode=True)
    )

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        first = await client.get("/api/v1/energy/carbon-intensity")
        second = await client.get("/api/v1/energy/carbon-intensity")
        write_attempt = await client.post("/api/v1/energy/carbon-intensity")
        policy_route = await client.post(
            "/api/v1/energy/carbon-gate",
            json={"carbon_intensity_gco2_per_kwh": 100.0},
        )
        unknown_route = await client.get("/api/v1/not-enabled")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["region_name"] == "London"
    assert first.json()["measurement_type"] == "actual"
    assert first.json()["intensity_gco2_per_kwh"] == 79
    assert first.json()["units"] == "gCO2/kWh"
    assert "fresh_until" in first.json()
    assert first.headers["cache-control"] == "public, max-age=30"
    assert second.json()["retrieved_at"] == first.json()["retrieved_at"]
    assert second.json()["fresh_until"] == first.json()["fresh_until"]
    assert second.json()["age_seconds"] >= first.json()["age_seconds"]
    assert calls == 1
    assert write_attempt.status_code == 405
    assert write_attempt.headers["allow"] == "GET"
    assert policy_route.status_code == 404
    assert unknown_route.status_code == 404


@pytest.mark.asyncio
async def test_public_cache_lifetime_is_bounded_by_remaining_freshness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    response_time = now + timedelta(seconds=5)
    payload = _payload(
        now,
        start=now - timedelta(seconds=3590),
        end=now + timedelta(minutes=10),
    )
    reading = parse_regional_response(
        payload,
        region_id=13,
        now=now,
        max_age_seconds=3600,
    )

    async def fetch_latest(_self: NesoCarbonIntensityClient):
        return reading

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return response_time if tz is None else response_time.astimezone(tz)

    monkeypatch.setattr(NesoCarbonIntensityClient, "fetch_latest", fetch_latest)
    monkeypatch.setattr(energy_api, "datetime", FrozenDateTime)
    application = create_app(
        application_settings=Settings(simulation_only_mode=True)
    )

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/v1/energy/carbon-intensity")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=5"
    assert datetime.fromisoformat(response.json()["fresh_until"]) == now + timedelta(seconds=10)
    assert response.json()["age_seconds"] == pytest.approx(3590)


@pytest.mark.asyncio
async def test_public_cache_lifetime_is_bounded_by_forecast_validity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    response_time = now + timedelta(seconds=5)
    valid_until = now + timedelta(seconds=10)
    payload = _payload(
        now,
        start=now - timedelta(minutes=5),
        end=valid_until,
        actual=None,
    )
    reading = parse_regional_response(
        payload,
        region_id=13,
        now=now,
        max_age_seconds=3600,
    )

    async def fetch_latest(_self: NesoCarbonIntensityClient):
        return reading

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return response_time if tz is None else response_time.astimezone(tz)

    monkeypatch.setattr(NesoCarbonIntensityClient, "fetch_latest", fetch_latest)
    monkeypatch.setattr(energy_api, "datetime", FrozenDateTime)
    application = create_app(application_settings=Settings(simulation_only_mode=True))

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/v1/energy/carbon-intensity")

    assert response.status_code == 200
    assert response.json()["measurement_type"] == "forecast"
    assert datetime.fromisoformat(response.json()["fresh_until"]) == valid_until
    assert response.headers["cache-control"] == "public, max-age=5"


@pytest.mark.asyncio
async def test_completed_forecast_is_marked_stale_and_not_publicly_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    payload = _payload(
        now,
        start=now - timedelta(minutes=40),
        end=now - timedelta(minutes=10),
        actual=None,
    )

    async def fetch_latest(self: NesoCarbonIntensityClient):
        return parse_regional_response(
            payload,
            region_id=self.region_id,
            now=now,
            max_age_seconds=self.max_age_seconds,
            source_url=self.endpoint_url,
        )

    monkeypatch.setattr(NesoCarbonIntensityClient, "fetch_latest", fetch_latest)
    application = create_app(application_settings=Settings(simulation_only_mode=True))

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/v1/energy/carbon-intensity")

    assert response.status_code == 200
    assert response.json()["measurement_type"] == "forecast"
    assert response.json()["is_fresh"] is False
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_public_neso_route_is_rate_limited_outside_simulation_only_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    payload = _payload(datetime.now(UTC))

    async def fetch_latest(self: NesoCarbonIntensityClient):
        nonlocal calls
        calls += 1
        return parse_regional_response(
            payload,
            region_id=self.region_id,
            now=datetime.now(UTC),
            max_age_seconds=self.max_age_seconds,
            source_url=self.endpoint_url,
        )

    monkeypatch.setattr(NesoCarbonIntensityClient, "fetch_latest", fetch_latest)
    application = create_app(
        application_settings=Settings(
            simulation_only_mode=False,
            api_rate_limit_requests_per_minute=1,
        )
    )

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        first = await client.get("/api/v1/energy/carbon-intensity")
        second = await client.get("/api/v1/energy/carbon-intensity")

    assert first.status_code == 200
    assert second.status_code == 429
    assert int(second.headers["retry-after"]) >= 1
    assert calls == 1


@pytest.mark.parametrize(
    ("body", "expected_status"),
    [(b"{}", 400), (b"x" * 1025, 413)],
)
@pytest.mark.asyncio
async def test_simulation_only_neso_get_rejects_request_bodies_before_upstream(
    monkeypatch: pytest.MonkeyPatch,
    body: bytes,
    expected_status: int,
) -> None:
    calls = 0

    async def fetch_latest(self: NesoCarbonIntensityClient):
        nonlocal calls
        calls += 1
        return parse_regional_response(
            _payload(datetime.now(UTC)),
            region_id=self.region_id,
            now=datetime.now(UTC),
            max_age_seconds=self.max_age_seconds,
            source_url=self.endpoint_url,
        )

    monkeypatch.setattr(NesoCarbonIntensityClient, "fetch_latest", fetch_latest)
    application = create_app(
        application_settings=Settings(
            simulation_only_mode=True,
            max_request_body_bytes=1024,
        )
    )

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        response = await client.request(
            "GET",
            "/api/v1/energy/carbon-intensity",
            content=body,
        )

    assert response.status_code == expected_status
    assert calls == 0


@pytest.mark.asyncio
async def test_simulation_only_neso_get_rejects_chunked_body_before_upstream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def fetch_latest(self: NesoCarbonIntensityClient):
        nonlocal calls
        calls += 1
        return parse_regional_response(
            _payload(datetime.now(UTC)),
            region_id=self.region_id,
            now=datetime.now(UTC),
            max_age_seconds=self.max_age_seconds,
            source_url=self.endpoint_url,
        )

    async def oversized_body():
        yield b"x" * 1025

    monkeypatch.setattr(NesoCarbonIntensityClient, "fetch_latest", fetch_latest)
    application = create_app(
        application_settings=Settings(
            simulation_only_mode=True,
            max_request_body_bytes=1024,
        )
    )

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        response = await client.request(
            "GET",
            "/api/v1/energy/carbon-intensity",
            content=oversized_body(),
        )

    assert response.status_code == 413
    assert calls == 0


@pytest.mark.asyncio
async def test_simulation_only_neso_get_rejects_fragmented_oversize_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def fetch_latest(self: NesoCarbonIntensityClient):
        nonlocal calls
        calls += 1
        return parse_regional_response(
            _payload(datetime.now(UTC)),
            region_id=self.region_id,
            now=datetime.now(UTC),
            max_age_seconds=self.max_age_seconds,
            source_url=self.endpoint_url,
        )

    async def fragmented_body():
        yield b"x" * 600
        yield b"x" * 600

    monkeypatch.setattr(NesoCarbonIntensityClient, "fetch_latest", fetch_latest)
    application = create_app(
        application_settings=Settings(
            simulation_only_mode=True,
            max_request_body_bytes=1024,
        )
    )

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        response = await client.request(
            "GET",
            "/api/v1/energy/carbon-intensity",
            content=fragmented_body(),
        )

    assert response.status_code == 413
    assert calls == 0
