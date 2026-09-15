"""Version 1 API router composition."""

from fastapi import APIRouter

from app.api.v1 import (
    admin,
    auth,
    chat,
    hotels,
    locations,
    onboarding,
    preferences,
    recommendations,
    reviews,
    trips,
    users,
)

router = APIRouter()
router.include_router(auth.router)
router.include_router(users.router)
router.include_router(onboarding.router)
router.include_router(preferences.router)
router.include_router(trips.router)
router.include_router(locations.router)
router.include_router(hotels.router)
router.include_router(reviews.router)
router.include_router(recommendations.router)
router.include_router(chat.router)
router.include_router(admin.router)

