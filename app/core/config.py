from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration; production mode is conservative by default."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_name: str = "green-energy-dispatch-dashboard"
    app_env: Literal["development", "testing", "production"] = "production"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1, le=65535)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    neso_api_base_url: str = "https://api.carbonintensity.org.uk"
    neso_region_id: int = Field(default=13, ge=1, le=17)
    neso_max_age_seconds: int = Field(default=3600, ge=60, le=86400)
    neso_timeout_seconds: float = Field(default=5.0, gt=0, le=30)

    @model_validator(mode="after")
    def production_is_not_debug(self) -> "Settings":
        if self.app_env == "production" and self.debug:
            raise ValueError("DEBUG=true is not allowed when APP_ENV=production")
        if not self.neso_api_base_url.startswith("https://"):
            raise ValueError("NESO_API_BASE_URL must use HTTPS")
        return self


settings = Settings()
