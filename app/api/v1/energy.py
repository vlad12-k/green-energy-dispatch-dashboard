from datetime import datetime, timezone

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.carbon_gate import evaluate_carbon_gate

router = APIRouter(prefix="/energy", tags=["energy"])


class CarbonGateRequest(BaseModel):
    """Observed UK grid carbon intensity for a simulated dispatch decision."""

    carbon_intensity_gco2_per_kwh: float = Field(ge=0)


class CarbonGateResponse(BaseModel):
    """Transparent recommendation from the carbon-aware dispatch policy."""

    decision: str
    reason: str
    carbon_intensity_gco2_per_kwh: float


@router.get("/grid-status")
async def get_grid_status() -> dict[str, bool | str]:
    """Return the current placeholder grid-connection status."""
    return {
        "grid_connected": True,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/carbon-intensity")
async def get_carbon_intensity() -> dict[str, float | str]:
    """Return a documented placeholder until live-source ingestion is introduced."""
    return {
        "co2g_per_kwh": 230.5,
        "region": "UK",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/carbon-gate", response_model=CarbonGateResponse)
async def evaluate_simulated_dispatch(request: CarbonGateRequest) -> CarbonGateResponse:
    """Evaluate a carbon policy; this endpoint never performs a market trade."""
    result = evaluate_carbon_gate(request.carbon_intensity_gco2_per_kwh)
    return CarbonGateResponse(
        decision=result.decision,
        reason=result.reason,
        carbon_intensity_gco2_per_kwh=result.carbon_intensity_gco2_per_kwh,
    )
