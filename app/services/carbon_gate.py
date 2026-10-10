"""Carbon-aware simulated dispatch policy.

This module only evaluates a dispatch recommendation. It does not place trades,
interact with market participants, or control physical energy assets.
"""

from dataclasses import dataclass
from enum import StrEnum
import math


class CarbonDecision(StrEnum):
    """Possible decisions from the carbon-aware dispatch policy."""

    ALLOW = "allow"
    DEFER = "defer"
    BLOCK = "block"


@dataclass(frozen=True)
class CarbonGateResult:
    """A transparent result returned by the carbon-aware dispatch policy."""

    decision: CarbonDecision
    reason: str
    carbon_intensity_gco2_per_kwh: float


LOW_CARBON_THRESHOLD_GCO2_PER_KWH = 150.0
HIGH_CARBON_THRESHOLD_GCO2_PER_KWH = 300.0


def evaluate_carbon_gate(carbon_intensity_gco2_per_kwh: float) -> CarbonGateResult:
    """Return a simulated dispatch decision for an observed carbon intensity."""
    if not math.isfinite(carbon_intensity_gco2_per_kwh) or carbon_intensity_gco2_per_kwh < 0:
        raise ValueError("carbon intensity must be finite and non-negative")

    if carbon_intensity_gco2_per_kwh <= LOW_CARBON_THRESHOLD_GCO2_PER_KWH:
        return CarbonGateResult(
            decision=CarbonDecision.ALLOW,
            reason="Carbon intensity is within the illustrative low-carbon threshold.",
            carbon_intensity_gco2_per_kwh=carbon_intensity_gco2_per_kwh,
        )

    if carbon_intensity_gco2_per_kwh <= HIGH_CARBON_THRESHOLD_GCO2_PER_KWH:
        return CarbonGateResult(
            decision=CarbonDecision.DEFER,
            reason="Carbon intensity exceeds the illustrative low-carbon threshold; defer simulated dispatch.",
            carbon_intensity_gco2_per_kwh=carbon_intensity_gco2_per_kwh,
        )

    return CarbonGateResult(
        decision=CarbonDecision.BLOCK,
        reason="Carbon intensity exceeds the illustrative high-carbon threshold; block simulated dispatch.",
        carbon_intensity_gco2_per_kwh=carbon_intensity_gco2_per_kwh,
    )
