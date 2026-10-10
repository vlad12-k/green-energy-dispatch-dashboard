from httpx import ASGITransport, AsyncClient
import pytest

from app.main import app


@pytest.mark.asyncio
async def test_carbon_gate_allows_simulated_dispatch_at_low_intensity():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/energy/carbon-gate",
            json={"carbon_intensity_gco2_per_kwh": 120},
        )

    assert response.status_code == 200
    assert response.json() == {
        "decision": "allow",
        "reason": "Carbon intensity is within the low-carbon dispatch threshold.",
        "carbon_intensity_gco2_per_kwh": 120.0,
    }


@pytest.mark.asyncio
async def test_carbon_gate_defers_simulated_dispatch_at_medium_intensity():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/energy/carbon-gate",
            json={"carbon_intensity_gco2_per_kwh": 220},
        )

    assert response.status_code == 200
    assert response.json()["decision"] == "defer"


@pytest.mark.asyncio
async def test_carbon_gate_blocks_simulated_dispatch_at_high_intensity():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/energy/carbon-gate",
            json={"carbon_intensity_gco2_per_kwh": 301},
        )

    assert response.status_code == 200
    assert response.json()["decision"] == "block"


@pytest.mark.asyncio
async def test_carbon_gate_rejects_negative_carbon_intensity():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/energy/carbon-gate",
            json={"carbon_intensity_gco2_per_kwh": -1},
        )

    assert response.status_code == 422
