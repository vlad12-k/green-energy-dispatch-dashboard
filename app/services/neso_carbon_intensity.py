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
from app.services.intensity_units import (
    LIVE_NESO_API_REPORTED_UNIT,
    IntensityUnit,
    units_for_source_api,
)

SOURCE_API: Literal["neso-carbon-intensity-api"] = LIVE_SOURCE_API
DEFAULT_BASE_URL = "https://api.carbonintensity.org.uk"
ALLOWED_NESO_API_HOST = "api.carbonintensity.org.uk"
NESO_CACHE_TTL_SECONDS = 30
MAX_NESO_TIMEOUT_SECONDS = 30.0
MAX_NESO_RESPONSE_BYTES = 64 * 1024
NESO_INTENSITY_INDEXES = frozenset(
    {"very low", "low", "moderate", "high", "very high"}
)


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


def _regional_forecast_url(
    *,
    base_url: str,
    region_id: int,
    requested_from: datetime,
) -> str:
    timestamp = requested_from.astimezone(UTC).strftime("%Y-%m-%dT%H:%MZ")
    return urljoin(
        f"{base_url.rstrip('/')}/",
        f"regional/intensity/{timestamp}/fw24h/regionid/{region_id}",
    )


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


@dataclass(frozen=True)
class CarbonIntensityForecastInterval:
    """One explicitly forecast-only regional half-hour interval."""

    observed_at: datetime
    valid_until: datetime
    intensity_gco2_per_kwh: float
    intensity_index: str
    measurement_type: Literal["forecast"] = "forecast"


@dataclass(frozen=True)
class CarbonIntensityForecastTimeline:
    """A validated 24-hour regional forecast and its retrieval provenance."""

    region_id: int
    region_name: str
    requested_from: datetime
    valid_until: datetime
    retrieved_at: datetime
    intervals: tuple[CarbonIntensityForecastInterval, ...]
    source_api: Literal["neso-carbon-intensity-api"]
    source_url: str

    @property
    def units(self) -> Literal["gCO2/kWh"]:
        return LIVE_NESO_API_REPORTED_UNIT


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
    try:
        return parsed.astimezone(UTC)
    except OverflowError as exc:
        raise ValueError(
            f"{field_name} is outside the supported UTC datetime range"
        ) from exc


def _validate_region_shortname(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("region shortname must be a non-empty string")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("region shortname contains invalid Unicode") from exc
    return value


def next_half_hour_start(now: datetime) -> datetime:
    """Return the current exact UTC boundary or the next future boundary."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must include a timezone")
    now_utc = now.astimezone(UTC)
    boundary = now_utc.replace(minute=(now_utc.minute // 30) * 30, second=0, microsecond=0)
    return boundary if boundary == now_utc else boundary + timedelta(minutes=30)


def parse_regional_forecast_response(
    payload: Any,
    *,
    region_id: int,
    requested_from: datetime,
    retrieved_at: datetime,
    source_url: str | None = None,
) -> CarbonIntensityForecastTimeline:
    """Validate and normalise a regional 24-hour forecast response."""
    if (
        isinstance(region_id, bool)
        or not isinstance(region_id, int)
        or not 1 <= region_id <= 17
    ):
        raise ValueError("region_id must be between 1 and 17")
    if requested_from.tzinfo is None or requested_from.utcoffset() is None:
        raise ValueError("requested_from must include a timezone")
    if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
        raise ValueError("retrieved_at must include a timezone")
    try:
        requested_from = requested_from.astimezone(UTC)
        retrieved_at = retrieved_at.astimezone(UTC)
    except OverflowError as exc:
        raise ValueError(
            "forecast timestamps are outside the supported UTC datetime range"
        ) from exc
    if (
        requested_from.minute not in (0, 30)
        or requested_from.second != 0
        or requested_from.microsecond != 0
    ):
        raise ValueError("requested_from must be half-hour aligned in UTC")

    if not isinstance(payload, Mapping):
        raise ValueError("NESO response must be a JSON object")
    regions = payload.get("data")
    if isinstance(regions, Mapping):
        regions = [regions]
    if not isinstance(regions, list) or not regions:
        raise ValueError("response data must be a non-empty list")
    if any(not isinstance(item, Mapping) for item in regions):
        raise ValueError("regional entries must be JSON objects")
    matching_regions = [item for item in regions if item.get("regionid") == region_id]
    if not matching_regions:
        raise ValueError("NESO response does not include the requested region")
    if len(matching_regions) > 1:
        raise ValueError("NESO response contains a duplicate region")
    region = matching_regions[0]
    if type(region.get("regionid")) is not int or len(regions) != 1:
        raise ValueError("NESO response contains inconsistent region metadata")
    region_name = _validate_region_shortname(region.get("shortname"))
    raw_intervals = region.get("data")
    if not isinstance(raw_intervals, list) or not raw_intervals:
        raise ValueError("regional forecast intervals must be a non-empty list")
    if len(raw_intervals) == 49 and isinstance(raw_intervals[0], Mapping):
        leading_interval = raw_intervals[0]
        leading_start = _parse_timestamp(
            leading_interval.get("from"), "data[0].data[0].from"
        )
        leading_valid_until = _parse_timestamp(
            leading_interval.get("to"), "data[0].data[0].to"
        )
        if (
            leading_valid_until == requested_from
            and leading_valid_until - leading_start == timedelta(minutes=30)
        ):
            raw_intervals = raw_intervals[1:]
    if len(raw_intervals) < 48:
        raise ValueError("forecast is missing forecast intervals")
    if len(raw_intervals) > 48:
        raise ValueError("forecast contains more than 48 half-hour intervals")

    intervals: list[CarbonIntensityForecastInterval] = []
    seen_starts: set[datetime] = set()
    for index, raw_interval in enumerate(raw_intervals):
        if not isinstance(raw_interval, Mapping):
            raise ValueError("regional forecast interval must be a JSON object")
        intensity = raw_interval.get("intensity")
        if not isinstance(intensity, Mapping):
            raise ValueError("regional forecast intensity must be a JSON object")
        if intensity.get("actual") is not None:
            raise ValueError("actual values are not accepted in a forecast response")
        raw_value = intensity.get("forecast")
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            raise ValueError("forecast intensity must be numeric")
        try:
            value = float(raw_value)
        except OverflowError as exc:
            raise ValueError("forecast intensity must be finite and non-negative") from exc
        if not math.isfinite(value) or value < 0:
            raise ValueError("forecast intensity must be finite and non-negative")
        intensity_index = intensity.get("index")
        if not isinstance(intensity_index, str) or not intensity_index.strip():
            raise ValueError("forecast intensity index must be a non-empty string")
        if intensity_index not in NESO_INTENSITY_INDEXES:
            raise ValueError("forecast contains an unsupported intensity index")
        observed_at = _parse_timestamp(
            raw_interval.get("from"), f"data[0].data[{index}].from"
        )
        valid_until = _parse_timestamp(
            raw_interval.get("to"), f"data[0].data[{index}].to"
        )
        try:
            expected_valid_until = observed_at + timedelta(minutes=30)
        except OverflowError as exc:
            raise ValueError(
                "forecast interval exceeds the supported datetime range"
            ) from exc
        if valid_until != expected_valid_until:
            raise ValueError("each forecast interval must have a 30-minute duration")
        if valid_until <= retrieved_at:
            raise ValueError("forecast contains an outdated completed interval")
        if observed_at < requested_from:
            raise ValueError("forecast contains an outdated interval before the requested start")
        if observed_at in seen_starts:
            raise ValueError("forecast contains a duplicate forecast interval")
        try:
            expected_start = requested_from + timedelta(minutes=30 * index)
        except OverflowError as exc:
            raise ValueError(
                "forecast horizon exceeds the supported datetime range"
            ) from exc
        if observed_at > expected_start:
            raise ValueError("forecast is missing forecast intervals")
        if observed_at < expected_start:
            raise ValueError("forecast intervals are not in chronological order")
        seen_starts.add(observed_at)
        intervals.append(
            CarbonIntensityForecastInterval(
                observed_at=observed_at,
                valid_until=valid_until,
                intensity_gco2_per_kwh=value,
                intensity_index=intensity_index,
            )
        )

    return CarbonIntensityForecastTimeline(
        region_id=region_id,
        region_name=region_name.strip(),
        requested_from=requested_from,
        valid_until=intervals[-1].valid_until,
        retrieved_at=retrieved_at,
        intervals=tuple(intervals),
        source_api=SOURCE_API,
        source_url=source_url
        or _regional_forecast_url(
            base_url=DEFAULT_BASE_URL,
            region_id=region_id,
            requested_from=requested_from,
        ),
    )


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
    region_name = _validate_region_shortname(region.get("shortname"))

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
        return await self._fetch_json(self.endpoint_url)

    async def _fetch_json(self, endpoint_url: str) -> tuple[Any, datetime, str]:
        """Fetch bounded JSON from the allowlisted NESO origin without redirects."""
        try:
            async with asyncio.timeout(self.timeout_seconds):
                async with httpx.AsyncClient(
                    timeout=self.timeout_seconds,
                    transport=self.transport,
                    follow_redirects=False,
                ) as client:
                    async with client.stream(
                        "GET",
                        endpoint_url,
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
                        try:
                            payload = json.loads(body)
                        except RecursionError as exc:
                            raise NesoAPIError(
                                "NESO Carbon Intensity API returned invalid JSON"
                            ) from exc
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

    async def fetch_forecast_timeline(
        self,
        *,
        requested_from: datetime,
        region_id: int | None = None,
    ) -> CarbonIntensityForecastTimeline:
        """Fetch and validate one official regional 24-hour forecast timeline."""
        selected_region = self.region_id if region_id is None else region_id
        if (
            isinstance(selected_region, bool)
            or not isinstance(selected_region, int)
            or not 1 <= selected_region <= 17
        ):
            raise ValueError("region_id must be between 1 and 17")
        if requested_from.tzinfo is None or requested_from.utcoffset() is None:
            raise ValueError("requested_from must include a timezone")
        requested_from = requested_from.astimezone(UTC)
        if (
            requested_from.minute not in (0, 30)
            or requested_from.second != 0
            or requested_from.microsecond != 0
        ):
            raise ValueError("requested_from must be half-hour aligned in UTC")

        endpoint_url = _regional_forecast_url(
            base_url=self.base_url,
            region_id=selected_region,
            requested_from=requested_from,
        )
        payload, retrieved_at, source_url = await self._fetch_json(endpoint_url)
        try:
            return parse_regional_forecast_response(
                payload,
                region_id=selected_region,
                requested_from=requested_from,
                retrieved_at=retrieved_at,
                source_url=source_url,
            )
        except (ValueError, OverflowError) as exc:
            raise NesoAPIError(
                f"NESO Carbon Intensity API forecast data rejected: {exc}"
            ) from exc

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
        self._cached_forecasts: dict[
            int, tuple[datetime, CarbonIntensityForecastTimeline, float]
        ] = {}
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

    async def fetch_forecast_timeline(
        self,
        *,
        region_id: int,
        requested_from: datetime,
    ) -> CarbonIntensityForecastTimeline:
        """Cache one validated forecast per region and aligned request window."""
        if isinstance(region_id, bool) or not isinstance(region_id, int) or not 1 <= region_id <= 17:
            raise ValueError("region_id must be between 1 and 17")
        if requested_from.tzinfo is None or requested_from.utcoffset() is None:
            raise ValueError("requested_from must include a timezone")
        requested_from = requested_from.astimezone(UTC)

        cached = self._cached_forecasts.get(region_id)
        now_monotonic = self._clock()
        if (
            cached is not None
            and cached[0] == requested_from
            and now_monotonic < cached[2]
        ):
            return cached[1]

        async with self._lock:
            cached = self._cached_forecasts.get(region_id)
            now_monotonic = self._clock()
            if (
                cached is not None
                and cached[0] == requested_from
                and now_monotonic < cached[2]
            ):
                return cached[1]

            timeline = await self._client.fetch_forecast_timeline(
                region_id=region_id,
                requested_from=requested_from,
            )
            now_utc = datetime.now(UTC)
            remaining_validity = max(
                0.0,
                (timeline.valid_until - now_utc).total_seconds(),
            )
            cache_lifetime = min(self._cache_ttl_seconds, remaining_validity)
            self._cached_forecasts = {
                cached_region: entry
                for cached_region, entry in self._cached_forecasts.items()
                if self._clock() < entry[2] and cached_region != region_id
            }
            if cache_lifetime > 0:
                self._cached_forecasts[region_id] = (
                    requested_from,
                    timeline,
                    self._clock() + cache_lifetime,
                )
            return timeline
