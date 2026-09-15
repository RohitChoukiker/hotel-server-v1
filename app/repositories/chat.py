"""User-owned conversation persistence."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChatConversation, ChatMessage


class ChatRepository:
    """Persist visible assistant messages and structured intents."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_conversation(
        self, user_id: uuid.UUID, trip_id: uuid.UUID | None
    ) -> ChatConversation:
        """Create a user-owned conversation."""
        now = datetime.now(UTC)
        conversation = ChatConversation(user_id=user_id, trip_id=trip_id, last_message_at=now)
        self._session.add(conversation)
        await self._session.flush()
        return conversation

    async def get_owned(
        self, conversation_id: uuid.UUID, user_id: uuid.UUID
    ) -> ChatConversation | None:
        """Return a conversation only when owned by the user."""
        return await self._session.scalar(
            select(ChatConversation).where(
                ChatConversation.id == conversation_id,
                ChatConversation.user_id == user_id,
            )
        )

    async def add_message(
        self,
        conversation: ChatConversation,
        role: str,
        message: str,
        structured_intent: dict[str, object] | None = None,
    ) -> ChatMessage:
        """Append one visible message and advance conversation activity."""
        now = datetime.now(UTC)
        model = ChatMessage(
            conversation_id=conversation.id,
            role=role,
            message=message,
            structured_intent=structured_intent,
            created_at=now,
        )
        conversation.last_message_at = now
        self._session.add(model)
        await self._session.flush()
        return model

    async def list_owned(self, user_id: uuid.UUID, limit: int) -> list[ChatConversation]:
        """List most-recently active conversations."""
        return list(
            (
                await self._session.scalars(
                    select(ChatConversation)
                    .where(ChatConversation.user_id == user_id)
                    .order_by(ChatConversation.last_message_at.desc())
                    .limit(limit)
                )
            ).all()
        )

    async def messages(
        self,
        conversation_id: uuid.UUID,
        limit: int,
        cursor_created_at: datetime | None = None,
        cursor_id: uuid.UUID | None = None,
    ) -> list[ChatMessage]:
        """Return keyset-paged visible conversation history."""
        query = select(ChatMessage).where(
            ChatMessage.conversation_id == conversation_id
        )
        if cursor_created_at is not None and cursor_id is not None:
            query = query.where(
                or_(
                    ChatMessage.created_at > cursor_created_at,
                    and_(
                        ChatMessage.created_at == cursor_created_at,
                        ChatMessage.id > cursor_id,
                    ),
                )
            )
        return list(
            (
                await self._session.scalars(
                    query.order_by(ChatMessage.created_at, ChatMessage.id).limit(
                        limit + 1
                    )
                )
            ).all()
        )
