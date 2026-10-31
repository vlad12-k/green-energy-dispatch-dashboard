"""Deterministic FastAPI boundary for browser E2E tests; no upstream network calls."""

from datetime import UTC, datetime, timedelta

import httpx
from fastapi import HTTPException

import app.main as app_main
from app.core.config import Settings
from app.services.neso_carbon_intensity import (
    SOURCE_API,
    CarbonIntensityForecastInterval,
    CarbonIntensityForecastTimeline,
    CarbonIntensityReading,
    NesoCarbonIntensityClient,
)

E2E_FIXTURE_ID = "ged-b3-e2e-deterministic-v1"


class NoOutboundNesoTransport(httpx.AsyncBaseTransport):
    """Fail closed and count any attempted NESO HTTP request in browser tests."""

    def __init__(self) -> None:
        self.request_attempts = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.request_attempts += 1
        raise httpx.ConnectError(
            "Outbound NESO access is disabled in deterministic E2E tests",
            request=request,
        )


class DeterministicNesoReader:
    """Test-only upstream replacement; public API routes and scheduler stay real."""

    max_age_seconds = 3600

    async def fetch_latest(self) -> CarbonIntensityReading:
        now = datetime.now(UTC)
        interval_start = now.replace(minute=(now.minute // 30) * 30, second=0, microsecond=0)
        return CarbonIntensityReading(
            intensity_gco2_per_kwh=180.0,
            measurement_type="forecast",
            region_id=13,
            region_name="E2E deterministic fixture",
            intensity_index="moderate",
            observed_at=interval_start,
            valid_until=interval_start + timedelta(minutes=30),
            retrieved_at=now,
            interval_status="in_progress",
            interval_lag_seconds=0.0,
            is_fresh=True,
            age_seconds=0.0,
            source_api=SOURCE_API,
            source_url="https://api.carbonintensity.org.uk/regional/regionid/13",
        )

    async def fetch_forecast_timeline(
        self,
        *,
        region_id: int,
        requested_from: datetime,
    ) -> CarbonIntensityForecastTimeline:
        start = requested_from.astimezone(UTC)
        intervals = tuple(
            CarbonIntensityForecastInterval(
                observed_at=start + timedelta(minutes=30 * index),
                valid_until=start + timedelta(minutes=30 * (index + 1)),
                intensity_gco2_per_kwh=(400.0, 220.0, 60.0)[index % 3],
                intensity_index=("high", "moderate", "low")[index % 3],
            )
            for index in range(48)
        )
        timestamp = start.strftime("%Y-%m-%dT%H:%MZ")
        return CarbonIntensityForecastTimeline(
            region_id=region_id,
            region_name="E2E deterministic fixture",
            requested_from=start,
            valid_until=start + timedelta(hours=24),
            retrieved_at=datetime.now(UTC),
            intervals=intervals,
            source_api=SOURCE_API,
            source_url=(
                "https://api.carbonintensity.org.uk/regional/intensity/"
                f"{timestamp}/fw24h/regionid/{region_id}"
            ),
        )


neso_network_guard = NoOutboundNesoTransport()


def _guarded_neso_client(
    *,
    base_url: str,
    region_id: int,
    max_age_seconds: int,
    timeout_seconds: float,
) -> NesoCarbonIntensityClient:
    return NesoCarbonIntensityClient(
        base_url=base_url,
        region_id=region_id,
        max_age_seconds=max_age_seconds,
        timeout_seconds=timeout_seconds,
        transport=neso_network_guard,
    )


app_main.NesoCarbonIntensityClient = _guarded_neso_client
app = app_main.create_app(Settings(
    app_env="testing",
    debug=False,
    neso_api_base_url="https://api.carbonintensity.org.uk",
    neso_region_id=13,
    api_rate_limit_requests_per_minute=10_000,
    forecast_evaluation_rate_limit_requests_per_minute=60,
))
app.state.neso_carbon_intensity_reader = DeterministicNesoReader()
app.state.e2e_neso_network_guard = neso_network_guard


@app.get("/__e2e__/identity", include_in_schema=False)
async def e2e_fixture_identity() -> dict[str, int | str]:
    """Expose test-only identity without making an upstream request."""
    reader = app.state.neso_carbon_intensity_reader
    if not isinstance(reader, DeterministicNesoReader):
        raise HTTPException(status_code=503, detail="Deterministic E2E reader is not installed")
    return {
        "fixture_id": E2E_FIXTURE_ID,
        "reader": type(reader).__name__,
        "neso_network_policy": "blocked",
        "neso_transport_attempts": neso_network_guard.request_attempts,
    }
