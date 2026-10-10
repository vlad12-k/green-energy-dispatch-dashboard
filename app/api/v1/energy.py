from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, field_validator

from app.core.config import settings
from app.services.carbon_gate import CarbonDecision, evaluate_carbon_gate
from app.services.neso_carbon_intensity import (
    CarbonIntensityReading,
    NesoAPIError,
    NesoCarbonIntensityClient,
)

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
    measurement_type: str
    region_id: int = Field(gt=0)
    region_name: str = Field(min_length=1)
    intensity_index: str = Field(min_length=1)
    observed_at: datetime
    valid_until: datetime
    retrieved_at: datetime
    is_fresh: bool
    age_seconds: FiniteFloat = Field(ge=0)
    source_api: str
    source_url: str

    @classmethod
    def from_reading(cls, reading: CarbonIntensityReading) -> "CarbonIntensityResponse":
        return cls(
            intensity_gco2_per_kwh=reading.intensity_gco2_per_kwh,
            measurement_type=reading.measurement_type,
            region_id=reading.region_id,
            region_name=reading.region_name,
            intensity_index=reading.intensity_index,
            observed_at=reading.observed_at,
            valid_until=reading.valid_until,
            retrieved_at=reading.retrieved_at,
            is_fresh=reading.is_fresh,
            age_seconds=reading.age_seconds,
            source_api=reading.source_api,
            source_url=reading.source_url,
        )

    @field_validator("observed_at", "valid_until", "retrieved_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value


@router.get("/carbon-intensity", response_model=CarbonIntensityResponse)
async def get_carbon_intensity() -> CarbonIntensityResponse:
    """Fetch and validate the configured NESO regional carbon-intensity feed."""
    client = NesoCarbonIntensityClient(
        base_url=settings.neso_api_base_url,
        region_id=settings.neso_region_id,
        max_age_seconds=settings.neso_max_age_seconds,
        timeout_seconds=settings.neso_timeout_seconds,
    )
    try:
        reading = await client.fetch_latest()
    except NesoAPIError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return CarbonIntensityResponse.from_reading(reading)


@router.post("/carbon-gate", response_model=CarbonGateResponse)
async def evaluate_simulated_dispatch(request: CarbonGateRequest) -> CarbonGateResponse:
    """Evaluate a carbon policy; this endpoint never performs a market trade."""
    result = evaluate_carbon_gate(request.carbon_intensity_gco2_per_kwh)
    return CarbonGateResponse(
        decision=result.decision,
        reason=result.reason,
        carbon_intensity_gco2_per_kwh=result.carbon_intensity_gco2_per_kwh,
    )
