from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

REQUIRED_DRIVER_PREFIX = "postgresql+psycopg://"


class Settings(BaseSettings):
    """Application settings, validated at startup from env vars / `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = Field(default="API", min_length=1)
    environment: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    db_echo: bool = False
    database_url: str

    # Authentication (HS256 JWT issued by POST /auth/login)
    jwt_secret: SecretStr = Field(min_length=16)
    jwt_algorithm: Literal["HS256"] = "HS256"
    access_token_ttl_minutes: int = Field(default=60, gt=0)

    # Refund confirmation / SLA (policy thresholds come from the policy front-matter)
    confirmation_secret: SecretStr | None = None
    confirmation_ttl_min: int = Field(default=10, gt=0)
    sla_hours: int = Field(default=48, gt=0)

    # Payout worker (spec 05)
    payout_fail_rate: float = Field(default=0.0, ge=0, le=1, alias="FAIL_RATE")
    payout_max_attempts: int = Field(default=3, gt=0)
    payout_backoff_base_seconds: float = Field(default=30.0, ge=0)
    payout_poll_seconds: float = Field(default=2.0, gt=0)

    # Browser origins allowed by CORS (comma-separated in `CORS_ORIGINS`)
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]

    # Seconds between SSE heartbeats on the supervisor stream
    sse_heartbeat_seconds: float = Field(default=15.0, gt=0)

    # Directory with the policy .md files (default: `policies/` at the repo root)
    policies_dir: Path | None = None

    # RAG (spec 07). No key => embeddings/LLM unavailable; refunds never need them.
    openai_api_key: SecretStr | None = None
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = Field(default=1536, gt=0)
    llm_model: str = "gpt-4o-mini"
    # Agent (spec 09): per-call LLM timeout and the client's own retries
    llm_timeout_s: float = Field(default=30.0, gt=0)
    llm_max_retries: int = Field(default=2, ge=0)
    relevance_threshold: float = Field(default=0.25, ge=0, le=1)
    rerank_model: str = "BAAI/bge-reranker-base"

    # Security & resilience (spec 12). No Redis => reads fail open, writes fail closed.
    redis_url: str | None = None
    chat_rate_limit: int = Field(default=20, gt=0)
    chat_rate_window_s: int = Field(default=60, gt=0)
    refund_rate_limit: int = Field(default=5, gt=0)
    refund_rate_window_s: int = Field(default=3600, gt=0)
    thread_token_budget: int = Field(default=50_000, gt=0)
    llm_fallback_model: str | None = None
    breaker_failure_threshold: int = Field(default=5, gt=0)
    breaker_reset_s: float = Field(default=30.0, gt=0)

    # Observability (spec 13). Everything is off by default: no network calls.
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str = "http://localhost:4318"
    tracing_provider: Literal["none", "langsmith"] = "none"
    langsmith_api_key: SecretStr | None = None
    langsmith_project: str = "learnly-refunds"
    langsmith_endpoint: str | None = None
    # Bearer token for /metrics; unset = open (restrict it at the network level).
    metrics_token: SecretStr | None = None

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [o.strip() for o in value.split(",") if o.strip()]
        return value

    @model_validator(mode="after")
    def _derive_confirmation_secret(self) -> Self:
        if self.confirmation_secret is None:
            self.confirmation_secret = self.jwt_secret
        return self

    @model_validator(mode="after")
    def _langsmith_needs_key(self) -> Self:
        if self.tracing_provider == "langsmith" and self.langsmith_api_key is None:
            raise ValueError("TRACING_PROVIDER=langsmith requires LANGSMITH_API_KEY")
        return self

    @model_validator(mode="after")
    def _reject_placeholder_secret_in_production(self) -> Self:
        placeholder = self.jwt_secret.get_secret_value().startswith("change-me")
        if self.environment == "production" and placeholder:
            raise ValueError("JWT_SECRET must be changed from the example value")
        return self

    @field_validator("database_url")
    @classmethod
    def _validate_database_url(cls, value: str) -> str:
        if not value.startswith(REQUIRED_DRIVER_PREFIX):
            raise ValueError(f"DATABASE_URL must start with '{REQUIRED_DRIVER_PREFIX}'")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
