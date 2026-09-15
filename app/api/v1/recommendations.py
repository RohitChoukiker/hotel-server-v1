"""Live deterministic recommendation HTTP endpoints."""

import uuid

from fastapi import APIRouter, Depends, Request

from app.api.responses import success
from app.dependencies import CacheDep, CurrentUserDep, SessionDep, rate_limit
from app.dto.common import SuccessResponse
from app.dto.recommendations import (
    RecommendationDetail,
    RecommendationRequest,
    RecommendationRunRead,
)
from app.services.recommendations import RecommendationService

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


@router.post(
    "",
    response_model=SuccessResponse[RecommendationRunRead],
    dependencies=[Depends(rate_limit("recommendation"))],
)
async def recommend(
    payload: RecommendationRequest,
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
    cache: CacheDep,
) -> SuccessResponse[RecommendationRunRead]:
    """Rank destination hotels live from precomputed scores."""
    return success(
        request,
        await RecommendationService(session, cache).generate(user.id, payload),
    )


@router.get("/{run_id}", response_model=SuccessResponse[RecommendationRunRead])
async def recommendation_run(
    run_id: uuid.UUID,
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
    cache: CacheDep,
) -> SuccessResponse[RecommendationRunRead]:
    """Return an owned recommendation run."""
    return success(
        request, await RecommendationService(session, cache).get(user.id, run_id)
    )


@router.get(
    "/{run_id}/hotels/{hotel_id}", response_model=SuccessResponse[RecommendationDetail]
)
async def recommendation_detail(
    run_id: uuid.UUID,
    hotel_id: uuid.UUID,
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
    cache: CacheDep,
) -> SuccessResponse[RecommendationDetail]:
    """Return one hotel explanation from an owned run."""
    return success(
        request,
        await RecommendationService(session, cache).detail(user.id, run_id, hotel_id),
    )
