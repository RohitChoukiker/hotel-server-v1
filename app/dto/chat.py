"""Conversational hotel assistant DTOs."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import Field

from app.dto.common import DTO, Pagination
from app.enums import ChatIntent


class ChatRequest(DTO):
    """User chat turn."""

    message: str = Field(min_length=1, max_length=4000)
    conversation_id: uuid.UUID | None = None
    trip_id: uuid.UUID | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class ChatResponse(DTO):
    """Structured assistant response."""

    conversation_id: uuid.UUID
    message: str
    intent: ChatIntent
    data: dict[str, Any]


class ChatMessageRead(DTO):
    """Visible message history entry."""

    id: uuid.UUID
    role: str
    message: str
    structured_intent: dict[str, Any] | None
    created_at: datetime


class ConversationRead(DTO):
    """Conversation history response."""

    id: uuid.UUID
    trip_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime
    messages: list[ChatMessageRead] = Field(default_factory=list)
    message_pagination: Pagination | None = None
