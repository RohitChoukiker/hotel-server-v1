"""Configuration security validation tests."""

import unittest

from pydantic import ValidationError

from app.config import AppConfig, DatabaseConfig, JWTConfig, LLMConfig, RedisConfig, Settings
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
            llm=LLMConfig(provider="anthropic", api_key="test-key"),
        )
        self.assertIs(settings.app.environment, Environment.PRODUCTION)

    def test_anthropic_configuration_uses_canonical_settings(self) -> None:
        config = LLMConfig(
            provider="anthropic",
            base_url="https://api.anthropic.com",
            api_key="test-key",
            model="claude-sonnet-4-5",
            timeout_s=17,
        )
        self.assertEqual(config.provider, "anthropic")
        self.assertEqual(config.api_key.get_secret_value(), "test-key")
        self.assertEqual(str(config.base_url), "https://api.anthropic.com/")
        self.assertEqual(config.model, "claude-sonnet-4-5")
        self.assertEqual(config.timeout_s, 17)

    def test_production_rejects_empty_anthropic_api_key(self) -> None:
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
                redis=RedisConfig(url="redis://:strong@redis:6379/0", fail_open=False),
                jwt=JWTConfig(secret_key="x" * 48),
                llm=LLMConfig(provider="anthropic", api_key=""),
            )

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
                jwt=JWTConfig(secret_key="weak-production-fixture"),
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
