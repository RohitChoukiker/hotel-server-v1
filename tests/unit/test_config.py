"""Configuration security validation tests."""

import unittest

from pydantic import ValidationError

from app.config import AppConfig, DatabaseConfig, JWTConfig, RedisConfig, Settings
from app.enums import Environment


class ConfigTests(unittest.TestCase):
    """Production must fail fast on insecure defaults."""

    def test_secure_production_settings(self) -> None:
        settings = Settings(
            app=AppConfig(
                environment=Environment.PRODUCTION,
                public_base_url="https://api.example.com",
                trusted_hosts=["api.example.com"],
            ),
            database=DatabaseConfig(
                url="postgresql+asyncpg://hotel:strong@db.internal:5432/hotel"
            ),
            redis=RedisConfig(url="redis://:strong@redis:6379/0", fail_open=False),
            jwt=JWTConfig(secret_key="x" * 48),
        )
        self.assertIs(settings.app.environment, Environment.PRODUCTION)

    def test_production_rejects_weak_secret(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(
                app=AppConfig(
                    environment=Environment.PRODUCTION,
                    public_base_url="https://api.example.com",
                    trusted_hosts=["api.example.com"],
                ),
                database=DatabaseConfig(
                    url="postgresql+asyncpg://hotel:strong@db.internal:5432/hotel"
                ),
                redis=RedisConfig(
                    url="redis://:strong@redis:6379/0", fail_open=False
                ),
            )

    def test_production_rejects_fail_open_rate_limiting(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(
                app=AppConfig(
                    environment=Environment.PRODUCTION,
                    public_base_url="https://api.example.com",
                    trusted_hosts=["api.example.com"],
                ),
                database=DatabaseConfig(
                    url="postgresql+asyncpg://hotel:strong@db.internal:5432/hotel"
                ),
                redis=RedisConfig(url="redis://:strong@redis:6379/0"),
                jwt=JWTConfig(secret_key="x" * 48),
            )


if __name__ == "__main__":
    unittest.main()
