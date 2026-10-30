"""Forecast-backed EV-depot scheduling API contract tests."""

from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx
import pytest
from sqlalchemy.engine import Engine

from app.api.v1 import simulations as simulations_api
from app.core.config import Settings
from app.main import create_app
from app.services import neso_carbon_intensity as neso_service
from app.services.neso_carbon_intensity import (
    CachedNesoCarbonIntensityReader,
    NesoAPIError,
    NesoCarbonIntensityClient,
    parse_regional_forecast_response,
)

FORECAST_EVALUATION_PATH = "/api/v1/simulations/ev-depot/forecast-evaluate"
_NOW = datetime(2026, 10, 30, 12, 10, tzinfo=UTC)


def _request_payload(
    *,
    start_at: datetime | None = None,
    intervals: int = 4,
    neso_region_id: int | None = 13,
) -> dict[str, Any]:
    start_at = start_at or datetime(2026, 10, 30, 12, 30, tzinfo=UTC)
    horizon_end = start_at + timedelta(minutes=30 * intervals)
    return {
        "scenario": {
            "scenario_id": "api-forecast-depot",
            "start_at": start_at.isoformat().replace("+00:00", "Z"),
            "interval_minutes": 30,
            "intervals": intervals,
            "depot_power_kw": 7.0,
            "vehicles": [
                {
                    "vehicle_id": "van-1",
                    "availability_start": start_at.isoformat().replace("+00:00", "Z"),
                    "availability_end": horizon_end.isoformat().replace("+00:00", "Z"),
                    "required_energy_kwh": 8.5,
                    "charging_power_kw": 7.0,
                }
            ],
        },
        **({} if neso_region_id is None else {"neso_region_id": neso_region_id}),
    }


def _forecast_payload(
    requested_from: datetime,
    *,
    region_id: int = 13,
) -> dict[str, Any]:
    intervals = []
    for index in range(48):
        start = requested_from + timedelta(minutes=30 * index)
        intervals.append(
            {
                "from": start.isoformat().replace("+00:00", "Z"),
                "to": (start + timedelta(minutes=30)).isoformat().replace("+00:00", "Z"),
                "intensity": {
                    "actual": None,
                    "forecast": 400.0 if index == 0 else 50.0,
                    "index": "high" if index == 0 else "low",
                },
            }
        )
    return {
        "data": [
            {
                "regionid": region_id,
                "shortname": "London",
                "data": intervals,
            }
        ]
    }


class _ForecastReader:
    max_age_seconds = 3600

    def __init__(
        self,
        *,
        retrieved_at: datetime = _NOW,
        unavailable: bool = False,
        error: NesoAPIError | None = None,
    ) -> None:
        self.retrieved_at = retrieved_at
        self.unavailable = unavailable
        self.error = error
        self.calls: list[tuple[int, datetime]] = []

    async def fetch_forecast_timeline(
        self,
        *,
        region_id: int,
        requested_from: datetime,
    ):
        self.calls.append((region_id, requested_from))
        if self.error is not None:
            raise self.error
        if self.unavailable:
            return None
        return parse_regional_forecast_response(
            _forecast_payload(requested_from, region_id=region_id),
            region_id=region_id,
            requested_from=requested_from,
            retrieved_at=self.retrieved_at,
        )


class _TimelineClient:
    max_age_seconds = 3600

    def __init__(self) -> None:
        self.calls: list[tuple[int, datetime]] = []

    async def fetch_forecast_timeline(
        self,
        *,
        region_id: int,
        requested_from: datetime,
    ):
        self.calls.append((region_id, requested_from))
        return parse_regional_forecast_response(
            _forecast_payload(requested_from, region_id=region_id),
            region_id=region_id,
            requested_from=requested_from,
            retrieved_at=_NOW,
        )


@pytest.mark.asyncio
async def test_forecast_evaluation_returns_validated_advisory_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(simulations_api, "_utc_now", lambda: _NOW, raising=False)
    application = create_app(
        application_settings=Settings(simulation_only_mode=True, neso_region_id=13)
    )
    reader = _ForecastReader()
    application.state.neso_carbon_intensity_reader = reader

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        response = await client.post(
            FORECAST_EVALUATION_PATH,
            json=_request_payload(neso_region_id=None),
        )

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "available"
    assert result["input_provenance"]["region_id"] == 13
    assert result["input_provenance"]["source_api"] == "neso-carbon-intensity-api"
    assert result["input_provenance"]["forecast_retrieved_at"].replace("Z", "+00:00") == _NOW.isoformat()
    assert result["input_provenance"]["decision_time"].replace("Z", "+00:00") == _NOW.isoformat()
    assert result["input_provenance"]["source_classification"] == "regional_forecast"
    assert result["input_provenance"]["measurement_type"] == "forecast"
    assert result["input_provenance"]["intensity_unit"] == "gCO2/kWh"
    assert result["input_provenance"]["interval_count"] == 48
    assert result["input_provenance"]["source_url"] == (
        "https://api.carbonintensity.org.uk/regional/intensity/"
        "2026-10-30T12:30Z/fw24h/regionid/13"
    )
    assert result["immediate_baseline"]["feasible"] is True
    assert result["forecast_aware"]["feasible"] is True
    assert result["comparison_eligible"] is True
    assert result["carbon_proxy_unit"] == "gCO2"
    assert result["advisory_only"] is True
    assert result["forecast_based"] is True
    assert result["measured"] is False
    assert result["real_world_emissions_reduction_claimed"] is False
    for strategy in (result["immediate_baseline"], result["forecast_aware"]):
        assert strategy["delivered_energy_kwh"]["van-1"] + strategy["unmet_energy_kwh"]["van-1"] == pytest.approx(8.5)
        assert strategy["peak_depot_power_kw"] <= 7.0
        assert all(
            check
            for check in strategy["constraint_checks"].values()
        )
        assert all(row["peak_power_kw"] <= 7.0 for row in strategy["aggregate_depot_load"])
        for allocation in strategy["allocations"]:
            assert allocation["charging_power_kw"] <= 7.0
            assert allocation["segment_start"] >= "2026-10-30T12:30:00Z"
            assert allocation["segment_end"] <= "2026-10-30T14:30:00Z"
    assert reader.calls == [(13, datetime(2026, 10, 30, 12, 30, tzinfo=UTC))]


@pytest.mark.asyncio
async def test_forecast_api_is_deterministic_stateless_and_uses_validated_neso_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_database_connection(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("forecast evaluation must not open a database connection")

    monkeypatch.setattr(Engine, "connect", reject_database_connection)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return _NOW.astimezone(tz) if tz is not None else _NOW.replace(tzinfo=None)

    monkeypatch.setattr(simulations_api, "_utc_now", lambda: _NOW, raising=False)
    monkeypatch.setattr(neso_service, "datetime", FrozenDateTime)
    upstream_requests: list[httpx.Request] = []

    async def upstream_handler(upstream_request: httpx.Request) -> httpx.Response:
        upstream_requests.append(upstream_request)
        requested_from = datetime(2026, 10, 30, 12, 30, tzinfo=UTC)
        return httpx.Response(
            200,
            json=_forecast_payload(requested_from),
            request=upstream_request,
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
        transport=httpx.ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        request_payload = _request_payload(neso_region_id=None)
        response = await client.post(
            FORECAST_EVALUATION_PATH,
            json=request_payload,
        )
        repeated_response = await client.post(
            FORECAST_EVALUATION_PATH,
            json=request_payload,
        )

    assert response.status_code == 200, response.text
    assert repeated_response.status_code == 200, repeated_response.text
    assert repeated_response.json() == response.json()
    assert response.json()["status"] == "available"
    assert response.json()["input_provenance"]["forecast_retrieved_at"].replace("Z", "+00:00") == _NOW.isoformat()
    assert len(upstream_requests) == 1
    assert upstream_requests[0].method == "GET"
    assert str(upstream_requests[0].url) == (
        "https://api.carbonintensity.org.uk/regional/intensity/"
        "2026-10-30T12:30Z/fw24h/regionid/13"
    )
    assert upstream_requests[0].headers["accept-encoding"] == "identity"


@pytest.mark.asyncio
async def test_forecast_evaluation_has_a_specific_bounded_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(simulations_api, "_utc_now", lambda: _NOW, raising=False)
    application = create_app(
        application_settings=Settings(
            simulation_only_mode=True,
            neso_region_id=13,
            api_rate_limit_requests_per_minute=60,
            forecast_evaluation_rate_limit_requests_per_minute=1,
        )
    )
    reader = _ForecastReader()
    application.state.neso_carbon_intensity_reader = reader

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        first = await client.post(
            FORECAST_EVALUATION_PATH,
            json=_request_payload(),
        )
        second = await client.post(
            FORECAST_EVALUATION_PATH,
            json=_request_payload(),
        )

    assert first.status_code == 200
    assert second.status_code == 429
    assert len(reader.calls) == 1


@pytest.mark.asyncio
async def test_forecast_evaluation_consumes_shared_neso_request_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(simulations_api, "_utc_now", lambda: _NOW, raising=False)
    application = create_app(
        application_settings=Settings(
            simulation_only_mode=True,
            neso_region_id=13,
            api_rate_limit_requests_per_minute=1,
            forecast_evaluation_rate_limit_requests_per_minute=5,
        )
    )
    reader = _ForecastReader()
    application.state.neso_carbon_intensity_reader = reader

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        forecast_evaluation = await client.post(
            FORECAST_EVALUATION_PATH,
            json=_request_payload(),
        )
        timeline_read = await client.get(
            "/api/v1/energy/carbon-intensity/forecast?region_id=13"
        )

    assert forecast_evaluation.status_code == 200
    assert timeline_read.status_code == 429
    assert timeline_read.headers["retry-after"]
    assert len(reader.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("untrusted_field", "untrusted_value"),
    [
        ("forecast", {"values": ["CLIENT_FORECAST_VALUE"]}),
        ("forecast_retrieved_at", "CLIENT_RETRIEVAL_TIME"),
        ("decision_time", "CLIENT_DECISION_TIME"),
        ("input_provenance", {"source_url": "https://private-token.example.invalid"}),
        ("scheduling_result", {"carbon_proxy": 999}),
        ("forecast_url", "https://private-token.example.invalid/credential"),
    ],
)
async def test_client_supplied_forecast_evidence_is_rejected_without_echoing_values(
    untrusted_field: str,
    untrusted_value: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(simulations_api, "_utc_now", lambda: _NOW, raising=False)
    application = create_app(
        application_settings=Settings(simulation_only_mode=True, neso_region_id=13)
    )
    reader = _ForecastReader()
    application.state.neso_carbon_intensity_reader = reader
    payload = _request_payload()
    payload[untrusted_field] = untrusted_value

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        response = await client.post(FORECAST_EVALUATION_PATH, json=payload)

    assert response.status_code == 422
    assert "CLIENT_" not in response.text
    assert "private-token.example.invalid" not in response.text
    assert len(reader.calls) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_case",
    [
        "too_many_intervals",
        "too_many_vehicles",
        "unsupported_interval",
        "non_utc_timestamp",
        "malformed_timestamp",
        "client_decision_time",
        "nested_provenance",
        "oversized_identifier",
        "invalid_region",
    ],
)
async def test_forecast_request_schema_rejects_untrusted_or_unbounded_inputs(
    invalid_case: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(simulations_api, "_utc_now", lambda: _NOW, raising=False)
    application = create_app(
        application_settings=Settings(simulation_only_mode=True, neso_region_id=13)
    )
    reader = _ForecastReader()
    application.state.neso_carbon_intensity_reader = reader
    payload = _request_payload()
    scenario = payload["scenario"]

    if invalid_case == "too_many_intervals":
        scenario["intervals"] = 49
    elif invalid_case == "too_many_vehicles":
        vehicle = scenario["vehicles"][0]
        scenario["vehicles"] = [
            {**vehicle, "vehicle_id": f"van-{index}"}
            for index in range(51)
        ]
    elif invalid_case == "unsupported_interval":
        scenario["interval_minutes"] = 60
    elif invalid_case == "non_utc_timestamp":
        scenario["start_at"] = "2026-10-30T13:30:00+01:00"
        scenario["vehicles"][0]["availability_start"] = "2026-10-30T13:30:00+01:00"
        scenario["vehicles"][0]["availability_end"] = "2026-10-30T15:30:00+01:00"
    elif invalid_case == "malformed_timestamp":
        scenario["start_at"] = "not-a-timestamp"
    elif invalid_case == "client_decision_time":
        payload["decision_time"] = "2026-10-30T12:10:00Z"
    elif invalid_case == "nested_provenance":
        scenario["source_url"] = "https://private-token.example.invalid"
    elif invalid_case == "oversized_identifier":
        scenario["scenario_id"] = "s" * 121
    else:
        payload["neso_region_id"] = 18

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        response = await client.post(FORECAST_EVALUATION_PATH, json=payload)

    assert response.status_code == 422, invalid_case
    assert len(reader.calls) == 0


@pytest.mark.asyncio
async def test_unrepresentable_scenario_horizon_returns_sanitised_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(simulations_api, "_utc_now", lambda: _NOW, raising=False)
    application = create_app(
        application_settings=Settings(simulation_only_mode=True, neso_region_id=13)
    )
    reader = _ForecastReader()
    application.state.neso_carbon_intensity_reader = reader
    payload = _request_payload()
    payload["scenario"]["start_at"] = "9999-12-31T23:30:00Z"
    payload["scenario"]["intervals"] = 48

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.post(FORECAST_EVALUATION_PATH, json=payload)

    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"
    assert "9999-12-31" not in response.text
    assert len(reader.calls) == 0


@pytest.mark.asyncio
async def test_mismatched_client_region_is_rejected_before_forecast_retrieval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(simulations_api, "_utc_now", lambda: _NOW, raising=False)
    application = create_app(
        application_settings=Settings(simulation_only_mode=True, neso_region_id=13)
    )
    reader = _ForecastReader()
    application.state.neso_carbon_intensity_reader = reader

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        response = await client.post(
            FORECAST_EVALUATION_PATH,
            json=_request_payload(neso_region_id=12),
        )

    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"
    assert len(reader.calls) == 0


@pytest.mark.asyncio
async def test_scenario_before_next_eligible_boundary_is_rejected_before_retrieval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(simulations_api, "_utc_now", lambda: _NOW, raising=False)
    application = create_app(
        application_settings=Settings(simulation_only_mode=True, neso_region_id=13)
    )
    reader = _ForecastReader()
    application.state.neso_carbon_intensity_reader = reader

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        response = await client.post(
            FORECAST_EVALUATION_PATH,
            json=_request_payload(start_at=datetime(2026, 10, 30, 12, 0, tzinfo=UTC)),
        )

    assert response.status_code == 422
    assert len(reader.calls) == 0


@pytest.mark.asyncio
async def test_scenario_at_current_boundary_must_wait_for_the_next_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boundary_now = datetime(2026, 10, 30, 12, 30, tzinfo=UTC)
    monkeypatch.setattr(simulations_api, "_utc_now", lambda: boundary_now, raising=False)
    application = create_app(
        application_settings=Settings(simulation_only_mode=True, neso_region_id=13)
    )
    reader = _ForecastReader()
    application.state.neso_carbon_intensity_reader = reader

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        response = await client.post(
            FORECAST_EVALUATION_PATH,
            json=_request_payload(start_at=boundary_now),
        )

    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"
    assert len(reader.calls) == 0


@pytest.mark.asyncio
async def test_scenario_that_becomes_past_at_decision_time_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    times = iter((_NOW, _NOW + timedelta(minutes=21)))
    monkeypatch.setattr(simulations_api, "_utc_now", lambda: next(times), raising=False)
    application = create_app(
        application_settings=Settings(simulation_only_mode=True, neso_region_id=13)
    )
    reader = _ForecastReader()
    application.state.neso_carbon_intensity_reader = reader

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        response = await client.post(
            FORECAST_EVALUATION_PATH,
            json=_request_payload(),
        )

    assert response.status_code == 422
    assert len(reader.calls) == 1


@pytest.mark.asyncio
async def test_decision_time_is_captured_after_retrieval_and_is_server_owned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    times = iter((_NOW, _NOW + timedelta(minutes=20)))
    monkeypatch.setattr(simulations_api, "_utc_now", lambda: next(times), raising=False)
    application = create_app(
        application_settings=Settings(simulation_only_mode=True, neso_region_id=13)
    )
    reader = _ForecastReader(retrieved_at=_NOW + timedelta(minutes=10))
    application.state.neso_carbon_intensity_reader = reader

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        response = await client.post(
            FORECAST_EVALUATION_PATH,
            json=_request_payload(start_at=datetime(2026, 10, 30, 13, 0, tzinfo=UTC)),
        )

    assert response.status_code == 200, response.text
    provenance = response.json()["input_provenance"]
    retrieved_at = datetime.fromisoformat(provenance["forecast_retrieved_at"].replace("Z", "+00:00"))
    decision_time = datetime.fromisoformat(provenance["decision_time"].replace("Z", "+00:00"))
    assert retrieved_at == _NOW + timedelta(minutes=10)
    assert decision_time == _NOW + timedelta(minutes=20)
    assert retrieved_at <= decision_time


@pytest.mark.asyncio
async def test_unavailable_forecast_returns_sanitised_503_without_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(simulations_api, "_utc_now", lambda: _NOW, raising=False)
    application = create_app(
        application_settings=Settings(simulation_only_mode=True, neso_region_id=13)
    )
    reader = _ForecastReader(
        error=NesoAPIError("UPSTREAM_PRIVATE_TOKEN", retryable=True)
    )
    application.state.neso_carbon_intensity_reader = reader

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        response = await client.post(
            FORECAST_EVALUATION_PATH,
            json=_request_payload(),
        )

    assert response.status_code == 503
    assert "UPSTREAM_PRIVATE_TOKEN" not in response.text
    result = response.json()
    assert result["status"] == "unavailable"
    assert result["unavailable_reason"] == "forecast_unavailable"
    assert result["unavailable_detail"] == "upstream_error"
    assert result["input_provenance"] is None
    assert result["immediate_baseline"] is None
    assert result["forecast_aware"] is None
    assert result["comparison_eligible"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure_kind", "expected_detail"),
    [
        ("timeout", "upstream_error"),
        ("http_503", "upstream_error"),
        ("malformed_json", "upstream_validation_error"),
        ("invalid_forecast_payload", "upstream_validation_error"),
        ("oversized_response", "upstream_validation_error"),
    ],
)
async def test_neso_timeout_malformed_and_oversized_responses_fail_safely(
    failure_kind: str,
    expected_detail: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(simulations_api, "_utc_now", lambda: _NOW, raising=False)
    upstream_requests: list[tuple[str, str]] = []

    async def upstream_handler(upstream_request: httpx.Request) -> httpx.Response:
        upstream_requests.append((upstream_request.method, str(upstream_request.url)))
        if failure_kind == "timeout":
            raise httpx.ReadTimeout("PRIVATE_UPSTREAM_PATH", request=upstream_request)
        if failure_kind == "http_503":
            return httpx.Response(
                503,
                text="UPSTREAM_PRIVATE_TOKEN",
                request=upstream_request,
            )
        if failure_kind == "malformed_json":
            return httpx.Response(200, content=b"{invalid", request=upstream_request)
        if failure_kind == "invalid_forecast_payload":
            payload = _forecast_payload(datetime(2026, 10, 30, 12, 30, tzinfo=UTC))
            payload["data"][0]["data"][0]["intensity"]["actual"] = 10.0
            return httpx.Response(200, json=payload, request=upstream_request)
        return httpx.Response(
            200,
            content=b"x" * 65_537,
            request=upstream_request,
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
        transport=httpx.ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        response = await client.post(
            FORECAST_EVALUATION_PATH,
            json=_request_payload(),
        )

    assert response.status_code == 503
    assert response.json()["unavailable_detail"] == expected_detail
    assert "PRIVATE_UPSTREAM_PATH" not in response.text
    assert "UPSTREAM_PRIVATE_TOKEN" not in response.text
    assert len(upstream_requests) == 1
    assert upstream_requests[0][0] == "GET"
    assert upstream_requests[0][1] == (
        "https://api.carbonintensity.org.uk/regional/intensity/"
        "2026-10-30T12:30Z/fw24h/regionid/13"
    )


@pytest.mark.asyncio
async def test_missing_forecast_returns_explicit_unavailable_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(simulations_api, "_utc_now", lambda: _NOW, raising=False)
    application = create_app(
        application_settings=Settings(simulation_only_mode=True, neso_region_id=13)
    )
    reader = _ForecastReader(unavailable=True)
    application.state.neso_carbon_intensity_reader = reader

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        response = await client.post(
            FORECAST_EVALUATION_PATH,
            json=_request_payload(),
        )

    assert response.status_code == 503
    assert response.json()["unavailable_detail"] == "explicitly_unavailable"


@pytest.mark.asyncio
async def test_stale_forecast_is_rejected_with_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(simulations_api, "_utc_now", lambda: _NOW, raising=False)
    application = create_app(
        application_settings=Settings(simulation_only_mode=True, neso_region_id=13)
    )
    reader = _ForecastReader(retrieved_at=_NOW - timedelta(seconds=3601))
    application.state.neso_carbon_intensity_reader = reader

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        response = await client.post(
            FORECAST_EVALUATION_PATH,
            json=_request_payload(),
        )

    assert response.status_code == 503
    assert response.json()["unavailable_reason"] == "stale_forecast"
    assert response.json()["input_provenance"] is None


@pytest.mark.asyncio
async def test_cached_timeline_that_no_longer_covers_scenario_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return _NOW.astimezone(tz) if tz is not None else _NOW.replace(tzinfo=None)

    decision_times = iter((_NOW, _NOW, _NOW + timedelta(minutes=10), _NOW + timedelta(minutes=10)))
    monkeypatch.setattr(simulations_api, "_utc_now", lambda: next(decision_times), raising=False)
    monkeypatch.setattr(neso_service, "datetime", FrozenDateTime)
    application = create_app(
        application_settings=Settings(simulation_only_mode=True, neso_region_id=13)
    )
    client = _TimelineClient()
    reader = CachedNesoCarbonIntensityReader(cast(Any, client), clock=lambda: 1.0)
    application.state.neso_carbon_intensity_reader = reader

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://test",
    ) as api:
        first = await api.post(
            FORECAST_EVALUATION_PATH,
            json=_request_payload(intervals=48),
        )
        second = await api.post(
            FORECAST_EVALUATION_PATH,
            json=_request_payload(
                start_at=datetime(2026, 10, 30, 13, 0, tzinfo=UTC),
                intervals=48,
            ),
        )

    assert first.status_code == 200
    assert second.status_code == 503
    assert second.json()["unavailable_reason"] == "missing_interval_coverage"
    assert client.calls == [(13, datetime(2026, 10, 30, 12, 30, tzinfo=UTC))]


@pytest.mark.asyncio
async def test_horizon_outside_forecast_coverage_returns_503_without_synthetic_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(simulations_api, "_utc_now", lambda: _NOW, raising=False)
    application = create_app(
        application_settings=Settings(simulation_only_mode=True, neso_region_id=13)
    )
    reader = _ForecastReader()
    application.state.neso_carbon_intensity_reader = reader
    scenario_start = datetime(2026, 10, 31, 12, 30, tzinfo=UTC)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        response = await client.post(
            FORECAST_EVALUATION_PATH,
            json=_request_payload(start_at=scenario_start, intervals=1),
        )

    assert response.status_code == 503
    assert response.json()["unavailable_reason"] == "missing_interval_coverage"
    assert response.json()["immediate_baseline"] is None
    assert response.json()["forecast_aware"] is None


@pytest.mark.asyncio
async def test_available_forecast_with_incomplete_service_returns_200_without_comparison(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(simulations_api, "_utc_now", lambda: _NOW, raising=False)
    application = create_app(
        application_settings=Settings(simulation_only_mode=True, neso_region_id=13)
    )
    reader = _ForecastReader()
    application.state.neso_carbon_intensity_reader = reader
    payload = _request_payload()
    payload["scenario"]["vehicles"][0]["required_energy_kwh"] = 1000.0

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        response = await client.post(FORECAST_EVALUATION_PATH, json=payload)

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "available"
    assert result["immediate_baseline"]["feasible"] is False
    assert result["forecast_aware"]["feasible"] is False
    assert result["comparison_eligible"] is False
    assert result["absolute_carbon_proxy_reduction"] is None
    assert result["comparison_unavailable_reason"]


@pytest.mark.asyncio
async def test_forecast_evaluation_rejects_unsupported_methods_without_retrieval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(simulations_api, "_utc_now", lambda: _NOW, raising=False)
    application = create_app(
        application_settings=Settings(simulation_only_mode=True, neso_region_id=13)
    )
    reader = _ForecastReader()
    application.state.neso_carbon_intensity_reader = reader

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        response = await client.get(FORECAST_EVALUATION_PATH)

    assert response.status_code == 405
    assert response.headers["allow"] == "POST"
    assert len(reader.calls) == 0


@pytest.mark.asyncio
async def test_forecast_evaluation_body_limit_counts_fragmented_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(simulations_api, "_utc_now", lambda: _NOW, raising=False)
    application = create_app(
        application_settings=Settings(
            simulation_only_mode=True,
            neso_region_id=13,
            max_request_body_bytes=1024,
        )
    )
    reader = _ForecastReader()
    application.state.neso_carbon_intensity_reader = reader

    async def oversized_body():
        yield b'{"scenario":'
        yield b" " * 2048
        yield b"}"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        response = await client.post(
            FORECAST_EVALUATION_PATH,
            content=oversized_body(),
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 413
    assert len(reader.calls) == 0
