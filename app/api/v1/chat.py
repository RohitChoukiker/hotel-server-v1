"""Conversational hotel assistant HTTP endpoints."""

import uuid

from fastapi import APIRouter, Depends, Query, Request

from app.api.responses import success
from app.dependencies import CurrentUserDep, InterpreterDep, SessionDep, rate_limit
from app.dto.chat import ChatRequest, ChatResponse, ConversationRead
from app.dto.common import SuccessResponse
from app.orchestrators.chat import ChatOrchestrator
from app.services.chat import ChatService

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post(
    "",
    response_model=SuccessResponse[ChatResponse],
    dependencies=[Depends(rate_limit("chat"))],
)
async def chat(
    payload: ChatRequest,
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
    interpreter: InterpreterDep,
) -> SuccessResponse[ChatResponse]:
    """Process one hotel assistant turn."""
    return success(
        request,
        await ChatOrchestrator(session, interpreter).handle(user.id, payload),
    )


@router.get("/conversations", response_model=SuccessResponse[list[ConversationRead]])
async def conversations(
    request: Request, user: CurrentUserDep, session: SessionDep
) -> SuccessResponse[list[ConversationRead]]:
    """List the user's conversations."""
    return success(request, await ChatService(session).conversations(user.id))


@router.get(
    "/conversations/{conversation_id}", response_model=SuccessResponse[ConversationRead]
)
async def conversation(
    conversation_id: uuid.UUID,
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
    limit: int = Query(default=100, ge=1, le=500),
    cursor: str | None = None,
) -> SuccessResponse[ConversationRead]:
    """Return one owned conversation."""
    return success(
        request,
        await ChatService(session).conversation(
            user.id, conversation_id, limit, cursor
        ),
    )
