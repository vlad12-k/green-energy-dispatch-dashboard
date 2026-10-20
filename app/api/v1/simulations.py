"""Stateless advisory simulation endpoints."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter
from pydantic import model_validator

from app.schemas.ev_depot import CarbonAwareDepotScenario
from app.services.ev_depot_evaluation import CarbonAwareSimulationResult
from app.services.carbon_aware_scheduler import schedule_carbon_aware


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


router = APIRouter(prefix="/simulations/ev-depot", tags=["simulations"])


@router.post("/evaluate", response_model=CarbonAwareSimulationResult)
def evaluate_ev_depot_scenario(
    scenario: CarbonAwareDepotScenarioRequest,
) -> CarbonAwareSimulationResult:
    """Run the existing bounded scheduler without persistence or upstream calls."""
    return schedule_carbon_aware(scenario)
