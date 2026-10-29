from __future__ import annotations

from typing import Literal

LIVE_NESO_SOURCE_API = "neso-carbon-intensity-api"
HISTORICAL_NESO_SOURCE_API = "neso-regional-carbon-intensity-forecast-datastore"

IntensityUnit = Literal["gCO2/kWh", "gCO2e/kWh"]
# The public Carbon Intensity API reports these raw values as gCO2/kWh. Keep
# its HTTP contract separate from the existing persisted-observation mapping.
LIVE_NESO_API_REPORTED_UNIT: Literal["gCO2/kWh"] = "gCO2/kWh"

SOURCE_INTENSITY_UNITS: dict[str, IntensityUnit] = {
    LIVE_NESO_SOURCE_API: "gCO2e/kWh",
    HISTORICAL_NESO_SOURCE_API: "gCO2/kWh",
}


def units_for_source_api(source_api: str) -> IntensityUnit:
    """Return the explicit unit contract for a recognised source API."""
    try:
        return SOURCE_INTENSITY_UNITS[source_api]
    except KeyError as exc:
        raise ValueError(
            f"no intensity unit contract is defined for source API {source_api!r}"
        ) from exc
