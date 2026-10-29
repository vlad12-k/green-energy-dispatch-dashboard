from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.ev_depot import DepotScenario
from app.services.neso_carbon_intensity import CarbonIntensityForecastTimeline


class ScenarioRegionMapping(BaseModel):
    """Explicit mapping from one depot scenario to a NESO regional identity."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    scenario_id: str = Field(min_length=1)
    neso_region_id: int = Field(ge=1, le=17)


ForecastUnavailableDetail = Literal[
    "upstream_error",
    "upstream_validation_error",
    "explicitly_unavailable",
]


class ForecastBackedDepotScenario(BaseModel):
    """A depot scheduling request with separately sourced regional forecast data."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    scenario: DepotScenario
    region_mapping: ScenarioRegionMapping
    forecast: CarbonIntensityForecastTimeline | None
    forecast_unavailable_detail: ForecastUnavailableDetail | None = None
    decision_time: datetime
    max_forecast_age_seconds: int = Field(default=3600, ge=1, le=3600)
    intensity_unit: str = Field(min_length=1, max_length=32)


ForecastInputUnavailableReason = Literal[
    "missing_forecast",
    "scenario_region_mapping_mismatch",
    "forecast_region_mismatch",
    "forecast_unavailable",
    "invalid_decision_time",
    "decision_after_scenario_start",
    "forecast_retrieved_after_decision_time",
    "stale_forecast",
    "invalid_depot_scenario",
    "invalid_forecast_source",
    "intensity_unit_mismatch",
    "invalid_forecast_timeline",
    "misaligned_forecast_intervals",
    "overlapping_forecast_intervals",
    "missing_interval_coverage",
    "scenario_interval_misaligned",
]
