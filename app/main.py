from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
import uvicorn

from app.api.v1.energy import router as energy_router
from app.api.v1.simulations import router as simulations_router
from app.core.config import Settings, settings
from app.core.logging import setup_logging
from app.core.portfolio_guard import PublicDemoGuardMiddleware

_FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Application lifespan — startup / shutdown hooks."""
    setup_logging()
    yield


def _frontend_file(frontend_root: Path, index_file: Path, asset_path: str) -> FileResponse:
    reserved_paths = ("api", "docs", "redoc", "openapi.json")
    if any(asset_path == path or asset_path.startswith(f"{path}/") for path in reserved_paths):
        raise HTTPException(status_code=404, detail="Not found")
    candidate = (frontend_root / asset_path).resolve()
    if not candidate.is_relative_to(frontend_root):
        raise HTTPException(status_code=404, detail="Not found")
    if candidate.is_file():
        return FileResponse(candidate)
    if Path(asset_path).suffix:
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(index_file)


def create_app(
    application_settings: Settings = settings,
    frontend_directory: Path | None = None,
) -> FastAPI:
    """Create the API and, for container deployments, the built React frontend."""
    api_docs_enabled = application_settings.debug and not application_settings.simulation_only_mode
    application = FastAPI(
        title=application_settings.app_name,
        version="0.2.0",
        lifespan=lifespan,
        docs_url="/docs" if api_docs_enabled else None,
        redoc_url="/redoc" if api_docs_enabled else None,
        openapi_url="/openapi.json" if api_docs_enabled else None,
    )
    application.include_router(energy_router, prefix="/api/v1")
    application.include_router(simulations_router, prefix="/api/v1")
    application.add_middleware(
        PublicDemoGuardMiddleware,
        simulation_only_mode=application_settings.simulation_only_mode,
        max_request_body_bytes=application_settings.max_request_body_bytes,
        api_rate_limit_requests_per_minute=(
            application_settings.api_rate_limit_requests_per_minute
        ),
    )

    @application.get("/health", tags=["system"])
    async def health_check() -> dict[str, str]:
        """Health endpoint for readiness probes."""
        return {
            "status": "ok",
            "app": application_settings.app_name,
            "environment": application_settings.app_env,
        }

    if application_settings.serve_frontend:
        frontend_root = (frontend_directory or _FRONTEND_DIST).resolve()
        index_file = frontend_root / "index.html"
        if not frontend_root.is_dir() or not index_file.is_file():
            raise RuntimeError("A built frontend with index.html is required when SERVE_FRONTEND is enabled")

        @application.get("/", include_in_schema=False)
        async def serve_frontend_root() -> FileResponse:
            return FileResponse(index_file)

        @application.get("/{asset_path:path}", include_in_schema=False)
        async def serve_frontend_path(asset_path: str) -> FileResponse:
            return _frontend_file(frontend_root, index_file, asset_path)

    return application


app = create_app()


def main() -> None:
    """Start the development ASGI server."""
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )


if __name__ == "__main__":
    main()
