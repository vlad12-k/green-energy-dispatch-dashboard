"""Tests for the single-origin portfolio deployment profile."""

import json
from collections.abc import Iterable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import SupportsIndex

from httpx import ASGITransport, AsyncClient
import pytest
from pydantic import ValidationError
from starlette.types import Message, Receive, Scope, Send

from app.core.config import Settings
from app.core import portfolio_guard
from app.main import create_app
from scripts import smoke_staging

_EXAMPLE_SCENARIO = (
    Path(__file__).resolve().parents[2]
    / "examples"
    / "carbon_aware_ev_depot_demo.json"
)


def _synthetic_smoke_scenario() -> dict[str, object]:
    scenario = json.loads(_EXAMPLE_SCENARIO.read_text(encoding="utf-8"))
    scenario["vehicles"][0]["required_energy_kwh"] = 8.5
    scenario["carbon_intensity_profile"][1]["intensity_value"] = 50.0
    return scenario


def test_portfolio_settings_default_to_local_compatible_mode() -> None:
    configured = Settings()

    assert configured.simulation_only_mode is False
    assert configured.serve_frontend is False
    assert configured.max_request_body_bytes == 65_536
    assert configured.api_rate_limit_requests_per_minute == 60


def test_portfolio_settings_accept_bounded_runtime_controls() -> None:
    configured = Settings(
        simulation_only_mode=True,
        serve_frontend=True,
        max_request_body_bytes=65_536,
        api_rate_limit_requests_per_minute=60,
    )

    assert configured.simulation_only_mode is True
    assert configured.serve_frontend is True
    assert configured.max_request_body_bytes == 65_536
    assert configured.api_rate_limit_requests_per_minute == 60


def test_portfolio_request_limits_reject_unsafe_values() -> None:
    with pytest.raises(ValidationError):
        Settings(max_request_body_bytes=0)
    with pytest.raises(ValidationError):
        Settings(api_rate_limit_requests_per_minute=0)


def test_smoke_base_url_accepts_loopback_http_and_rejects_path_prefix() -> None:
    assert smoke_staging._validate_base_url("http://127.0.0.1:18000/") == "http://127.0.0.1:18000"
    with pytest.raises(ValueError, match="must not contain a path"):
        smoke_staging._validate_base_url("https://demo.example.test/subpath")


def test_smoke_request_does_not_follow_cross_origin_redirect() -> None:
    target_requests = 0

    class TargetHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            nonlocal target_requests
            target_requests += 1
            self.send_response(200)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), TargetHandler) as target_server:

        class RedirectHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self.send_response(302)
                self.send_header(
                    "Location",
                    f"http://127.0.0.1:{target_server.server_port}/private",
                )
                self.end_headers()

            def log_message(self, format: str, *args: object) -> None:
                pass

        with ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler) as source_server:
            threads = [
                Thread(target=server.serve_forever, daemon=True)
                for server in (target_server, source_server)
            ]
            for thread in threads:
                thread.start()
            try:
                status, _headers, _body = smoke_staging._request(
                    f"http://127.0.0.1:{source_server.server_port}", "/health"
                )
            finally:
                source_server.shutdown()
                target_server.shutdown()
                for thread in threads:
                    thread.join(timeout=2)

    assert status == 302
    assert target_requests == 0


def test_smoke_runner_validates_base_url_before_any_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        smoke_staging,
        "_wait_for_health",
        lambda *_args: pytest.fail("invalid base URLs must be rejected before probing"),
    )

    with pytest.raises(ValueError, match=r"absolute HTTP\(S\)"):
        smoke_staging.run_smoke("file:///tmp")


def test_smoke_verifies_read_only_neso_route_without_fetching_live_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict[str, object]]] = []

    def request(base_url: str, path: str, **kwargs: object):
        calls.append((base_url, path, kwargs))
        return 405, {"Allow": "GET"}, b'{"detail":"Method not allowed"}'

    monkeypatch.setattr(smoke_staging, "_request", request)

    smoke_staging._verify_read_only_neso_route("https://demo.example.test")

    assert calls == [(
        "https://demo.example.test",
        "/api/v1/energy/carbon-intensity",
        {"method": "POST", "body": b"{}"},
    )]


def test_smoke_extracts_production_frontend_assets() -> None:
    document = """
    <link rel="icon" href="/favicon.svg">
    <script type="module" src="/assets/app.js"></script>
    <link rel="stylesheet" href="/assets/app.css">
    """

    assert smoke_staging._extract_frontend_assets(document) == [
        "/assets/app.css",
        "/assets/app.js",
        "/favicon.svg",
    ]


def test_smoke_rejects_external_production_frontend_assets() -> None:
    document = '<script type="module" src="https://cdn.example.test/app.js"></script>'

    with pytest.raises(RuntimeError, match="same-origin URLs"):
        smoke_staging._extract_frontend_assets(document)


def test_smoke_health_retries_a_transient_connection_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def request(_base_url: str, _path: str, *, timeout: float) -> tuple[int, object, bytes]:
        nonlocal attempts
        assert timeout == 10.0
        attempts += 1
        if attempts == 1:
            raise ConnectionResetError("service is still starting")
        return 200, object(), b'{"status":"ok"}'

    monkeypatch.setattr(smoke_staging, "_request", request)
    monkeypatch.setattr(smoke_staging.time, "sleep", lambda _delay: None)

    assert smoke_staging._wait_for_health("https://demo.example.test", timeout=1) == {
        "status": "ok"
    }
    assert attempts == 2


@pytest.mark.asyncio
async def test_frontend_serves_assets_and_client_side_routes(tmp_path: Path) -> None:
    frontend = tmp_path / "dist"
    assets = frontend / "assets"
    assets.mkdir(parents=True)
    (frontend / "index.html").write_text(
        "<!doctype html><main id='portfolio-root'></main>", encoding="utf-8"
    )
    (assets / "app.js").write_text("console.info('built')", encoding="utf-8")
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("not public", encoding="utf-8")
    (frontend / "linked-secret.txt").symlink_to(outside_file)
    application = create_app(
        application_settings=Settings(serve_frontend=True),
        frontend_directory=frontend,
    )

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        root = await client.get("/")
        route = await client.get("/scenario/example")
        asset = await client.get("/assets/app.js")
        missing_asset = await client.get("/assets/missing.js")
        unknown_api = await client.get("/api/v1/not-enabled")
        docs_page = await client.get("/docs")
        redoc_page = await client.get("/redoc")
        linked_file = await client.get("/linked-secret.txt")

    assert root.status_code == 200
    assert route.status_code == 200
    assert "portfolio-root" in route.text
    assert asset.status_code == 200
    assert asset.text == "console.info('built')"
    assert missing_asset.status_code == 404
    assert unknown_api.status_code == 404
    assert docs_page.status_code == 404
    assert redoc_page.status_code == 404
    assert linked_file.status_code == 404


def test_frontend_profile_fails_fast_when_build_is_missing(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="built frontend"):
        create_app(
            application_settings=Settings(serve_frontend=True),
            frontend_directory=tmp_path / "missing-dist",
        )


@pytest.mark.asyncio
async def test_simulation_only_profile_preserves_synthetic_evaluation_and_hides_policy_route() -> None:
    application = create_app(
        application_settings=Settings(simulation_only_mode=True)
    )
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        evaluation = await client.post(
            "/api/v1/simulations/ev-depot/evaluate",
            json=_synthetic_smoke_scenario(),
        )
        policy_endpoint = await client.post(
            "/api/v1/energy/carbon-gate",
            json={"carbon_intensity_gco2_per_kwh": 100.0},
        )
        unknown_endpoint = await client.get("/api/v1/not-enabled")

    assert evaluation.status_code == 200
    result = evaluation.json()
    assert result["comparison_eligible"] is True
    assert result["baseline_carbon_proxy"] == 2_875
    assert result["carbon_aware_carbon_proxy"] == 950
    assert result["absolute_carbon_proxy_reduction"] == 1_925
    assert result["input_provenance"]["source_classification"] == "synthetic_scenario"
    assert result["real_world_emissions_reduction_claimed"] is False
    assert policy_endpoint.status_code == 404
    assert unknown_endpoint.status_code == 404


@pytest.mark.asyncio
async def test_simulation_only_profile_hides_debug_api_documentation() -> None:
    application = create_app(
        application_settings=Settings(
            app_env="development",
            debug=True,
            simulation_only_mode=True,
        )
    )
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        docs_page = await client.get("/docs")
        redoc_page = await client.get("/redoc")
        openapi_schema = await client.get("/openapi.json")

    assert docs_page.status_code == 404
    assert redoc_page.status_code == 404
    assert openapi_schema.status_code == 404


@pytest.mark.asyncio
async def test_simulation_only_profile_rejects_oversized_chunked_requests() -> None:
    application = create_app(
        application_settings=Settings(
            simulation_only_mode=True,
            max_request_body_bytes=1024,
        )
    )

    async def oversized_body():
        yield b'{"scenario_id":"synthetic"'
        yield b" " * 2048
        yield b"}"

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/simulations/ev-depot/evaluate",
            content=oversized_body(),
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 413


@pytest.mark.asyncio
async def test_oversized_single_chunk_is_rejected_before_buffering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TrackingBytearray(bytearray):
        def __init__(self) -> None:
            super().__init__()
            self.extend_calls = 0
            buffers.append(self)

        def extend(self, value: Iterable[SupportsIndex]) -> None:
            self.extend_calls += 1
            super().extend(value)

    buffers: list[TrackingBytearray] = []
    monkeypatch.setattr(portfolio_guard, "bytearray", TrackingBytearray, raising=False)

    async def unexpected_app(_scope: Scope, _receive: Receive, _send: Send) -> None:
        pytest.fail("an oversized request must not reach the application")

    middleware = portfolio_guard.PublicDemoGuardMiddleware(
        app=unexpected_app,
        simulation_only_mode=True,
        max_request_body_bytes=1024,
        api_rate_limit_requests_per_minute=60,
    )
    oversized_chunk = b"x" * 2048
    received = False
    sent_messages: list[Message] = []

    async def receive() -> Message:
        nonlocal received
        if received:
            return {"type": "http.disconnect"}
        received = True
        return {"type": "http.request", "body": oversized_chunk, "more_body": False}

    async def send(message: Message) -> None:
        sent_messages.append(message)

    scope: Scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/simulations/ev-depot/evaluate",
        "headers": [],
    }
    await middleware(
        scope,
        receive,
        send,
    )

    assert sent_messages[0]["status"] == 413
    assert buffers[0].extend_calls == 0


@pytest.mark.asyncio
async def test_simulation_only_profile_applies_a_bounded_rate_limit() -> None:
    application = create_app(
        application_settings=Settings(
            simulation_only_mode=True,
            api_rate_limit_requests_per_minute=1,
        )
    )
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post(
            "/api/v1/simulations/ev-depot/evaluate",
            json=_synthetic_smoke_scenario(),
        )
        second = await client.post(
            "/api/v1/simulations/ev-depot/evaluate",
            json=_synthetic_smoke_scenario(),
        )

    assert first.status_code == 200
    assert second.status_code == 429
    assert int(second.headers["retry-after"]) >= 1


@pytest.mark.asyncio
async def test_rate_limited_request_is_rejected_before_reading_its_body() -> None:
    application = create_app(
        application_settings=Settings(
            simulation_only_mode=True,
            api_rate_limit_requests_per_minute=1,
        )
    )
    transport = ASGITransport(app=application)

    async def unread_body():
        raise AssertionError("a rate-limited request body must not be consumed")
        yield b""

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post(
            "/api/v1/simulations/ev-depot/evaluate",
            json=_synthetic_smoke_scenario(),
        )
        second = await client.post(
            "/api/v1/simulations/ev-depot/evaluate",
            content=unread_body(),
            headers={"content-type": "application/json"},
        )

    assert first.status_code == 200
    assert second.status_code == 429
