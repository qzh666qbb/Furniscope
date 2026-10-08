"""API runtime configuration. Secret values are environment-only."""

from functools import lru_cache
from pathlib import Path
import re
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ApiSettings(BaseSettings):
    app_name: str = "FurniScope API"
    app_env: Literal["development", "test", "production"] = "development"
    app_debug: bool = False
    database_url: str = "postgresql+asyncpg://postgres@127.0.0.1:5432/furniscope"
    admin_database_url: str | None = None
    worker_database_url: str | None = None
    database_pool_size: int = 10
    database_max_overflow: int = 10
    database_pool_timeout_seconds: int = 10
    database_command_timeout_seconds: int = 30
    database_allow_runtime_ddl: bool = False
    database_require_runtime_role_separation: bool = False
    deployment_cell_code: str = "cell-local"
    deployment_region: str = "local"

    furniscope_jwt_private_key: SecretStr | None = None
    furniscope_jwt_public_keys_json: SecretStr | None = None
    furniscope_jwt_issuer: str = "furniscope-api"
    furniscope_jwt_audience: str = "furniscope-web"
    access_token_ttl_seconds: int = 900
    jwt_clock_skew_seconds: int = 60
    refresh_token_ttl_seconds: int = 2_592_000

    log_level: str = "INFO"
    cors_allowed_origins: list[str] = []
    storage_backend: Literal["local", "s3"] = "local"
    demo_storage_root: str = "var/demo_uploads"
    object_storage_endpoint: str | None = None
    object_storage_access_key: SecretStr | None = None
    object_storage_secret_key: SecretStr | None = None
    object_storage_bucket: str = "furniscope"
    object_storage_region: str = "us-east-1"
    object_storage_secure: bool = True
    object_storage_auto_create_bucket: bool = False
    upload_max_bytes: int = 26_214_400
    analysis_ontology_version: str = "sofa-ontology-v1"
    analysis_scoring_version: str = "opportunity-score-v3"
    analysis_prompt_bundle_version: str = "furniscope-agent-v1"
    analysis_model_route_version: str = "token-plan-route-v1"
    analysis_worker_mode: Literal["demo_only", "token_plan_demo", "external"] = "demo_only"
    product_parse_mode: Literal["demo", "model"] = "demo"
    analysis_tool_mode: Literal["synthetic", "external"] = "synthetic"
    internal_service_token: SecretStr | None = None
    langgraph_database_url: str | None = None
    redis_url: str | None = None
    job_queue_mode: Literal["in_process", "redis"] = "in_process"
    worker_concurrency: int = 2
    worker_block_ms: int = 5000
    worker_lease_ms: int = 120_000
    worker_max_attempts: int = 3
    worker_job_timeout_seconds: int = 900
    worker_tenant_max_in_flight: int = 1
    worker_tenant_queue_limit: int = 100
    worker_tenant_defer_ms: int = 250
    worker_metrics_port: int = 9108
    aliyun_model_router_api_key: SecretStr | None = None
    aliyun_model_router_chat_base_url: str = (
        "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    )
    aliyun_model_router_chat_path: str = "/chat/completions"
    aliyun_model_router_text_model: str = "qwen3.7-plus"
    aliyun_model_router_embedding_base_url: str = (
        "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    )
    aliyun_model_router_embedding_path: str = "/embeddings"
    aliyun_model_router_embedding_model: str = "text-embedding-v4"
    aliyun_model_router_embedding_dimensions: int = 1024
    aliyun_model_router_rerank_base_url: str = (
        "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-api/v1"
    )
    aliyun_model_router_rerank_path: str = "/reranks"
    aliyun_model_router_rerank_model: str = "qwen3-rerank"
    aliyun_model_router_timeout_seconds: float = 90.0
    aliyun_model_router_max_retries: int = 2
    deepseek_api_key: SecretStr | None = None
    deepseek_chat_base_url: str = "https://api.deepseek.com/v1"
    deepseek_chat_path: str = "/chat/completions"
    deepseek_text_model: str = "deepseek-chat"
    forecast_enabled: bool = True
    forecast_engine_root: str = "backend/furniscope_forecast"
    forecast_state_dir: str = "forecast_assets/state"
    forecast_artifact_root: str = "forecast_assets/tenants"
    forecast_model_version: str = "sales-forecast-v4"
    forecast_max_pairs: int = 100
    data_query_timeout_ms: int = 5000
    knowledge_relevance_threshold: float = 0.35
    knowledge_auto_select_limit: int = 5
    memory_auto_extract_enabled: bool = False
    agent_runtime_enabled: bool = False

    @model_validator(mode="after")
    def validate_contract(self) -> "ApiSettings":
        # Docker Compose commonly supplies an unset optional secret as an empty
        # string.  Treat it as absent so we never build an invalid
        # ``Authorization: Bearer `` header.
        if (
            self.aliyun_model_router_api_key is not None
            and not self.aliyun_model_router_api_key.get_secret_value().strip()
        ):
            self.aliyun_model_router_api_key = None
        if (
            self.deepseek_api_key is not None
            and not self.deepseek_api_key.get_secret_value().strip()
        ):
            self.deepseek_api_key = None
        for field_name in (
            "object_storage_access_key",
            "object_storage_secret_key",
        ):
            secret = getattr(self, field_name)
            if secret is not None and not secret.get_secret_value().strip():
                setattr(self, field_name, None)
        if not self.database_url.startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL must use postgresql+asyncpg")
        for name, url in {
            "ADMIN_DATABASE_URL": self.admin_database_url,
            "WORKER_DATABASE_URL": self.worker_database_url,
        }.items():
            if url is not None and not url.startswith("postgresql+asyncpg://"):
                raise ValueError(f"{name} must use postgresql+asyncpg")
        if not 1 <= self.database_pool_size <= 100:
            raise ValueError("DATABASE_POOL_SIZE must be between 1 and 100")
        if self.app_env == "production" and self.database_allow_runtime_ddl:
            raise ValueError("Production forbids runtime schema DDL")
        if self.app_env == "production" and not self.database_require_runtime_role_separation:
            raise ValueError("Production requires database runtime-role separation")
        if not re.fullmatch(r"[a-z][a-z0-9-]{1,63}", self.deployment_cell_code):
            raise ValueError("DEPLOYMENT_CELL_CODE must be a lowercase cell identifier")
        if not re.fullmatch(r"[a-z][a-z0-9-]{1,63}", self.deployment_region):
            raise ValueError("DEPLOYMENT_REGION must be a lowercase region identifier")
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
        if self.app_env == "production" and self.storage_backend != "s3":
            raise ValueError("Production requires STORAGE_BACKEND=s3")
        if self.storage_backend == "s3":
            missing_storage = [
                name for name, value in {
                    "OBJECT_STORAGE_ENDPOINT": self.object_storage_endpoint,
                    "OBJECT_STORAGE_ACCESS_KEY": self.object_storage_access_key,
                    "OBJECT_STORAGE_SECRET_KEY": self.object_storage_secret_key,
                }.items() if not value
            ]
            if missing_storage:
                raise ValueError(
                    "S3 storage requires " + ", ".join(missing_storage)
                )
            if "://" in (self.object_storage_endpoint or ""):
                raise ValueError(
                    "OBJECT_STORAGE_ENDPOINT must be host:port without a URL scheme"
                )
            if not re.fullmatch(
                r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]",
                self.object_storage_bucket,
            ):
                raise ValueError("OBJECT_STORAGE_BUCKET is invalid")
            if self.app_env == "production" and not self.object_storage_secure:
                raise ValueError("Production object storage requires TLS")
            if self.app_env == "production" and self.object_storage_auto_create_bucket:
                raise ValueError("Production object storage bucket must be pre-provisioned")
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
        if self.app_env == "production":
            database_urls = {
                "ADMIN_DATABASE_URL": self.admin_database_url,
                "WORKER_DATABASE_URL": self.worker_database_url,
            }
            missing = [name for name, url in database_urls.items() if not url]
            if missing:
                raise ValueError("Production requires separate " + ", ".join(missing))
            if any(url == self.database_url for url in database_urls.values()):
                raise ValueError("Production database roles must use distinct connection URLs")
            main_sync_url = self.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
            if self.langgraph_database_url == main_sync_url:
                raise ValueError("Production requires a dedicated LANGGRAPH_DATABASE_URL")
        if self.job_queue_mode == "redis" and not self.redis_url:
            raise ValueError("REDIS_URL is required when JOB_QUEUE_MODE=redis")
        if self.app_env == "production" and self.job_queue_mode != "redis":
            raise ValueError("Production requires JOB_QUEUE_MODE=redis")
        if not 1 <= self.worker_concurrency <= 32:
            raise ValueError("WORKER_CONCURRENCY must be between 1 and 32")
        if not 100 <= self.worker_block_ms <= 60_000:
            raise ValueError("WORKER_BLOCK_MS must be between 100 and 60000")
        if not 10_000 <= self.worker_lease_ms <= 3_600_000:
            raise ValueError("WORKER_LEASE_MS must be between 10000 and 3600000")
        if not 1 <= self.worker_max_attempts <= 10:
            raise ValueError("WORKER_MAX_ATTEMPTS must be between 1 and 10")
        if not 1 <= self.worker_job_timeout_seconds <= 86_400:
            raise ValueError("WORKER_JOB_TIMEOUT_SECONDS must be between 1 and 86400")
        if not 1 <= self.worker_tenant_max_in_flight <= 16:
            raise ValueError("WORKER_TENANT_MAX_IN_FLIGHT must be between 1 and 16")
        if not 1 <= self.worker_tenant_queue_limit <= 100_000:
            raise ValueError("WORKER_TENANT_QUEUE_LIMIT must be between 1 and 100000")
        if not 10 <= self.worker_tenant_defer_ms <= 10_000:
            raise ValueError("WORKER_TENANT_DEFER_MS must be between 10 and 10000")
        if not 1024 <= self.worker_metrics_port <= 65_535:
            raise ValueError("WORKER_METRICS_PORT must be between 1024 and 65535")
        if self.app_env == "production" and self.analysis_worker_mode in {
            "demo_only", "token_plan_demo"
        }:
            raise ValueError("Demo analysis workers are forbidden in production")
        if self.app_env == "production" and self.product_parse_mode != "model":
            raise ValueError("Production requires PRODUCT_PARSE_MODE=model")
        if self.app_env == "production" and self.analysis_tool_mode != "external":
            raise ValueError("Production requires ANALYSIS_TOOL_MODE=external")
        if self.app_env == "production" and self.internal_service_token is None:
            raise ValueError("Production requires INTERNAL_SERVICE_TOKEN")
        if self.analysis_worker_mode == "token_plan_demo" and not self._has_any_chat_key():
            raise ValueError("ALIYUN_MODEL_ROUTER_API_KEY or DEEPSEEK_API_KEY is required for token_plan_demo")
        if not self.aliyun_model_router_chat_base_url.startswith("https://"):
            raise ValueError("ALIYUN_MODEL_ROUTER_CHAT_BASE_URL must use HTTPS")
        if not self.aliyun_model_router_chat_path.startswith("/"):
            raise ValueError("ALIYUN_MODEL_ROUTER_CHAT_PATH must start with /")
        if not self.deepseek_chat_base_url.startswith("https://"):
            raise ValueError("DEEPSEEK_CHAT_BASE_URL must use HTTPS")
        if not self.deepseek_chat_path.startswith("/"):
            raise ValueError("DEEPSEEK_CHAT_PATH must start with /")
        if not self.deepseek_text_model.strip():
            raise ValueError("DEEPSEEK_TEXT_MODEL is required")
        for name, value in {
            "ALIYUN_MODEL_ROUTER_EMBEDDING_BASE_URL": self.aliyun_model_router_embedding_base_url,
            "ALIYUN_MODEL_ROUTER_RERANK_BASE_URL": self.aliyun_model_router_rerank_base_url,
        }.items():
            if not value.startswith("https://"):
                raise ValueError(f"{name} must use HTTPS")
        for name, value in {
            "ALIYUN_MODEL_ROUTER_EMBEDDING_PATH": self.aliyun_model_router_embedding_path,
            "ALIYUN_MODEL_ROUTER_RERANK_PATH": self.aliyun_model_router_rerank_path,
        }.items():
            if not value.startswith("/"):
                raise ValueError(f"{name} must start with /")
        if not self.aliyun_model_router_text_model.strip():
            raise ValueError("ALIYUN_MODEL_ROUTER_TEXT_MODEL is required")
        if not self.aliyun_model_router_embedding_model.strip():
            raise ValueError("ALIYUN_MODEL_ROUTER_EMBEDDING_MODEL is required")
        if self.aliyun_model_router_embedding_dimensions != 1024:
            raise ValueError("ALIYUN_MODEL_ROUTER_EMBEDDING_DIMENSIONS must be 1024")
        if not self.aliyun_model_router_rerank_model.strip():
            raise ValueError("ALIYUN_MODEL_ROUTER_RERANK_MODEL is required")
        if not 1 <= self.aliyun_model_router_timeout_seconds <= 300:
            raise ValueError("ALIYUN_MODEL_ROUTER_TIMEOUT_SECONDS must be between 1 and 300")
        if not 0 <= self.aliyun_model_router_max_retries <= 5:
            raise ValueError("ALIYUN_MODEL_ROUTER_MAX_RETRIES must be between 0 and 5")
        if not 1 <= self.forecast_max_pairs <= 1000:
            raise ValueError("FORECAST_MAX_PAIRS must be between 1 and 1000")
        if not 500 <= self.data_query_timeout_ms <= 30_000:
            raise ValueError("DATA_QUERY_TIMEOUT_MS must be between 500 and 30000")
        if not 0 <= self.knowledge_relevance_threshold <= 1:
            raise ValueError("KNOWLEDGE_RELEVANCE_THRESHOLD must be between 0 and 1")
        if not 1 <= self.knowledge_auto_select_limit <= 20:
            raise ValueError("KNOWLEDGE_AUTO_SELECT_LIMIT must be between 1 and 20")
        if self.forecast_enabled:
            engine_value = Path(self.forecast_engine_root).expanduser()
            state_value = Path(self.forecast_state_dir).expanduser()
            artifact_value = Path(self.forecast_artifact_root).expanduser()
            engine_root = (engine_value if engine_value.is_absolute()
                           else PROJECT_ROOT / engine_value).resolve()
            state_dir = (state_value if state_value.is_absolute()
                         else PROJECT_ROOT / state_value).resolve()
            artifact_root = (artifact_value if artifact_value.is_absolute()
                             else PROJECT_ROOT / artifact_value).resolve()
            if self.app_env == "production" and not engine_root.is_absolute():
                raise ValueError("FORECAST_ENGINE_ROOT must resolve to an absolute path")
            self.forecast_engine_root = str(engine_root)
            self.forecast_state_dir = str(state_dir)
            self.forecast_artifact_root = str(artifact_root)
        return self

    def _secret_value(self, secret: SecretStr | None) -> str:
        if secret is None:
            return ""
        return secret.get_secret_value().strip()

    def _has_any_chat_key(self) -> bool:
        return bool(self._secret_value(self.aliyun_model_router_api_key) or self._secret_value(self.deepseek_api_key))

    def has_model_router_key(self) -> bool:
        return self._has_any_chat_key()

    def has_deepseek_key(self) -> bool:
        return bool(self._secret_value(self.deepseek_api_key))

    def chat_provider_routes(self) -> list[dict[str, str]]:
        routes: list[dict[str, str]] = []
        aliyun_key = self._secret_value(self.aliyun_model_router_api_key)
        if aliyun_key:
            routes.append({
                "provider": "aliyun_token_plan",
                "api_key": aliyun_key,
                "base_url": self.aliyun_model_router_chat_base_url,
                "chat_path": self.aliyun_model_router_chat_path,
                "model_id": self.aliyun_model_router_text_model,
            })
        deepseek_key = self._secret_value(self.deepseek_api_key)
        if deepseek_key:
            routes.append({
                "provider": "deepseek",
                "api_key": deepseek_key,
                "base_url": self.deepseek_chat_base_url,
                "chat_path": self.deepseek_chat_path,
                "model_id": self.deepseek_text_model,
            })
        return routes

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> ApiSettings:
    return ApiSettings()
