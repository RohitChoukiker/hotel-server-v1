"""Claude Messages API adapter for strict adaptive-onboarding tool output."""

import asyncio
import re
from copy import deepcopy
from typing import Any

import httpx
from pydantic import ValidationError

from app.common.logging import get_logger
from app.config import LLMConfig
from app.dto.onboarding import NextQuestionDecision
from app.integrations.llm.adaptive import (
    AdaptiveAuthenticationError,
    AdaptiveProviderError,
    AdaptiveRateLimitError,
    AdaptiveTimeoutError,
    context_json,
)

_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(?:authorization|x-api-key|api[_ -]?key|access[_ -]?token|"
    r"refresh[_ -]?token|secret|password)\b"
    r"\s*[:=]\s*[^\s,;]+"
)
_BEARER_TOKEN = re.compile(r"(?i)\bbearer\s+[^\s,;]+")
_POTENTIALLY_SENSITIVE_PROVIDER_VALUE = re.compile(
    r"(?i)\b(?:answer|input|prompt|content|user(?:_answer)?|message)\b"
    r"\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_ALLOWED_INFERRED_ROUTING_KEYS = frozenset(
    {
        "destination_branch",
        "destination_detail",
        "couple_style",
        "family_needs",
        "friends_vibe",
    }
)
_KNOWN_NON_ROUTING_KEYS = frozenset(
    {
        "price_value",
        "travel_pace",
        "food_preference",
        "amenities",
        "room_quality",
        "privacy",
        "activities",
    }
)
_ANTHROPIC_UNSUPPORTED_SCHEMA_KEYWORDS = frozenset(
    {
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minLength",
        "maxLength",
        "pattern",
        "maxItems",
        "minItems",
        "minProperties",
        "maxProperties",
    }
)
_ANTHROPIC_SCHEMA_DIAGNOSTIC_KEYWORDS = (
    _ANTHROPIC_UNSUPPORTED_SCHEMA_KEYWORDS
    | {
        "minItems",
        "anyOf",
        "oneOf",
        "allOf",
        "$ref",
        "$defs",
        "definitions",
        "additionalProperties",
        "format",
        "const",
    }
)
_ANTHROPIC_SCHEMA_KEYWORD_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    + "|".join(sorted(_ANTHROPIC_SCHEMA_DIAGNOSTIC_KEYWORDS, key=len, reverse=True))
    + r")(?![A-Za-z0-9_])"
)
_SAFE_ERROR_TYPE_PATTERN = re.compile(r"[^A-Za-z0-9_.-]")


def _to_anthropic_strict_schema(
    schema: dict[str, Any],
    *,
    required_root_fields: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Adapt a Pydantic schema to Anthropic's strict-output JSON Schema subset."""
    transformed = deepcopy(schema)

    def transform(node: Any) -> Any:
        if isinstance(node, dict):
            if node.get("type") == "object":
                node["additionalProperties"] = False
            for key in list(node):
                if key in _ANTHROPIC_UNSUPPORTED_SCHEMA_KEYWORDS:
                    del node[key]
                    continue
                node[key] = transform(node[key])
            return node
        if isinstance(node, list):
            return [transform(item) for item in node]
        return node

    transform(transformed)
    if required_root_fields:
        required = transformed.get("required")
        existing = required if isinstance(required, list) else []
        transformed["required"] = [
            *existing,
            *(field for field in required_root_fields if field not in existing),
        ]
    return transformed


def _adaptive_question_tool_schema(*, require_choice_options: bool = False) -> dict[str, Any]:
    """Build Claude's compact strict next-question schema without changing DTO semantics."""
    schema = _to_anthropic_strict_schema(
        NextQuestionDecision.model_json_schema(),
        required_root_fields=("question",),
    )
    question = schema.get("properties", {}).get("question")
    if isinstance(question, dict):
        question.pop("default", None)
    if not require_choice_options:
        return schema
    definition = schema.get("$defs", {}).get("AdaptiveQuestion")
    if not isinstance(definition, dict):
        return schema
    properties = definition.get("properties")
    required = definition.get("required")
    if not isinstance(properties, dict) or not isinstance(required, list):
        return schema
    properties["options"] = {
        "type": "array",
        "items": {"$ref": "#/$defs/AdaptiveQuestionOption"},
    }
    if "options" not in required:
        required.append("options")
    return schema


class AnthropicAdaptiveOnboardingProvider:
    """Use Claude tool use as a strict JSON-schema response channel."""

    provider_name = "anthropic"

    def __init__(self, config: LLMConfig, client: httpx.AsyncClient) -> None:
        if config.api_key is None or not config.api_key.get_secret_value().strip():
            raise ValueError("LLM API key is required for the Anthropic provider")
        self._config = config
        self._client = client
        self.model = config.model
        self.prompt_version = config.prompt_version

    async def generate_next_question(self, context: dict[str, Any]) -> NextQuestionDecision:
        """Ask Claude for exactly one bounded next-question decision."""
        system = self._system_prompt("next question")
        user = (
            "Generate the next onboarding decision from this JSON context. "
            "Return the required tool and nothing else.\n<context>"
            + context_json(context)
            + "</context>"
        )
        return await self._validated(
            "adaptive_onboarding_question",
            NextQuestionDecision,
            system,
            user,
        )

    async def _validated(
        self,
        name: str,
        model_type: type[NextQuestionDecision],
        system: str,
        user: str,
    ) -> Any:
        last_error: Exception | None = None
        correction: str | None = None
        for attempt in range(self._config.max_retries + 1):
            try:
                request_user = user
                if correction:
                    request_user = f"{user}\n\n{correction}"
                schema = (
                    _adaptive_question_tool_schema(
                        require_choice_options=bool(
                            correction
                            and "question.options: include 2-5 option objects" in correction
                        )
                    )
                    if name == "adaptive_onboarding_question"
                    else _to_anthropic_strict_schema(model_type.model_json_schema())
                )
                tool_input = await self._request_tool(name, schema, system, request_user)
                get_logger().info(
                    "adaptive_claude_tool_keys_received",
                    tool_name=name,
                    keys=sorted(tool_input.keys()),
                )
                raw = tool_input
                raw = self._sanitize_inferred_signals(raw, name, attempt + 1)
                raw = self._normalize_internal_metadata(raw, name)
                return model_type.model_validate(raw)
            except (ValidationError, AdaptiveProviderError) as exc:
                last_error = exc
                validation_errors = self._validation_errors(exc)
                get_logger().warning(
                    "adaptive_claude_validation_failed",
                    tool_name=name,
                    attempt=attempt + 1,
                    model=self.model,
                    error_type=type(exc).__name__,
                    error_message=self._safe_error_message(exc),
                    validation_errors=validation_errors,
                )
                correction = self._correction_instruction(validation_errors, name)
                if attempt >= self._config.max_retries:
                    break
                await asyncio.sleep(min(2**attempt, 4))
        assert last_error is not None
        final_fields: dict[str, Any] = {
            "tool_name": name,
            "model": self.model,
            "attempts": attempt + 1,
            "final_error_type": type(last_error).__name__,
            "final_error_message": self._safe_error_message(last_error),
        }
        validation_errors = self._validation_errors(last_error)
        if validation_errors is not None:
            final_fields["validation_errors"] = validation_errors
        get_logger().error("adaptive_claude_validation_exhausted", **final_fields)
        if isinstance(last_error, AdaptiveProviderError) and str(last_error).startswith(
            "Claude schema request rejected:"
        ):
            raise last_error
        raise AdaptiveProviderError(f"Claude returned no valid {name} output") from last_error

    def _normalize_internal_metadata(self, raw: dict[str, Any], tool_name: str) -> dict[str, Any]:
        """Bound verbose non-semantic decision metadata before DTO validation."""
        if tool_name != "adaptive_onboarding_question":
            return raw
        normalized = dict(raw)
        limits = {"reasoning_summary": 300, "completion_reason": 80}
        for field, limit in limits.items():
            value = raw.get(field)
            if not isinstance(value, str) or len(value) <= limit:
                continue
            normalized[field] = value[:limit].rstrip()
            get_logger().info(
                "adaptive_claude_metadata_normalized",
                field=field,
                original_length=len(value),
                final_length=len(normalized[field]),
            )
        return normalized

    def _sanitize_inferred_signals(
        self, raw: dict[str, Any], tool_name: str, attempt: int
    ) -> dict[str, Any]:
        """Drop unsupported routing metadata without weakening the DTO contract."""
        if tool_name != "adaptive_onboarding_question":
            return raw
        signals = raw.get("inferred_signals")
        if not isinstance(signals, list):
            return raw
        retained: list[Any] = []
        normalized = dict(raw)
        changed = False
        for signal in signals:
            if not isinstance(signal, dict):
                retained.append(signal)
                continue
            routing_key = signal.get("routing_key")
            if isinstance(routing_key, str) and routing_key in _ALLOWED_INFERRED_ROUTING_KEYS:
                retained.append(signal)
                continue
            changed = True
            safe_key = (
                routing_key
                if isinstance(routing_key, str) and routing_key in _KNOWN_NON_ROUTING_KEYS
                else "[unsupported]"
            )
            get_logger().warning(
                "adaptive_claude_inferred_signal_dropped",
                routing_key=safe_key,
                tool_name=tool_name,
                attempt=attempt,
                model=self.model,
            )
        if changed:
            normalized["inferred_signals"] = retained
        return normalized

    @staticmethod
    def _correction_instruction(
        validation_errors: list[dict[str, Any]] | None,
        tool_name: str | None = None,
    ) -> str | None:
        """Build a small field-only correction without echoing tool input."""
        if not validation_errors:
            return None
        lines: list[str] = []
        for error in validation_errors[:6]:
            location = ".".join(error.get("location", [])) or "output"
            message = str(error.get("message", "correct the schema value"))
            if (
                tool_name == "adaptive_onboarding_question"
                and location == "question"
                and "option" in message.casefold()
            ):
                location = "question.options"
            lines.append(f"- {location}: {message[:160]}")
            if (
                tool_name == "adaptive_onboarding_question"
                and "single_choice requires 2-5 options" in message
            ):
                lines.extend(
                    [
                        "- question.answer_type: use `single_choice`.",
                        "- question.options: include 2-5 option objects.",
                        "- question.options: every option requires `value` and `label`.",
                        "- question: return the complete question object again.",
                    ]
                )
            if (
                tool_name == "adaptive_onboarding_question"
                and "Incomplete questionnaires require one question" in message
            ):
                lines.extend(
                    [
                        "- questionnaire_complete: keep false only when returning a question.",
                        "- question: this required key must contain the complete question object.",
                        "- question: never omit this key when questionnaire_complete is false.",
                    ]
                )
        return (
            "Your previous structured output was invalid. Correct these fields:\n"
            + "\n".join(lines)
            + "\nReturn the tool again with the corrected structure."
        )

    def _safe_error_message(self, exc: Exception) -> str:
        """Return a bounded diagnostic with configured credentials redacted."""
        if isinstance(exc, ValidationError):
            validation_errors = self._validation_errors(exc) or []
            return "; ".join(
                f"{'.'.join(error['location']) or 'output'}: {error['message']}"
                for error in validation_errors
            )[:1000]
        message = str(exc)
        api_key = self._config.api_key.get_secret_value()
        if api_key:
            message = message.replace(api_key, "[REDACTED]")
        message = _SENSITIVE_ASSIGNMENT.sub("[REDACTED]", message)
        return _BEARER_TOKEN.sub("Bearer [REDACTED]", message)

    def _validation_errors(self, exc: Exception) -> list[dict[str, Any]] | None:
        """Keep validation diagnostics to locations, messages, and error codes only."""
        if not isinstance(exc, ValidationError):
            return None
        return [
            {
                "location": [str(part) for part in item.get("loc", ())],
                "message": _SENSITIVE_ASSIGNMENT.sub("[REDACTED]", str(item.get("msg", ""))),
                "type": str(item.get("type", "")),
            }
            for item in exc.errors(include_input=False)
        ]

    async def _request_tool(
        self, name: str, schema: dict[str, Any], system: str, user: str
    ) -> dict[str, Any]:
        configured_url = str(self._config.base_url or "https://api.anthropic.com").rstrip("/")
        base_url = configured_url if configured_url.endswith("/v1") else f"{configured_url}/v1"
        try:
            response = await self._client.post(
                f"{base_url}/messages",
                headers={
                    "x-api-key": self._config.api_key.get_secret_value(),
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": self._config.model,
                    "max_tokens": 1200,
                    "system": system,
                    "messages": [{"role": "user", "content": user}],
                    "tools": [
                        {
                            "name": name,
                            "description": "Return only the structured onboarding object.",
                            "strict": True,
                            "input_schema": schema,
                        }
                    ],
                    "tool_choice": {"type": "tool", "name": name},
                },
                timeout=self._config.timeout_s,
            )
        except httpx.TimeoutException as exc:
            raise AdaptiveTimeoutError("Claude request timed out") from exc
        except httpx.HTTPError as exc:
            raise AdaptiveProviderError("Claude request failed") from exc
        if response.status_code in {401, 403}:
            raise AdaptiveAuthenticationError("Claude authentication failed")
        if response.status_code == 429:
            raise AdaptiveRateLimitError("Claude rate limit reached")
        if response.status_code == 400:
            raise self._safe_schema_request_error(response)
        if response.status_code >= 500:
            raise AdaptiveProviderError(
                f"Claude request temporarily failed: {response.status_code}"
            )
        try:
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AdaptiveProviderError("Claude request failed") from exc
        for block in payload.get("content", []):
            if block.get("type") == "tool_use" and isinstance(block.get("input"), dict):
                return block["input"]
        raise AdaptiveProviderError("Claude response did not contain the requested structured tool")

    @staticmethod
    def _safe_schema_request_error(response: httpx.Response) -> AdaptiveProviderError:
        """Return bounded schema diagnostics without echoing request content."""
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        provider_error = payload.get("error") if isinstance(payload, dict) else None
        if not isinstance(provider_error, dict):
            provider_error = {}
        error_type = provider_error.get("type")
        if not isinstance(error_type, str) or not error_type:
            error_type = "invalid_request_error"
        error_type = _SAFE_ERROR_TYPE_PATTERN.sub("", error_type)[:80] or "provider_error"
        message = provider_error.get("message")
        message_text = message if isinstance(message, str) else ""
        keywords = sorted(
            set(_ANTHROPIC_SCHEMA_KEYWORD_PATTERN.findall(message_text)),
            key=str.casefold,
        )
        if keywords:
            detail = f"{error_type}: rejected schema constructs: {', '.join(keywords)}"
        elif message_text:
            safe_message = _SENSITIVE_ASSIGNMENT.sub("[REDACTED]", message_text)
            safe_message = _BEARER_TOKEN.sub("Bearer [REDACTED]", safe_message)
            safe_message = _POTENTIALLY_SENSITIVE_PROVIDER_VALUE.sub(
                "[REDACTED]", safe_message
            )
            safe_message = " ".join(safe_message.split())[:500]
            detail = f"{error_type}: {safe_message}" if safe_message else error_type
        else:
            detail = f"{error_type}: provider rejected the schema"
        return AdaptiveProviderError(f"Claude schema request rejected: {detail}")

    @staticmethod
    def _system_prompt(task: str) -> str:
        return (
            "You are the Hotel Recommendation backend's adaptive onboarding specialist. "
            f"Your task is to generate a {task}. Ask ONE question only when incomplete. "
            "The canonical flow has four layers: context, with deterministic Q1 already "
            "answered and travel group normally next; universal core, covering price/value, "
            "holiday pace, and food preference; exactly one destination branch; and at most one "
            "group branch. Q1 is never generated by you and must never be repeated. Normally make "
            "Q2 ask, 'Who's travelling with you?' with solo, couple, family with kids, and friends "
            "concepts. Do not ask trip purpose or property type before group context unless the "
            "conversation already makes group explicit. Use decision-oriented price/value framing "
            "rather than asking only for a budget. Preserve active/packed, mixed, and "
            "restful/relaxed pace intent, plus eating out, hotel dining, and mix food preferences, "
            "while wording questions naturally. "
            "Every generated question must include exactly one canonical question_key; never omit "
            "it and never invent arbitrary keys. Use travel_group for the travel group question, "
            "price_value for price/value, travel_pace for pace, food_preference for food, "
            "destination_branch for branch selection, city_focus or city_location for city hotel "
            "matching, the relevant beach_/mountain_/rural_ key for those branches, "
            "destination_detail only for a generic destination matching question, and "
            "couple_style, family_needs, or friends_vibe for the applicable group branch. "
            "semantic_dimensions may add descriptive detail but never replace question_key. "
            "For the destination branch, choose only one of beach/coast, mountains/hills, "
            "city/town, or countryside/rural. Infer it from the actual destination and resolved "
            "metadata when reasonably clear: Goa may support beach/coast, Manali mountains/hills, "
            "Jaipur city/town, and Aizawl may be hills or city context. These are hypotheses, not "
            "facts. Do not ask destination type separately unless branch choice is genuinely "
            "ambiguous; then ask one useful clarification. Never activate multiple branches. Use "
            "only relevant branch intent: beach activity style and proximity; mountain "
            "outdoors/scenery/winter sports only when relevant plus recovery style; city "
            "culture/food/nightlife plus urban location; or rural isolation plus stay style. "
            "A destination_branch question is routing clarification only; "
            "it does not satisfy destination hotel matching. A destination_detail, "
            "destination_preference, mountain_outdoors, mountain_scenery, mountain_recovery, "
            "city_focus, city_location, beach_activity, beach_proximity, rural_isolation, or "
            "rural_stay_style question is a destination matching signal. If a balanced answer "
            "leaves the branch ambiguous, choose the most useful single branch for the next "
            "matching question. "
            "Ask at most one group branch question: solo gets none; family covers children needs; "
            "couple covers romance/adventure/decompression; friends covers party, active, or relax "
            "together. inferred_signals is ONLY for high-confidence routing/completion-gate "
            "signals. Its routing_key must be exactly one of destination_branch, "
            "destination_detail, couple_style, family_needs, or friends_vibe. Never emit "
            "price_value, travel_pace, food_preference, amenities, room_quality, privacy, "
            "activities, or any scoring/profile preference attribute there; those belong in "
            "explicit answers and deterministic completion mapping. Valid Aizawl examples are "
            "destination_branch=city_town, destination_detail=peaceful_retreat, and "
            "couple_style=decompression. Include value, confidence, and evidence question IDs. "
            "For a couple, an inferred couple_style value must be romance, adventure, or "
            "decompression/relaxation. Skip anything already clearly implied. Never ask a "
            "question whose canonical question_key is already answered, even when the wording "
            "or semantic_dimensions are different; price_value, travel_pace, food_preference, "
            "travel_group, the applicable destination matching key, and the applicable group "
            "branch each count as one covered topic. If corrective feedback says a topic is "
            "already answered, ask only for a missing canonical signal or declare completion "
            "when the gate is ready. Do not repeat semantic dimensions "
            "unless clarification materially improves hotel matching. If answers conflict "
            "materially, ask one clarification rather than silently averaging them. Set "
            "questionnaire_complete=true only when completion_gate.ready is true. Always emit "
            "the `question` key. When questionnaire_complete is false, `question` "
            "must contain exactly one valid question. When questionnaire_complete is true, "
            "`question` must be null. The gate requires destination context, travel group, "
            "universal core, one selected destination "
            "branch, a meaningful destination matching signal, and the applicable group signal "
            "for non-solo travel. A routing clarification alone never makes the gate ready. "
            "Target 7-8 "
            "total questions, "
            "never force all questions, and never exceed 8 except one additional clarification for "
            "material ambiguity or contradiction. Use the supplied routing state and unanswered "
            "dimensions to choose the next question. Every decision must use the actual "
            "destination, "
            "resolved metadata, inferred branch/group state, all prior questions and answers, "
            "human-readable selected labels, covered dimensions, and inferred preferences. Never "
            "recommend hotels, inspect hotel names, calculate scores, rank hotels, request "
            "sensitive data, or expose chain-of-thought. reasoning_summary is a concise internal "
            "summary only: maximum 250 characters, one short sentence stating the routing decision "
            "and why. completion_reason is a short machine-friendly reason, maximum 60 characters; "
            "use examples such as 'Canonical signals complete' or "
            "'Sufficient preference coverage'. "
            "For single_choice, emit 2-5 options and null scale controls. For multi_choice, "
            "emit 2-8 options and null scale controls. For scale, emit null options plus numeric "
            "scale_min below scale_max. For text, emit null options and null scale controls. "
            "Do not provide detailed reasoning. Use supported answer types."
        )
