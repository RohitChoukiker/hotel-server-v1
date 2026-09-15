"""Conversation ownership and message persistence service."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.pagination import (
    InvalidCursorError,
    cursor_datetime,
    cursor_uuid,
    decode_cursor,
    encode_cursor,
)
from app.dto.chat import ChatMessageRead, ConversationRead
from app.dto.common import Pagination
from app.exceptions import NotFoundError
from app.models import ChatConversation
from app.repositories.chat import ChatRepository


class ChatService:
    """Own visible conversation storage; never persist hidden reasoning."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._chat = ChatRepository(session)

    async def conversation(
        self,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        limit: int = 100,
        cursor: str | None = None,
    ) -> ConversationRead:
        """Return an owned conversation and visible messages."""
        conversation = await self._owned(user_id, conversation_id)
        values = decode_cursor(cursor)
        cursor_created = cursor_datetime(values, "created_at")
        cursor_id = cursor_uuid(values, "id")
        if cursor and (cursor_created is None or cursor_id is None):
            raise InvalidCursorError()
        rows = await self._chat.messages(
            conversation.id, limit, cursor_created, cursor_id
        )
        has_more = len(rows) > limit
        selected = rows[:limit]
        messages = [ChatMessageRead.model_validate(item) for item in selected]
        next_cursor = None
        if has_more and selected:
            next_cursor = encode_cursor(
                {
                    "created_at": selected[-1].created_at,
                    "id": selected[-1].id,
                }
            )
        return ConversationRead.model_validate(conversation).model_copy(
            update={
                "messages": messages,
                "message_pagination": Pagination(
                    next_cursor=next_cursor,
                    has_more=has_more,
                    limit=limit,
                ),
            }
        )

    async def conversations(self, user_id: uuid.UUID) -> list[ConversationRead]:
        """List owned conversations without expanding message bodies."""
        return [
            ConversationRead.model_validate(item)
            for item in await self._chat.list_owned(user_id, 100)
        ]

    async def _owned(
        self, user_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> ChatConversation:
        conversation = await self._chat.get_owned(conversation_id, user_id)
        if conversation is None:
            raise NotFoundError("Conversation not found")
        return conversation
