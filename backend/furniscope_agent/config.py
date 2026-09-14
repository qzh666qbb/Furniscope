"""Runtime configuration. Secrets are read from environment variables only."""

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    database_url: str
    langgraph_database_url: str
    aliyun_model_router_api_key: SecretStr
    aliyun_model_router_chat_base_url: str
    aliyun_model_router_text_model: str = "qwen3.7-plus"
    aliyun_model_router_embedding_base_url: str | None = None
    aliyun_model_router_rerank_base_url: str | None = None
    aliyun_model_router_chat_path: str = "/chat/completions"
    aliyun_model_router_embedding_path: str = "/embeddings"
    aliyun_model_router_rerank_path: str = "/reranks"
    app_env: str = "development"
    app_debug: bool = False
    enable_synthetic_demo: bool = False

    @model_validator(mode="after")
    def validate_runtime_contract(self) -> "AgentSettings":
        if not self.database_url.startswith(("postgresql://", "postgresql+asyncpg://")):
            raise ValueError("DATABASE_URL must use PostgreSQL")
        if not self.langgraph_database_url.startswith("postgresql://"):
            raise ValueError("LANGGRAPH_DATABASE_URL must use PostgreSQL")
        router_urls = {"ALIYUN_MODEL_ROUTER_CHAT_BASE_URL": self.aliyun_model_router_chat_base_url}
        if self.aliyun_model_router_embedding_base_url:
            router_urls["ALIYUN_MODEL_ROUTER_EMBEDDING_BASE_URL"] = self.aliyun_model_router_embedding_base_url
        if self.aliyun_model_router_rerank_base_url:
            router_urls["ALIYUN_MODEL_ROUTER_RERANK_BASE_URL"] = self.aliyun_model_router_rerank_base_url
        for name, url in router_urls.items():
            if not url.startswith("https://"):
                raise ValueError(f"{name} must use HTTPS")
        if self.enable_synthetic_demo and self.app_env.lower() in {"production", "prod"}:
            raise ValueError("Synthetic demo adapter is forbidden in production")
        return self

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
