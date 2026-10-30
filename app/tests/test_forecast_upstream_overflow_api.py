"""Regression tests for malformed NESO forecast data at the API boundary."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from app.api.v1 import simulations as simulations_api
from app.core.config import Settings
from app.main import create_app
from app.services.neso_carbon_intensity import (
    CachedNesoCarbonIntensityReader,
    NesoCarbonIntensityClient,
    next_half_hour_start,
)

_FORECAST_EVALUATION_PATH = "/api/v1/simulations/ev-depot/forecast-evaluate"
_REQUEST_TIME = datetime(2026, 10, 30, 12, 10, tzinfo=UTC)
_REQUESTED_FROM = datetime(2026, 10, 30, 12, 30, tzinfo=UTC)


def _valid_upstream_payload(
    *,
    region_name: str = "London",
    overflowing_interval: bool = False,
    requested_from: datetime = _REQUESTED_FROM,
) -> dict[str, Any]:
    intervals = []
    for index in range(48):
        interval_start = requested_from + timedelta(minutes=30 * index)
        intervals.append(
            {
                "from": interval_start.strftime("%Y-%m-%dT%H:%MZ"),
                "to": (interval_start + timedelta(minutes=30)).strftime(
                    "%Y-%m-%dT%H:%MZ"
                ),
                "intensity": {"actual": None, "forecast": 100, "index": "low"},
            }
        )
    if overflowing_interval:
        intervals[0]["from"] = "9999-12-31T23:30:00Z"
        intervals[0]["to"] = "9999-12-31T23:59:00Z"
    return {
        "data": [
            {"regionid": 13, "shortname": region_name, "data": intervals}
        ]
    }


def _scenario_request(start_at: datetime = _REQUESTED_FROM) -> dict[str, Any]:
    scenario_end = start_at + timedelta(hours=2)
    return {
        "scenario": {
            "scenario_id": "upstream-overflow-regression",
            "start_at": start_at.isoformat().replace("+00:00", "Z"),
            "interval_minutes": 30,
            "intervals": 4,
            "depot_power_kw": 7.0,
            "vehicles": [
                {
                    "vehicle_id": "van-1",
                    "availability_start": start_at.isoformat().replace("+00:00", "Z"),
                    "availability_end": scenario_end.isoformat().replace(
                        "+00:00", "Z"
                    ),
                    "required_energy_kwh": 1.0,
                    "charging_power_kw": 7.0,
                }
            ],
        },
        "neso_region_id": 13,
    }


def _application_with_upstream(handler: Any) -> Any:
    neso_client = NesoCarbonIntensityClient(
        region_id=13,
        transport=httpx.MockTransport(handler),
    )
    application = create_app(
        application_settings=Settings(simulation_only_mode=True, neso_region_id=13)
    )
    application.state.neso_carbon_intensity_reader = CachedNesoCarbonIntensityReader(
        neso_client,
        clock=lambda: 1.0,
    )
    return application


async def _post_scenario(
    application: Any,
    payload: dict[str, Any] | None = None,
) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        return await client.post(
            _FORECAST_EVALUATION_PATH,
            json=payload if payload is not None else _scenario_request(),
        )


def _requested_from_from_url(request: httpx.Request) -> datetime:
    timestamp = request.url.path.split("/regional/intensity/", 1)[1].split(
        "/fw24h/", 1
    )[0]
    return datetime.strptime(timestamp, "%Y-%m-%dT%H:%MZ").replace(tzinfo=UTC)


@pytest.mark.asyncio
async def test_unrepresentable_upstream_interval_returns_sanitised_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(simulations_api, "_utc_now", lambda: _REQUEST_TIME)
    upstream_requests: list[httpx.Request] = []

    async def upstream_handler(request: httpx.Request) -> httpx.Response:
        upstream_requests.append(request)
        return httpx.Response(
            200,
            json=_valid_upstream_payload(overflowing_interval=True),
            request=request,
        )

    neso_client = NesoCarbonIntensityClient(
        region_id=13,
        transport=httpx.MockTransport(upstream_handler),
    )
    application = create_app(
        application_settings=Settings(simulation_only_mode=True, neso_region_id=13)
    )
    application.state.neso_carbon_intensity_reader = CachedNesoCarbonIntensityReader(
        neso_client,
        clock=lambda: 1.0,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.post(
            _FORECAST_EVALUATION_PATH,
            json=_scenario_request(),
        )

    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    result = response.json()
    assert result["status"] == "unavailable"
    assert result["unavailable_detail"] == "upstream_validation_error"
    assert result["input_provenance"] is None
    assert "9999-12-31" not in response.text
    assert len(upstream_requests) == 1
    assert upstream_requests[0].method == "GET"


@pytest.mark.asyncio
async def test_deeply_nested_upstream_json_returns_sanitised_503_without_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(simulations_api, "_utc_now", lambda: _REQUEST_TIME)
    upstream_requests: list[httpx.Request] = []
    nested_json = b'{"data":' + b"[" * 10000 + b"0" + b"]" * 10000 + b"}"
    assert len(nested_json) < 64 * 1024
    with pytest.raises(RecursionError):
        json.loads(nested_json)

    async def upstream_handler(request: httpx.Request) -> httpx.Response:
        upstream_requests.append(request)
        return httpx.Response(
            200,
            content=nested_json,
            headers={"Content-Type": "application/json"},
            request=request,
        )

    application = _application_with_upstream(upstream_handler)
    responses = [await _post_scenario(application) for _ in range(2)]

    for response in responses:
        assert response.status_code == 503
        assert response.headers["cache-control"] == "no-store"
        assert "RecursionError" not in response.text
        assert "maximum recursion" not in response.text
        result = response.json()
        assert result["status"] == "unavailable"
        assert result["unavailable_detail"] == "upstream_validation_error"
        assert result["input_provenance"] is None
        assert result["immediate_baseline"] is None
        assert result["forecast_aware"] is None
    assert len(upstream_requests) == 2


@pytest.mark.asyncio
async def test_surrogate_region_shortname_returns_sanitised_503_without_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(simulations_api, "_utc_now", lambda: datetime.now(UTC))
    upstream_requests: list[httpx.Request] = []
    scenario_start = next_half_hour_start(datetime.now(UTC)) + timedelta(hours=1)

    async def upstream_handler(request: httpx.Request) -> httpx.Response:
        upstream_requests.append(request)
        forecast_start = _requested_from_from_url(request)
        invalid_payload = _valid_upstream_payload(
            region_name="\ud800",
            requested_from=forecast_start,
        )
        invalid_json = json.dumps(invalid_payload, ensure_ascii=True).encode("utf-8")
        assert b"\\ud800" in invalid_json
        return httpx.Response(
            200,
            content=invalid_json,
            headers={"Content-Type": "application/json"},
            request=request,
        )

    application = _application_with_upstream(upstream_handler)
    scenario_payload = _scenario_request(scenario_start)
    responses = [
        await _post_scenario(application, scenario_payload) for _ in range(2)
    ]

    for response in responses:
        assert response.status_code == 503
        assert response.headers["cache-control"] == "no-store"
        assert "\\ud800" not in response.text
        assert "surrogate" not in response.text.lower()
        result = response.json()
        assert result["status"] == "unavailable"
        assert result["unavailable_detail"] == "upstream_validation_error"
        assert result["input_provenance"] is None
        assert result["immediate_baseline"] is None
        assert result["forecast_aware"] is None
    assert len(upstream_requests) == 2
