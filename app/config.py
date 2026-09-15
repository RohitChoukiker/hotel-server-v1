"""Typed application configuration."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.enums import Environment


class AppConfig(BaseSettings):
    """HTTP application settings."""

    environment: Environment = Environment.DEVELOPMENT
    app_name: str = "Hotel Recommendation API"
    api_prefix: str = "/api/v1"
    debug: bool = False
    public_base_url: str = "http://localhost:8000"
    trusted_hosts: list[str] = ["localhost", "127.0.0.1", "testserver"]
    max_request_bytes: int = 2 * 1024 * 1024 * 1024


class DatabaseConfig(BaseSettings):
    """PostgreSQL connection pool settings."""

    url: SecretStr = SecretStr("postgresql+asyncpg://hotel:hotel@postgres:5432/hotel")
    pool_size: int = Field(default=10, ge=1, le=100)
    max_overflow: int = Field(default=20, ge=0, le=200)
    pool_timeout_seconds: int = Field(default=30, ge=1)

    @field_validator("url")
    @classmethod
    def require_async_postgresql(cls, value: SecretStr) -> SecretStr:
        """Reject sync or non-PostgreSQL database drivers."""
        if not value.get_secret_value().startswith("postgresql+asyncpg://"):
            raise ValueError("Database URL must use postgresql+asyncpg")
        return value


class RedisConfig(BaseSettings):
    """Redis cache, queue, and rate-limit settings."""

    url: SecretStr = SecretStr("redis://redis:6379/0")
    cache_prefix: str = "hotel-platform"
    fail_open: bool = True

    @field_validator("url")
    @classmethod
    def require_redis_url(cls, value: SecretStr) -> SecretStr:
        """Accept only Redis URI schemes."""
        if not value.get_secret_value().startswith(("redis://", "rediss://")):
            raise ValueError("Redis URL must use redis or rediss")
        return value


class JWTConfig(BaseSettings):
    """JWT signing and lifetime settings."""

    secret_key: SecretStr = SecretStr("development-only-secret-change-me-123456")
    algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    access_token_minutes: int = Field(default=15, ge=1, le=60)
    refresh_token_days: int = Field(default=30, ge=1, le=90)
    issuer: str = "hotel-platform"
    audience: str = "hotel-platform-users"


class CORSConfig(BaseSettings):
    """Cross-origin request settings."""

    allowed_origins: list[str] = ["http://localhost:3000"]
    allow_credentials: bool = True

    @field_validator("allowed_origins")
    @classmethod
    def reject_wildcard_with_credentials(cls, value: list[str]) -> list[str]:
        """Reject an unsafe wildcard credential configuration."""
        if "*" in value:
            raise ValueError("Wildcard CORS origins are not permitted")
        return value


class LoggingConfig(BaseSettings):
    """Logging settings."""

    level: str = "INFO"
    json_logs: bool = True


class SentryConfig(BaseSettings):
    """Sentry telemetry settings."""

    dsn: SecretStr | None = None
    traces_sample_rate: float = Field(default=0.1, ge=0.0, le=1.0)


class LLMConfig(BaseSettings):
    """Structured LLM adapter settings."""

    provider: Literal["deterministic", "openai"] = "deterministic"
    base_url: AnyHttpUrl = AnyHttpUrl("https://api.openai.com/v1")
    api_key: SecretStr | None = None
    model: str = "gpt-5-mini"
    timeout_seconds: int = Field(default=30, ge=1, le=120)


class StorageConfig(BaseSettings):
    """Import object-storage settings."""

    backend: Literal["local", "gcs"] = "local"
    local_path: Path = Path("/data/imports")
    gcs_bucket: str | None = None


class GCPConfig(BaseSettings):
    """Google Cloud integration settings."""

    project_id: str | None = None
    secret_prefix: str = "hotel-platform"


class WorkerConfig(BaseSettings):
    """Worker throughput settings."""

    task_time_limit_seconds: int = Field(default=3600, ge=60)
    import_batch_size: int = Field(default=1000, ge=100, le=10000)
    scraper_concurrency: int = Field(default=4, ge=1, le=20)


class RateLimitsConfig(BaseSettings):
    """Named endpoint rate limits."""

    auth: str = "10/minute"
    search: str = "120/minute"
    chat: str = "30/minute"
    recommendation: str = "20/minute"
    admin_import: str = "5/hour"


class ScraperConfig(BaseSettings):
    """Source scraper guardrails."""

    tripadvisor_enabled: bool = False
    request_timeout_seconds: int = Field(default=30, ge=1, le=120)
    max_retries: int = Field(default=5, ge=1, le=10)
    user_agent: str = "HotelPlatformDataOperator/1.0"
    tripadvisor_cookie: SecretStr | None = None


class Settings(BaseSettings):
    """Root settings object loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",
    )

    app: AppConfig = Field(default_factory=AppConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    jwt: JWTConfig = Field(default_factory=JWTConfig)
    cors: CORSConfig = Field(default_factory=CORSConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    sentry: SentryConfig = Field(default_factory=SentryConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    gcp: GCPConfig = Field(default_factory=GCPConfig)
    worker: WorkerConfig = Field(default_factory=WorkerConfig)
    rate_limits: RateLimitsConfig = Field(default_factory=RateLimitsConfig)
    scraper: ScraperConfig = Field(default_factory=ScraperConfig)

    @model_validator(mode="after")
    def validate_production_secrets(self) -> "Settings":
        """Fail fast for insecure production settings."""
        if self.app.environment is Environment.PRODUCTION:
            secret = self.jwt.secret_key.get_secret_value()
            insecure_markers = ("change-me", "development", "replace", "example")
            if len(secret) < 32 or any(
                marker in secret.casefold() for marker in insecure_markers
            ):
                raise ValueError("Production JWT secret must be a strong external secret")
            if self.app.debug:
                raise ValueError("Debug mode cannot be enabled in production")
            if "*" in self.app.trusted_hosts:
                raise ValueError("Wildcard trusted hosts are not permitted in production")
            if not self.app.public_base_url.startswith("https://"):
                raise ValueError("Production public_base_url must use HTTPS")
            if self.redis.fail_open:
                raise ValueError("Production Redis rate limiting must fail closed")
            database_url = self.database.url.get_secret_value().casefold()
            redis_url = self.redis.url.get_secret_value().casefold()
            if any(
                marker in database_url
                for marker in ("change-me", "url_encoded", "hotel:hotel@")
            ):
                raise ValueError("Production database URL contains a placeholder")
            if "redis_password" in redis_url or redis_url == "redis://redis:6379/0":
                raise ValueError("Production Redis URL contains a placeholder")
            if self.storage.backend == "gcs" and not self.storage.gcs_bucket:
                raise ValueError("GCS bucket is required when storage backend is gcs")
            if self.llm.provider == "openai" and self.llm.api_key is None:
                raise ValueError("OpenAI provider requires an API key")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return an immutable process-local settings snapshot."""
    return Settings()
