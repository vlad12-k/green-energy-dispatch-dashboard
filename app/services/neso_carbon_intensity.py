"""Typed client and validation for the NESO Carbon Intensity API."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import math
from typing import Any
from urllib.parse import urljoin

import httpx

SOURCE_API = "neso-carbon-intensity-api"
DEFAULT_BASE_URL = "https://api.carbonintensity.org.uk"


class NesoAPIError(RuntimeError):
    """Raised when the NESO API is unavailable or returns unusable evidence."""


@dataclass(frozen=True)
class CarbonIntensityReading:
    """A region-specific reading with time validity and source provenance."""

    intensity_gco2_per_kwh: float
    measurement_type: str
    region_id: int
    region_name: str
    intensity_index: str
    observed_at: datetime
    valid_until: datetime
    retrieved_at: datetime
    interval_status: str
    interval_lag_seconds: float
    is_fresh: bool
    age_seconds: float
    source_api: str
    source_url: str


def _parse_timestamp(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return parsed.astimezone(UTC)


def parse_regional_response(
    payload: Any,
    *,
    region_id: int,
    now: datetime | None = None,
    max_age_seconds: int = 3600,
    source_url: str | None = None,
) -> CarbonIntensityReading:
    """Validate the NESO region endpoint payload and enforce freshness."""
    if isinstance(region_id, bool) or region_id <= 0:
        raise ValueError("region_id must be a positive integer")
    if max_age_seconds <= 0:
        raise ValueError("max_age_seconds must be positive")
    retrieved_at = now or datetime.now(UTC)
    if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
        raise ValueError("now must include a timezone")
    retrieved_at = retrieved_at.astimezone(UTC)

    if not isinstance(payload, Mapping):
        raise ValueError("NESO response must be a JSON object")
    regions = payload.get("data")
    if not isinstance(regions, list) or not regions:
        raise ValueError("response data must be a non-empty list")
    if any(not isinstance(item, Mapping) for item in regions):
        raise ValueError("regional entries must be JSON objects")
    region = next(
        (item for item in regions if item.get("regionid") == region_id), None
    )
    if region is None:
        raise ValueError("NESO response does not include the requested region")

    intervals = region.get("data")
    if not isinstance(intervals, list) or not intervals:
        raise ValueError("regional intervals must be a non-empty list")
    record = intervals[0]
    if not isinstance(record, Mapping):
        raise ValueError("regional interval must be a JSON object")
    intensity = record.get("intensity")
    if not isinstance(intensity, Mapping):
        raise ValueError("regional intensity must be a JSON object")

    observed_at = _parse_timestamp(record.get("from"), "data[0].data[0].from")
    valid_until = _parse_timestamp(record.get("to"), "data[0].data[0].to")
    forecast = intensity.get("forecast")
    actual = intensity.get("actual")
    raw_value = actual if actual is not None else forecast
    if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
        raise ValueError("intensity must contain a numeric actual or forecast value")
    try:
        value = float(raw_value)
    except OverflowError as exc:
        raise ValueError("intensity must be finite and non-negative") from exc
    if not math.isfinite(value) or value < 0:
        raise ValueError("intensity must be finite and non-negative")
    index = intensity.get("index")
    if not isinstance(index, str) or not index:
        raise ValueError("intensity index must be a non-empty string")
    measurement_type = "actual" if actual is not None else "forecast"
    region_name = region.get("shortname")
    if not isinstance(region_name, str) or not region_name:
        raise ValueError("region shortname must be a non-empty string")

    if valid_until <= observed_at:
        raise ValueError("measurement validity window is invalid")

    if observed_at > retrieved_at and actual is not None:
        raise ValueError("actual measurement starts in the future")
    if observed_at > retrieved_at and observed_at - retrieved_at > timedelta(hours=48):
        raise ValueError("forecast exceeds the supported 48-hour horizon")

    age_seconds = max(0.0, (retrieved_at - observed_at).total_seconds())
    if age_seconds > max_age_seconds:
        raise ValueError(
            f"NESO measurement is stale (age {age_seconds:.0f}s, limit {max_age_seconds}s)"
        )

    if retrieved_at < observed_at:
        interval_status = "upcoming"
    elif retrieved_at >= valid_until:
        interval_status = "completed"
    else:
        interval_status = "in_progress"
    interval_lag_seconds = max(0.0, (retrieved_at - valid_until).total_seconds())
    is_fresh = not (measurement_type == "forecast" and interval_status == "completed")

    return CarbonIntensityReading(
        intensity_gco2_per_kwh=value,
        measurement_type=measurement_type,
        region_id=region_id,
        region_name=region_name,
        intensity_index=index,
        observed_at=observed_at,
        valid_until=valid_until,
        retrieved_at=retrieved_at,
        interval_status=interval_status,
        interval_lag_seconds=interval_lag_seconds,
        is_fresh=is_fresh,
        age_seconds=age_seconds,
        source_api=SOURCE_API,
        source_url=source_url or DEFAULT_BASE_URL,
    )


class NesoCarbonIntensityClient:
    """Async read-only client for the NESO regional intensity endpoint."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        region_id: int = 13,
        max_age_seconds: int = 3600,
        timeout_seconds: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not base_url.startswith("https://"):
            raise ValueError("NESO API base URL must use HTTPS")
        if isinstance(region_id, bool) or region_id <= 0:
            raise ValueError("region_id must be a positive integer")
        if max_age_seconds <= 0 or timeout_seconds <= 0:
            raise ValueError("freshness limit and timeout must be positive")
        self.base_url = base_url.rstrip("/")
        self.region_id = region_id
        self.max_age_seconds = max_age_seconds
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    @property
    def endpoint_url(self) -> str:
        return urljoin(
            f"{self.base_url}/",
            f"regional/regionid/{self.region_id}",
        )

    async def fetch_raw(self) -> tuple[Any, datetime, str]:
        """Fetch one raw regional JSON response with retrieval-time provenance."""
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                transport=self.transport,
                follow_redirects=False,
            ) as client:
                response = await client.get(
                    self.endpoint_url,
                    headers={"Accept": "application/json"},
                )
                response.raise_for_status()
                payload = response.json()
                retrieved_at = datetime.now(UTC)
        except httpx.TimeoutException as exc:
            raise NesoAPIError("NESO Carbon Intensity API request timed out") from exc
        except httpx.HTTPStatusError as exc:
            raise NesoAPIError(
                f"NESO Carbon Intensity API returned HTTP {exc.response.status_code}"
            ) from exc
        except httpx.RequestError as exc:
            raise NesoAPIError("NESO Carbon Intensity API request failed") from exc
        except ValueError as exc:
            raise NesoAPIError("NESO Carbon Intensity API returned invalid JSON") from exc

        return payload, retrieved_at, str(response.url)

    async def fetch_latest(self) -> CarbonIntensityReading:
        """Fetch the latest regional interval; fail closed on invalid/stale data."""
        payload, retrieved_at, source_url = await self.fetch_raw()
        try:
            return parse_regional_response(
                payload,
                region_id=self.region_id,
                now=retrieved_at,
                max_age_seconds=self.max_age_seconds,
                source_url=source_url,
            )
        except ValueError as exc:
            raise NesoAPIError(f"NESO Carbon Intensity API data rejected: {exc}") from exc
