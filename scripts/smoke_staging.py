"""Exercise a deployed simulation-only portfolio instance without credentials."""

from __future__ import annotations

import argparse
from html.parser import HTMLParser
import json
import math
import os
from pathlib import Path
import sys
import time
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_SCENARIO_PATH = _REPOSITORY_ROOT / "examples" / "carbon_aware_ev_depot_demo.json"
_EVALUATION_PATH = "/api/v1/simulations/ev-depot/evaluate"


class _RejectRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


class _FrontendAssetParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.asset_paths: set[str] = set()
        self.script_paths: set[str] = set()
        self.has_external_resource = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        script_source = attributes.get("src")
        link_href = attributes.get("href")
        if tag == "script" and script_source is not None:
            self._add_resource(script_source, is_script=True)
        elif tag == "link" and link_href is not None:
            relations = set((attributes.get("rel") or "").lower().split())
            if relations.intersection({"icon", "modulepreload", "preload", "stylesheet"}):
                self._add_resource(link_href)

    def _add_resource(self, value: str, *, is_script: bool = False) -> None:
        parsed = urlsplit(value)
        if parsed.scheme or parsed.netloc or not parsed.path.startswith("/"):
            self.has_external_resource = True
            return
        self.asset_paths.add(value)
        if is_script:
            self.script_paths.add(value)


def _extract_frontend_assets(document: str) -> list[str]:
    parser = _FrontendAssetParser()
    parser.feed(document)
    parser.close()
    if parser.has_external_resource:
        raise RuntimeError("frontend assets must use same-origin URLs")
    if not parser.script_paths:
        raise RuntimeError("built frontend document does not contain a script asset")
    return sorted(parser.asset_paths)


def _request(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    timeout: float = 10.0,
) -> tuple[int, Any, bytes]:
    headers = {"Accept": "application/json, text/html;q=0.9"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    try:
        opener = build_opener(_RejectRedirectHandler())
        with opener.open(request, timeout=timeout) as response:
            return response.status, response.headers, response.read()
    except HTTPError as error:
        return error.code, error.headers, error.read()


def _wait_for_health(base_url: str, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    delay = 1.0
    last_status: int | None = None
    while time.monotonic() < deadline:
        try:
            status, _headers, body = _request(base_url, "/health", timeout=10.0)
            last_status = status
            if status == 200:
                payload = json.loads(body)
                if payload.get("status") != "ok":
                    raise RuntimeError("/health returned an unexpected status")
                return payload
            if status not in {429, 500, 502, 503, 504}:
                raise RuntimeError(f"/health returned HTTP {status}")
        except OSError:
            pass
        time.sleep(min(delay, max(0.0, deadline - time.monotonic())))
        delay = min(delay * 2, 8.0)
    suffix = f"; last HTTP status was {last_status}" if last_status is not None else ""
    raise RuntimeError(f"service did not become healthy within {timeout:g}s{suffix}")


def _require_status(actual: int, expected: int, path: str) -> None:
    if actual != expected:
        raise RuntimeError(f"{path} returned HTTP {actual}; expected {expected}")


def _verify_read_only_neso_route(base_url: str) -> None:
    path = "/api/v1/energy/carbon-intensity"
    status, headers, _body = _request(base_url, path, method="POST", body=b"{}")
    _require_status(status, 405, path)
    if headers.get("Allow") != "GET":
        raise RuntimeError(f"{path} did not advertise GET as its only allowed method")


def _validate_scenario_response(body: bytes) -> dict[str, Any]:
    result = json.loads(body)
    expected_values = {
        "baseline_carbon_proxy": 2_875.0,
        "carbon_aware_carbon_proxy": 950.0,
        "absolute_carbon_proxy_reduction": 1_925.0,
    }
    if result.get("comparison_eligible") is not True:
        raise RuntimeError("synthetic evaluation was not eligible for equal-service comparison")
    for field, expected in expected_values.items():
        actual = result.get(field)
        if not isinstance(actual, (int, float)) or not math.isclose(
            actual, expected, rel_tol=0.0, abs_tol=1e-9
        ):
            raise RuntimeError(f"synthetic evaluation returned an unexpected {field}")
    provenance = result.get("input_provenance", {})
    if provenance.get("source_classification") != "synthetic_scenario":
        raise RuntimeError("synthetic input provenance was not retained")
    if provenance.get("geography_code") != "simulation-only":
        raise RuntimeError("simulation-only geography was not retained")
    if result.get("real_world_emissions_reduction_claimed") is not False:
        raise RuntimeError("the evaluation made an unsupported real-world claim")
    return result


def _validate_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("--base-url must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("--base-url must not contain credentials, a query, or a fragment")
    if parsed.path not in {"", "/"}:
        raise ValueError("--base-url must not contain a path")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("unencrypted HTTP is permitted only for a loopback smoke test")
    return value.rstrip("/")


def run_smoke(base_url: str, startup_timeout: float = 90.0) -> dict[str, Any]:
    base_url = _validate_base_url(base_url)
    health = _wait_for_health(base_url, startup_timeout)
    frontend_assets: list[str] = []

    for path in ("/", "/portfolio-smoke-route"):
        status, headers, body = _request(base_url, path)
        _require_status(status, 200, path)
        if not headers.get_content_type().startswith("text/html"):
            raise RuntimeError(f"{path} did not return an HTML document")
        if b'id="root"' not in body:
            raise RuntimeError(f"{path} did not return the built React entry document")
        if path == "/":
            frontend_assets = _extract_frontend_assets(body.decode("utf-8"))
            for asset_path in frontend_assets:
                asset_status, asset_headers, asset_body = _request(base_url, asset_path)
                _require_status(asset_status, 200, asset_path)
                if not asset_body or asset_headers.get_content_type() == "text/html":
                    raise RuntimeError(f"{asset_path} did not return a built frontend asset")

    scenario = json.loads(_SCENARIO_PATH.read_text(encoding="utf-8"))
    scenario["vehicles"][0]["required_energy_kwh"] = 8.5
    scenario["carbon_intensity_profile"][1]["intensity_value"] = 50.0
    request_body = json.dumps(scenario, separators=(",", ":")).encode()
    status, _headers, response_body = _request(
        base_url,
        _EVALUATION_PATH,
        method="POST",
        body=request_body,
        timeout=20.0,
    )
    _require_status(status, 200, _EVALUATION_PATH)
    result = _validate_scenario_response(response_body)

    _verify_read_only_neso_route(base_url)
    for path in (
        "/api/v1/energy/carbon-gate",
        "/docs",
        "/openapi.json",
    ):
        status, _headers, _body = _request(base_url, path)
        _require_status(status, 404, path)

    return {
        "health": health["status"],
        "app": health.get("app"),
        "environment": health.get("environment"),
        "frontend": f"served {len(frontend_assets)} built assets with history fallback",
        "synthetic_evaluation": {
            "baseline_carbon_proxy": result["baseline_carbon_proxy"],
            "carbon_aware_carbon_proxy": result["carbon_aware_carbon_proxy"],
            "absolute_carbon_proxy_reduction": result["absolute_carbon_proxy_reduction"],
            "comparison_eligible": result["comparison_eligible"],
            "real_world_emissions_reduction_claimed": result[
                "real_world_emissions_reduction_claimed"
            ],
        },
        "neso_carbon_intensity_route": "GET-only; write method rejected without an upstream request",
        "simulation_only_routes": "verified",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=os.environ.get("STAGING_BASE_URL"),
        help="deployed service origin (or STAGING_BASE_URL); no trailing path",
    )
    parser.add_argument(
        "--startup-timeout",
        type=float,
        default=90.0,
        help="maximum seconds to wait for /health (default: 90)",
    )
    arguments = parser.parse_args()
    if not arguments.base_url:
        parser.error("--base-url or STAGING_BASE_URL is required")
    if arguments.startup_timeout <= 0:
        parser.error("--startup-timeout must be positive")

    try:
        base_url = _validate_base_url(arguments.base_url)
        result = run_smoke(base_url, arguments.startup_timeout)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        print(f"Staging smoke failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
