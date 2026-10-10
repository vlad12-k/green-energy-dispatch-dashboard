"""Application-level API contracts and production defaults."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import ValidationError

from app.api.v1.energy import CarbonIntensityResponse
from app.core.config import Settings
from app.main import app, lifespan
from app.services.neso_carbon_intensity import NesoAPIError, NesoCarbonIntensityClient


@pytest.mark.asyncio
async def test_application_lifespan_initializes_logging(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr("app.main.setup_logging", lambda: calls.append("setup"))
    async with lifespan(app):
        assert calls == ["setup"]


@pytest.mark.asyncio
async def test_only_one_health_route_is_registered() -> None:
    routes = [route.path for route in app.routes if route.path.endswith("/health")]
    assert routes == ["/health"]


@pytest.mark.asyncio
async def test_production_defaults_hide_interactive_api_documentation() -> None:
    assert Settings().debug is False
    assert Settings().app_env == "production"
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/docs")).status_code == 404
        assert (await client.get("/openapi.json")).status_code == 404


def test_production_settings_reject_debug_and_http_upstream() -> None:
    with pytest.raises(ValidationError):
        Settings(app_env="production", debug=True)
    with pytest.raises(ValidationError):
        Settings(neso_api_base_url="http://example.test")


@pytest.mark.parametrize("app_env", ["development", "testing", "production"])
def test_settings_accept_only_supported_environment_names(app_env: str) -> None:
    assert Settings(app_env=app_env).app_env == app_env


@pytest.mark.parametrize("app_env", ["test", "staging", "prod", "", "Production"])
def test_settings_reject_unsupported_environment_names(app_env: str) -> None:
    with pytest.raises(ValidationError):
        Settings(app_env=app_env)


def test_debug_is_allowed_only_outside_production() -> None:
    assert Settings(app_env="development", debug=True).debug is True
    assert Settings(app_env="testing", debug=True).debug is True
    assert Settings(app_env="production").debug is False


@pytest.mark.asyncio
async def test_carbon_intensity_endpoint_returns_fresh_source_evidence(monkeypatch) -> None:
    now = datetime.now(UTC)
    start = now - timedelta(minutes=5)
    payload = {
        "data": [{
            "regionid": 13,
            "dnoregion": "UKPN London",
            "shortname": "London",
            "data": [{
                "from": start.isoformat(),
                "to": (start + timedelta(minutes=30)).isoformat(),
                "intensity": {"forecast": 61, "actual": 59, "index": "low"},
            }],
        }]
    }

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client = NesoCarbonIntensityClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(
        "app.api.v1.energy.NesoCarbonIntensityClient", lambda **_kwargs: client
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
        response = await api.get("/api/v1/energy/carbon-intensity")

    assert response.status_code == 200
    body = response.json()
    assert body["intensity_gco2_per_kwh"] == 59
    assert body["measurement_type"] == "actual"
    assert body["region_id"] == 13
    assert body["interval_status"] == "in_progress"
    assert body["interval_lag_seconds"] == 0
    assert body["is_fresh"] is True
    assert body["source_api"] == "neso-carbon-intensity-api"
    assert body["source_url"].endswith("/regional/regionid/13")
    CarbonIntensityResponse.model_validate(body)


@pytest.mark.asyncio
async def test_carbon_intensity_endpoint_returns_503_on_upstream_failure(monkeypatch) -> None:
    class BrokenClient:
        def __init__(self, **_kwargs) -> None:
            pass

        async def fetch_latest(self):
            raise NesoAPIError("NESO Carbon Intensity API request timed out")

    monkeypatch.setattr("app.api.v1.energy.NesoCarbonIntensityClient", BrokenClient)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
        response = await api.get("/api/v1/energy/carbon-intensity")

    assert response.status_code == 503
    assert "timed out" in response.json()["detail"]


@pytest.mark.parametrize(
    "payload",
    [
        {"data": [None]},
        {"data": [{"regionid": 13, "shortname": "London", "data": [None]}]},
    ],
)
@pytest.mark.asyncio
async def test_carbon_intensity_endpoint_returns_503_for_malformed_json_shapes(
    monkeypatch, payload
) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client = NesoCarbonIntensityClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(
        "app.api.v1.energy.NesoCarbonIntensityClient", lambda **_kwargs: client
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
        response = await api.get("/api/v1/energy/carbon-intensity")

    assert response.status_code == 503
    assert "NESO Carbon Intensity API data rejected" in response.json()["detail"]
