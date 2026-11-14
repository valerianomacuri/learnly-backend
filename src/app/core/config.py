from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

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
