"""API runtime configuration. Secret values are environment-only."""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ApiSettings(BaseSettings):
    app_name: str = "FurniScope API"
    app_env: Literal["development", "test", "production"] = "development"
    app_debug: bool = False
    database_url: str
    database_pool_size: int = 10
    database_max_overflow: int = 10
    database_pool_timeout_seconds: int = 10
    database_command_timeout_seconds: int = 30

    furniscope_jwt_private_key: SecretStr | None = None
    furniscope_jwt_public_keys_json: SecretStr | None = None
    furniscope_jwt_issuer: str = "furniscope-api"
    furniscope_jwt_audience: str = "furniscope-web"
    access_token_ttl_seconds: int = 900
    jwt_clock_skew_seconds: int = 60
    refresh_token_ttl_seconds: int = 2_592_000

    log_level: str = "INFO"
    cors_allowed_origins: list[str] = []
    demo_storage_root: str = "var/demo_uploads"
    upload_max_bytes: int = 10_485_760
    analysis_ontology_version: str = "sofa-ontology-v1"
    analysis_scoring_version: str = "opportunity-score-v1"
    analysis_prompt_bundle_version: str = "furniscope-agent-v1"
    analysis_model_route_version: str = "default-route-v1"
    analysis_worker_mode: Literal["demo_only", "external"] = "demo_only"
    langgraph_database_url: str | None = None

    @model_validator(mode="after")
    def validate_contract(self) -> "ApiSettings":
        if not self.database_url.startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL must use postgresql+asyncpg")
        if not 1 <= self.database_pool_size <= 100:
            raise ValueError("DATABASE_POOL_SIZE must be between 1 and 100")
        if self.access_token_ttl_seconds != 900:
            raise ValueError("ACCESS_TOKEN_TTL_SECONDS must be 900 per API V3.2")
        if self.jwt_clock_skew_seconds != 60:
            raise ValueError("JWT_CLOCK_SKEW_SECONDS must be 60 per API V3.2")
        if self.refresh_token_ttl_seconds != 2_592_000:
            raise ValueError("REFRESH_TOKEN_TTL_SECONDS must be 2592000 per API V3.2")
        if self.app_env == "production" and (
            self.furniscope_jwt_private_key is None
            or self.furniscope_jwt_public_keys_json is None
        ):
            raise ValueError("JWT private/public key configuration is required in production")
        if self.upload_max_bytes < 1 or self.upload_max_bytes > 52_428_800:
            raise ValueError("UPLOAD_MAX_BYTES must be between 1 and 52428800")
        versions = {
            "ANALYSIS_ONTOLOGY_VERSION": self.analysis_ontology_version,
            "ANALYSIS_SCORING_VERSION": self.analysis_scoring_version,
            "ANALYSIS_PROMPT_BUNDLE_VERSION": self.analysis_prompt_bundle_version,
            "ANALYSIS_MODEL_ROUTE_VERSION": self.analysis_model_route_version,
        }
        for name, value in versions.items():
            if not value.strip() or len(value) > 64:
                raise ValueError(f"{name} must contain 1-64 characters")
        if self.langgraph_database_url is None:
            self.langgraph_database_url = self.database_url.replace(
                "postgresql+asyncpg://", "postgresql://", 1
            )
        if not self.langgraph_database_url.startswith("postgresql://"):
            raise ValueError("LANGGRAPH_DATABASE_URL must use postgresql://")
        if self.app_env == "production" and self.analysis_worker_mode == "demo_only":
            raise ValueError("ANALYSIS_WORKER_MODE=demo_only is forbidden in production")
        return self

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> ApiSettings:
    return ApiSettings()
