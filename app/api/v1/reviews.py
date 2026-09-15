"""Individual review HTTP endpoints."""

import uuid

from fastapi import APIRouter, Request

from app.api.responses import success
from app.dependencies import SessionDep
from app.dto.common import SuccessResponse
from app.dto.reviews import ReviewImageRead, ReviewRead
from app.services.catalog import ReviewService

router = APIRouter(prefix="/reviews", tags=["reviews"])


@router.get("/{review_id}", response_model=SuccessResponse[ReviewRead])
async def review(
    review_id: uuid.UUID, request: Request, session: SessionDep
) -> SuccessResponse[ReviewRead]:
    """Return one normalized review."""
    return success(request, await ReviewService(session).get(review_id))


@router.get("/{review_id}/images", response_model=SuccessResponse[list[ReviewImageRead]])
async def review_images(
    review_id: uuid.UUID, request: Request, session: SessionDep
) -> SuccessResponse[list[ReviewImageRead]]:
    """Return normalized review images."""
    return success(request, await ReviewService(session).images(review_id))

