"""Shared helpers for Claude adaptive onboarding."""

import json
import math
from typing import Any


class AdaptiveProviderError(RuntimeError):
    """Provider failure that is safe to expose as a recoverable onboarding error."""

    public_message = "Claude adaptive onboarding provider failed"


class DuplicateAdaptiveQuestionError(AdaptiveProviderError):
    """Claude proposed a question whose canonical topic is already answered."""


class AdaptiveAuthenticationError(AdaptiveProviderError):
    """Claude rejected the configured credentials."""

    public_message = "Claude adaptive onboarding authentication failed"


class AdaptiveRateLimitError(AdaptiveProviderError):
    """Claude rate-limited an onboarding request."""

    public_message = "Claude adaptive onboarding provider is rate-limited"


class AdaptiveTimeoutError(AdaptiveProviderError):
    """Claude did not respond before the configured timeout."""

    public_message = "Claude adaptive onboarding provider timed out"


class AdaptiveConfigurationError(AdaptiveProviderError):
    """Claude runtime configuration is incomplete or invalid."""

    public_message = "Claude adaptive onboarding is not configured"


class ClaudeUnavailableAdaptiveOnboardingProvider:
    """Explicit unavailable state; it never fabricates onboarding questions."""

    provider_name = "anthropic"

    def __init__(self, model: str, prompt_version: str) -> None:
        self.model = model
        self.prompt_version = prompt_version

    async def generate_next_question(self, context: dict[str, Any]) -> Any:
        del context
        raise AdaptiveConfigurationError()


def context_json(context: dict[str, Any]) -> str:
    """Serialize provider context without exposing credentials or account metadata."""
    return json.dumps(context, ensure_ascii=False, separators=(",", ":"), default=str)


def bounded_confidence(value: float) -> float:
    """Keep provider confidence safe if used by an adapter."""
    return max(0.0, min(1.0, value if math.isfinite(value) else 0.0))
