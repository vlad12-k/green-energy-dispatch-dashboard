"""HTTP contract tests for stateless EV-depot simulations."""

import copy
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.main import app


DEMO_PATH = Path(__file__).parents[2] / "examples" / "carbon_aware_ev_depot_demo.json"


def _demo_payload() -> dict[str, Any]:
    return json.loads(DEMO_PATH.read_text(encoding="utf-8"))


def _interval_rows(start_at: str, count: int, interval_minutes: int) -> list[dict[str, object]]:
    start = datetime.fromisoformat(start_at.replace("Z", "+00:00"))
    rows = []
    for index in range(count):
        interval_start = start + timedelta(minutes=interval_minutes * index)
        interval_end = interval_start + timedelta(minutes=interval_minutes)
        rows.append(
            {
                "interval_start": interval_start.isoformat().replace("+00:00", "Z"),
                "interval_end": interval_end.isoformat().replace("+00:00", "Z"),
                "intensity_value": 100.0,
                "intensity_unit": "gCO2/kWh",
                "source_classification": "synthetic_scenario",
                "source_id": "synthetic-scenario",
                "geography_code": "simulation-only",
                "provenance_label": "synthetic:api-test",
            }
        )
    return rows


async def _post(payload: dict[str, Any]) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/api/v1/simulations/ev-depot/evaluate", json=payload)


@pytest.mark.asyncio
async def test_evaluate_returns_real_scheduler_result_for_synthetic_demo() -> None:
    response = await _post(_demo_payload())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scenario_id"] == "synthetic-carbon-shift-demo"
    assert body["baseline"]["feasible"] is True
    assert body["carbon_aware"]["feasible"] is True
    assert body["baseline_carbon_proxy"] == pytest.approx(2900.0)
    assert body["carbon_aware_carbon_proxy"] == pytest.approx(1100.0)
    assert body["comparison_eligible"] is True
    assert body["absolute_carbon_proxy_reduction"] == pytest.approx(1800.0)
    assert body["input_provenance"]["source_classification"] == "synthetic_scenario"
    assert body["real_world_emissions_reduction_claimed"] is False
    assert len(body["carbon_aware"]["allocations"]) > 0


@pytest.mark.asyncio
async def test_evaluate_reports_incomplete_service_without_claiming_infeasibility() -> None:
    payload = _demo_payload()
    payload["vehicles"][0]["required_energy_kwh"] = 20.0

    response = await _post(payload)

    assert response.status_code == 200
    body = response.json()
    assert body["baseline_requirements_met_for_comparison"] is False
    assert body["carbon_aware_feasible"] is False
    assert body["comparison_eligible"] is False
    assert body["absolute_carbon_proxy_reduction"] is None
    assert "does not prove" in body["comparison_unavailable_reason"]


@pytest.mark.asyncio
async def test_evaluate_rejects_malformed_and_non_synthetic_inputs() -> None:
    malformed = _demo_payload()
    del malformed["carbon_intensity_profile"]
    unsupported = _demo_payload()
    unsupported["carbon_intensity_profile"][0]["source_classification"] = "observed_actual"

    malformed_response, unsupported_response = await _post(malformed), await _post(unsupported)

    assert malformed_response.status_code == 422
    assert unsupported_response.status_code == 422
    assert "detail" in malformed_response.json()
    assert "detail" in unsupported_response.json()


@pytest.mark.asyncio
async def test_evaluate_enforces_96_interval_limit() -> None:
    payload = _demo_payload()
    payload["interval_minutes"] = 15
    payload["intervals"] = 97
    payload["vehicles"][0]["availability_end"] = "2026-01-02T00:15:00Z"
    payload["carbon_intensity_profile"] = _interval_rows(payload["start_at"], 97, 15)

    response = await _post(payload)

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_evaluate_enforces_50_vehicle_limit() -> None:
    payload = _demo_payload()
    vehicle = payload["vehicles"][0]
    payload["vehicles"] = [
        {**copy.deepcopy(vehicle), "vehicle_id": f"van-{index:02d}"}
        for index in range(51)
    ]

    response = await _post(payload)

    assert response.status_code == 422
