from contextlib import asynccontextmanager

from fastapi import FastAPI
import uvicorn

from app.api.v1.energy import router as energy_router
from app.core.config import settings
from app.core.logging import setup_logging


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Application lifespan — startup / shutdown hooks."""
    setup_logging()
    yield


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
)
app.include_router(energy_router, prefix="/api/v1")


@app.get("/health", tags=["system"])
async def health_check() -> dict[str, str]:
    """Health endpoint for readiness probes."""
    return {
        "status": "ok",
        "app": settings.app_name,
        "environment": settings.app_env,
    }


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
