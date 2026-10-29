from datetime import UTC, datetime
import math
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, field_validator

from app.services.carbon_gate import CarbonDecision, evaluate_carbon_gate
from app.services.neso_carbon_intensity import (
    CarbonIntensityReading,
    NESO_CACHE_TTL_SECONDS,
    NesoAPIError,
    freshness_deadline,
)
from app.services.intensity_units import LIVE_NESO_API_REPORTED_UNIT

router = APIRouter(prefix="/energy", tags=["energy"])


class CarbonGateRequest(BaseModel):
    """Observed UK grid carbon intensity for a simulated dispatch decision."""

    model_config = ConfigDict(extra="forbid", strict=True)

    carbon_intensity_gco2_per_kwh: FiniteFloat = Field(ge=0)


class CarbonGateResponse(BaseModel):
    """Transparent recommendation from the carbon-aware dispatch policy."""

    model_config = ConfigDict(extra="forbid")

    decision: CarbonDecision
    reason: str
    carbon_intensity_gco2_per_kwh: FiniteFloat = Field(ge=0)


class CarbonIntensityResponse(BaseModel):
    """NESO intensity observation with value, time validity, and provenance."""

    model_config = ConfigDict(extra="forbid")

    intensity_gco2_per_kwh: FiniteFloat = Field(ge=0)
    measurement_type: Literal["actual", "forecast"]
    units: Literal["gCO2/kWh"]
    region_id: int = Field(gt=0)
    region_name: str = Field(min_length=1)
    intensity_index: str = Field(min_length=1)
    observed_at: datetime
    valid_until: datetime
    retrieved_at: datetime
    fresh_until: datetime
    interval_status: Literal["upcoming", "in_progress", "completed"]
    interval_lag_seconds: FiniteFloat = Field(ge=0)
    is_fresh: bool
    age_seconds: FiniteFloat = Field(ge=0)
    source_api: Literal["neso-carbon-intensity-api"]
    source_url: str

    @classmethod
    def from_reading(
        cls,
        reading: CarbonIntensityReading,
        *,
        fresh_until: datetime,
        now: datetime,
    ) -> "CarbonIntensityResponse":
        if now < reading.observed_at:
            interval_status: Literal["upcoming", "in_progress", "completed"] = "upcoming"
        elif now >= reading.valid_until:
            interval_status = "completed"
        else:
            interval_status = "in_progress"
        return cls(
            intensity_gco2_per_kwh=reading.intensity_gco2_per_kwh,
            measurement_type=reading.measurement_type,
            units=LIVE_NESO_API_REPORTED_UNIT,
            region_id=reading.region_id,
            region_name=reading.region_name,
            intensity_index=reading.intensity_index,
            observed_at=reading.observed_at,
            valid_until=reading.valid_until,
            retrieved_at=reading.retrieved_at,
            fresh_until=fresh_until,
            interval_status=interval_status,
            interval_lag_seconds=max(0.0, (now - reading.valid_until).total_seconds()),
            is_fresh=reading.is_fresh and now < fresh_until,
            age_seconds=reading.age_seconds,
            source_api=reading.source_api,
            source_url=reading.source_url,
        )

    @field_validator("observed_at", "valid_until", "retrieved_at", "fresh_until")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value


@router.get("/carbon-intensity", response_model=CarbonIntensityResponse)
async def get_carbon_intensity(
    request: Request,
    response: Response,
) -> CarbonIntensityResponse:
    """Fetch and validate the configured NESO regional carbon-intensity feed."""
    response.headers["Cache-Control"] = "no-store"
    reader = request.app.state.neso_carbon_intensity_reader
    try:
        reading = await reader.fetch_latest()
    except NesoAPIError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
            headers={"Cache-Control": "no-store"},
        ) from exc
    now = datetime.now(UTC)
    fresh_until = freshness_deadline(
        reading,
        max_age_seconds=reader.max_age_seconds,
    )
    remaining_freshness = max(0.0, (fresh_until - now).total_seconds())
    cache_max_age = min(NESO_CACHE_TTL_SECONDS, math.floor(remaining_freshness))
    response.headers["Cache-Control"] = (
        f"public, max-age={cache_max_age}"
        if reading.is_fresh and cache_max_age > 0
        else "no-store"
    )
    return CarbonIntensityResponse.from_reading(
        reading,
        fresh_until=fresh_until,
        now=now,
    )


@router.post("/carbon-gate", response_model=CarbonGateResponse)
async def evaluate_simulated_dispatch(request: CarbonGateRequest) -> CarbonGateResponse:
    """Evaluate a carbon policy; this endpoint never performs a market trade."""
    result = evaluate_carbon_gate(request.carbon_intensity_gco2_per_kwh)
    return CarbonGateResponse(
        decision=result.decision,
        reason=result.reason,
        carbon_intensity_gco2_per_kwh=result.carbon_intensity_gco2_per_kwh,
    )
