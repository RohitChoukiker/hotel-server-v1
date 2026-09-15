"""OpenAI-compatible strict JSON-schema adapter without embeddings or RAG."""

import json
from typing import Any

import httpx
from pydantic import TypeAdapter

from app.config import LLMConfig
from app.integrations.llm.base import AttributeMentionOutput, ChatIntentOutput, PreferenceOutput


class OpenAIStructuredInterpreter:
    """Constrained-output text interpreter using an OpenAI-compatible API."""

    def __init__(self, config: LLMConfig, client: httpx.AsyncClient) -> None:
        if config.api_key is None:
            raise ValueError("LLM API key is required for the OpenAI provider")
        self._config = config
        self._client = client

    async def _request(self, name: str, schema: dict[str, Any], prompt: str) -> Any:
        response = await self._client.post(
            f"{str(self._config.base_url).rstrip('/')}/responses",
            headers={"Authorization": f"Bearer {self._config.api_key.get_secret_value()}"},
            json={
                "model": self._config.model,
                "input": prompt,
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": name,
                        "schema": schema,
                        "strict": True,
                    }
                },
            },
            timeout=self._config.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        return json.loads(payload["output"][0]["content"][0]["text"])

    async def extract_review_mentions(
        self, text: str, allowed_attribute_slugs: list[str]
    ) -> list[AttributeMentionOutput]:
        """Extract only explicitly evidenced attribute mentions."""
        adapter = TypeAdapter(list[AttributeMentionOutput])
        schema = adapter.json_schema()
        raw = await self._request(
            "review_attribute_mentions",
            schema,
            "Extract explicit hotel attribute sentiments. Allowed slugs: "
            f"{allowed_attribute_slugs}. Review: {text}",
        )
        return adapter.validate_python(raw)

    async def interpret_onboarding(
        self, answers: list[dict[str, object]], allowed_attribute_slugs: list[str]
    ) -> list[PreferenceOutput]:
        """Convert questionnaire answers to bounded importance weights."""
        adapter = TypeAdapter(list[PreferenceOutput])
        raw = await self._request(
            "onboarding_preferences",
            adapter.json_schema(),
            f"Allowed slugs: {allowed_attribute_slugs}. Answers: {json.dumps(answers)}",
        )
        return adapter.validate_python(raw)

    async def extract_chat_intent(self, message: str) -> ChatIntentOutput:
        """Extract one supported intent and explicit entities."""
        raw = await self._request(
            "hotel_chat_intent",
            ChatIntentOutput.model_json_schema(),
            f"Extract a supported hotel assistant intent from: {message}",
        )
        return ChatIntentOutput.model_validate(raw)
