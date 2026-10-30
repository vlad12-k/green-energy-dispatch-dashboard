"""Stateless advisory simulation endpoints."""

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

from app.schemas.ev_depot import (
    CarbonAwareDepotScenario,
    DepotScenario,
    VehicleAvailability,
)
from app.schemas.forecast_backed_ev_depot import (
    ForecastBackedDepotScenario,
    ScenarioRegionMapping,
)
from app.services.ev_depot_evaluation import CarbonAwareSimulationResult
from app.services.carbon_aware_scheduler import schedule_carbon_aware
from app.services.forecast_backed_scheduler import (
    ForecastBackedSchedulingResult,
    schedule_forecast_backed,
)
from app.services.neso_carbon_intensity import (
    CarbonIntensityForecastTimeline,
    NesoAPIError,
    next_half_hour_start,
)

FORECAST_EVALUATION_PATH = "/api/v1/simulations/ev-depot/forecast-evaluate"
_MAX_FORECAST_AGE_SECONDS = 3600
_MAX_SCHEDULING_NUMERIC_VALUE = 1_000_000_000.0


class CarbonAwareDepotScenarioRequest(CarbonAwareDepotScenario):
    """HTTP JSON adapter for ISO-8601 timestamps under the strict domain schema."""

    @model_validator(mode="before")
    @classmethod
    def parse_timestamp_strings(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        parsed = dict(value)
        timestamp_fields = ("start_at",)
        for field in timestamp_fields:
            parsed[field] = _parse_iso_timestamp(parsed.get(field))
        for collection_name, fields in (
            ("vehicles", ("availability_start", "availability_end")),
            ("carbon_intensity_profile", ("interval_start", "interval_end")),
        ):
            rows = parsed.get(collection_name)
            if isinstance(rows, list):
                converted = []
                for row in rows:
                    if not isinstance(row, dict):
                        converted.append(row)
                        continue
                    converted_row = dict(row)
                    for field in fields:
                        converted_row[field] = _parse_iso_timestamp(converted_row.get(field))
                    converted.append(converted_row)
                parsed[collection_name] = converted
        return parsed


def _parse_iso_timestamp(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value


class ForecastBackedDepotScenarioRequest(DepotScenario):
    """Strict HTTP adapter for the bounded B2a scenario contract."""

    scenario_id: str = Field(min_length=1, max_length=120)
    interval_minutes: int = Field(ge=30, le=30)
    intervals: int = Field(gt=0, le=48)
    depot_power_kw: FiniteFloat = Field(
        gt=0,
        le=_MAX_SCHEDULING_NUMERIC_VALUE,
    )
    vehicles: list[VehicleAvailability] = Field(
        min_length=1,
        max_length=50,
    )

    @model_validator(mode="before")
    @classmethod
    def parse_timestamp_strings(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        parsed = dict(value)
        parsed["start_at"] = _parse_iso_timestamp(parsed.get("start_at"))
        rows = parsed.get("vehicles")
        if isinstance(rows, list):
            converted = []
            for row in rows:
                if not isinstance(row, dict):
                    converted.append(row)
                    continue
                converted_row = dict(row)
                for field in ("availability_start", "availability_end"):
                    converted_row[field] = _parse_iso_timestamp(converted_row.get(field))
                converted.append(converted_row)
            parsed["vehicles"] = converted
        return parsed

    @model_validator(mode="after")
    def require_half_hour_start(self) -> "ForecastBackedDepotScenarioRequest":
        if (
            self.start_at.minute not in (0, 30)
            or self.start_at.second != 0
            or self.start_at.microsecond != 0
        ):
            raise ValueError("start_at must align to a UTC half-hour boundary")
        if self.depot_power_kw > _MAX_SCHEDULING_NUMERIC_VALUE or any(
            len(vehicle.vehicle_id) > 120
            or vehicle.required_energy_kwh > _MAX_SCHEDULING_NUMERIC_VALUE
            or vehicle.charging_power_kw > _MAX_SCHEDULING_NUMERIC_VALUE
            for vehicle in self.vehicles
        ):
            raise ValueError("scenario identifiers and numeric values exceed API limits")
        return self


class ForecastBackedEvaluationRequest(BaseModel):
    """Client-supplied scenario only; forecast evidence remains server-owned."""

    model_config = ConfigDict(extra="forbid", strict=True)

    scenario: ForecastBackedDepotScenarioRequest
    neso_region_id: int | None = Field(default=None, ge=1, le=17)


def _utc_now() -> datetime:
    return datetime.now(UTC)


router = APIRouter(prefix="/simulations/ev-depot", tags=["simulations"])


@router.post("/evaluate", response_model=CarbonAwareSimulationResult)
def evaluate_ev_depot_scenario(
    scenario: CarbonAwareDepotScenarioRequest,
) -> CarbonAwareSimulationResult:
    """Run the existing bounded scheduler without persistence or upstream calls."""
    return schedule_carbon_aware(scenario)


@router.post(
    "/forecast-evaluate",
    response_model=ForecastBackedSchedulingResult,
    responses={
        503: {
            "model": ForecastBackedSchedulingResult,
            "description": "Forecast evidence is unavailable or invalid.",
        }
    },
)
async def evaluate_forecast_backed_ev_depot_scenario(
    body: ForecastBackedEvaluationRequest,
    request: Request,
    response: Response,
) -> ForecastBackedSchedulingResult:
    """Run the advisory scheduler against validated server-side NESO evidence."""
    response.headers["Cache-Control"] = "no-store"
    region_id = request.app.state.neso_region_id
    if body.neso_region_id is not None and body.neso_region_id != region_id:
        raise HTTPException(
            status_code=422,
            detail="neso_region_id must match the server-configured region",
            headers={"Cache-Control": "no-store"},
        )

    received_at = _utc_now()
    requested_from = next_half_hour_start(received_at)
    if requested_from <= received_at:
        requested_from += timedelta(minutes=30)
    if body.scenario.start_at < requested_from:
        raise HTTPException(
            status_code=422,
            detail="scenario.start_at must be at or after the next UTC half-hour boundary",
            headers={"Cache-Control": "no-store"},
        )

    reader = request.app.state.neso_carbon_intensity_reader
    forecast: CarbonIntensityForecastTimeline | None = None
    unavailable_detail = None
    try:
        forecast = await reader.fetch_forecast_timeline(
            region_id=region_id,
            requested_from=requested_from,
        )
    except NesoAPIError as exc:
        unavailable_detail = (
            "upstream_error"
            if exc.retryable or exc.http_status is not None
            else "upstream_validation_error"
        )

    decision_time = _utc_now()
    if body.scenario.start_at < decision_time:
        raise HTTPException(
            status_code=422,
            detail="scenario.start_at must not precede the server decision time",
            headers={"Cache-Control": "no-store"},
        )
    if forecast is None and unavailable_detail is None:
        unavailable_detail = "explicitly_unavailable"
    elif forecast is not None and not isinstance(forecast, CarbonIntensityForecastTimeline):
        forecast = None
        unavailable_detail = "upstream_validation_error"

    scheduling_request = ForecastBackedDepotScenario(
        scenario=body.scenario,
        region_mapping=ScenarioRegionMapping(
            scenario_id=body.scenario.scenario_id,
            neso_region_id=region_id,
        ),
        forecast=forecast,
        forecast_unavailable_detail=unavailable_detail,
        decision_time=decision_time,
        max_forecast_age_seconds=min(
            reader.max_age_seconds,
            _MAX_FORECAST_AGE_SECONDS,
        ),
        intensity_unit="gCO2/kWh",
    )
    result = schedule_forecast_backed(scheduling_request)
    if result.status == "unavailable":
        response.status_code = 503
    return result
