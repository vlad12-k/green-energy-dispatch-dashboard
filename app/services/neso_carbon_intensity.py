"""Typed client and validation for the NESO Carbon Intensity API."""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Any, Callable, Literal
from urllib.parse import urljoin, urlsplit

import httpx

from app.services.intensity_units import LIVE_NESO_SOURCE_API as LIVE_SOURCE_API
from app.services.intensity_units import IntensityUnit, units_for_source_api

SOURCE_API: Literal["neso-carbon-intensity-api"] = LIVE_SOURCE_API
DEFAULT_BASE_URL = "https://api.carbonintensity.org.uk"
ALLOWED_NESO_API_HOST = "api.carbonintensity.org.uk"
NESO_CACHE_TTL_SECONDS = 30
MAX_NESO_TIMEOUT_SECONDS = 30.0
MAX_NESO_RESPONSE_BYTES = 64 * 1024


def is_allowed_neso_base_url(base_url: str) -> bool:
    """Accept only the official HTTPS origin, without credentials or URL paths."""
    try:
        parsed = urlsplit(base_url)
        return (
            parsed.scheme == "https"
            and parsed.hostname == ALLOWED_NESO_API_HOST
            and parsed.port in (None, 443)
            and parsed.username is None
            and parsed.password is None
            and parsed.path in ("", "/")
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        return False


class NesoAPIError(RuntimeError):
    """Raised when the NESO API is unavailable or returns unusable evidence."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        http_status: int | None = None,
        retry_after: str | None = None,
    ) -> None:
        self.retryable = retryable
        self.http_status = http_status
        self.retry_after = retry_after
        super().__init__(message)


@dataclass(frozen=True)
class CarbonIntensityReading:
    """A region-specific reading with time validity and source provenance."""

    intensity_gco2_per_kwh: float
    measurement_type: Literal["actual", "forecast"]
    region_id: int
    region_name: str
    intensity_index: str
    observed_at: datetime
    valid_until: datetime
    retrieved_at: datetime
    interval_status: Literal["upcoming", "in_progress", "completed"]
    interval_lag_seconds: float
    is_fresh: bool
    age_seconds: float
    source_api: str
    source_url: str

    @property
    def units(self) -> IntensityUnit:
        return units_for_source_api(self.source_api)


def freshness_deadline(
    reading: CarbonIntensityReading,
    *,
    max_age_seconds: int,
) -> datetime:
    """Return the reading's freshness deadline without changing its shared model."""
    remaining_age = max(0.0, max_age_seconds - reading.age_seconds)
    try:
        deadline = reading.retrieved_at + timedelta(seconds=remaining_age)
    except OverflowError:
        deadline = datetime.max.replace(tzinfo=UTC)
    if reading.measurement_type == "forecast":
        deadline = min(deadline, reading.valid_until)
    return deadline


def _parse_timestamp(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp")  # noqa: TRY004
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
        raise ValueError("NESO response must be a JSON object")  # noqa: TRY004
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
        raise ValueError("regional interval must be a JSON object")  # noqa: TRY004
    intensity = record.get("intensity")
    if not isinstance(intensity, Mapping):
        raise ValueError("regional intensity must be a JSON object")  # noqa: TRY004

    observed_at = _parse_timestamp(record.get("from"), "data[0].data[0].from")
    valid_until = _parse_timestamp(record.get("to"), "data[0].data[0].to")
    forecast = intensity.get("forecast")
    actual = intensity.get("actual")
    raw_value = actual if actual is not None else forecast
    if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
        raise ValueError("intensity must contain a numeric actual or forecast value")  # noqa: TRY004
    try:
        value = float(raw_value)
    except OverflowError as exc:
        raise ValueError("intensity must be finite and non-negative") from exc
    if not math.isfinite(value) or value < 0:
        raise ValueError("intensity must be finite and non-negative")
    index = intensity.get("index")
    if not isinstance(index, str) or not index:
        raise ValueError("intensity index must be a non-empty string")
    measurement_type: Literal["actual", "forecast"] = (
        "actual" if actual is not None else "forecast"
    )
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
        interval_status: Literal["upcoming", "in_progress", "completed"] = "upcoming"
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
        if not is_allowed_neso_base_url(base_url):
            raise ValueError(
                "NESO API base URL must use the allowlisted HTTPS host "
                f"{ALLOWED_NESO_API_HOST} without credentials or a path"
            )
        if isinstance(region_id, bool) or region_id <= 0:
            raise ValueError("region_id must be a positive integer")
        if (
            max_age_seconds <= 0
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= MAX_NESO_TIMEOUT_SECONDS
        ):
            raise ValueError("freshness limit must be positive and timeout must be bounded")
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
            async with asyncio.timeout(self.timeout_seconds):
                async with httpx.AsyncClient(
                    timeout=self.timeout_seconds,
                    transport=self.transport,
                    follow_redirects=False,
                ) as client:
                    async with client.stream(
                        "GET",
                        self.endpoint_url,
                        headers={
                            "Accept": "application/json",
                            "Accept-Encoding": "identity",
                        },
                    ) as response:
                        response.raise_for_status()
                        content_encoding = response.headers.get(
                            "Content-Encoding", "identity"
                        ).strip().lower()
                        if content_encoding not in {"", "identity"}:
                            raise NesoAPIError(
                                "NESO Carbon Intensity API returned unsupported "
                                "content encoding"
                            )
                        content_length = response.headers.get("Content-Length")
                        if content_length is not None:
                            try:
                                if int(content_length) > MAX_NESO_RESPONSE_BYTES:
                                    raise NesoAPIError(
                                        "NESO Carbon Intensity API response exceeds "
                                        "the 64 KiB limit"
                                    )
                            except ValueError:
                                pass
                        body = bytearray()
                        if response.is_stream_consumed:
                            raw_content = response.content
                            if len(raw_content) > MAX_NESO_RESPONSE_BYTES:
                                raise NesoAPIError(
                                    "NESO Carbon Intensity API response exceeds "
                                    "the 64 KiB limit"
                                )
                            body.extend(raw_content)
                        else:
                            async for chunk in response.aiter_raw():
                                if len(chunk) > MAX_NESO_RESPONSE_BYTES - len(body):
                                    raise NesoAPIError(
                                        "NESO Carbon Intensity API response exceeds "
                                        "the 64 KiB limit"
                                    )
                                body.extend(chunk)
                        payload = json.loads(body)
                        retrieved_at = datetime.now(UTC)
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise NesoAPIError(
                "NESO Carbon Intensity API request timed out", retryable=True
            ) from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            raise NesoAPIError(
                f"NESO Carbon Intensity API returned HTTP {status}",
                retryable=status == 429 or 500 <= status <= 599,
                http_status=status,
                retry_after=exc.response.headers.get("Retry-After"),
            ) from exc
        except httpx.RequestError as exc:
            raise NesoAPIError(
                "NESO Carbon Intensity API request failed", retryable=True
            ) from exc
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


class CachedNesoCarbonIntensityReader:
    """Bounded, process-local cache for the public read-only dashboard feed."""

    def __init__(
        self,
        client: NesoCarbonIntensityClient,
        *,
        cache_ttl_seconds: float = NESO_CACHE_TTL_SECONDS,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if (
            not math.isfinite(cache_ttl_seconds)
            or not 0 < cache_ttl_seconds <= 300
        ):
            raise ValueError("NESO cache TTL must be between 0 and 300 seconds")
        self._client = client
        self._cache_ttl_seconds = cache_ttl_seconds
        self._clock = clock
        self._cached_reading: CarbonIntensityReading | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    @property
    def max_age_seconds(self) -> int:
        """Expose the client freshness policy for API response metadata."""
        return self._client.max_age_seconds

    async def fetch_latest(self) -> CarbonIntensityReading:
        """Return a fresh cache entry or fetch and cache validated NESO evidence."""
        if self._cached_reading is not None and self._clock() < self._expires_at:
            return self._cached_reading

        async with self._lock:
            if self._cached_reading is not None and self._clock() < self._expires_at:
                return self._cached_reading
            reading = await self._client.fetch_latest()
            self._cached_reading = reading
            fresh_until = freshness_deadline(
                reading,
                max_age_seconds=self._client.max_age_seconds,
            )
            remaining_freshness = max(
                0.0,
                (fresh_until - datetime.now(UTC)).total_seconds(),
            )
            cache_lifetime = (
                min(self._cache_ttl_seconds, remaining_freshness)
                if reading.is_fresh
                else 0.0
            )
            self._expires_at = self._clock() + cache_lifetime
            return reading
