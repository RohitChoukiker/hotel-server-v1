"""Focused coverage for explicit onboarding skips."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.exceptions import ImportValidationError
from app.services.onboarding import OnboardingService


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


class FakeUserRepository:
    def __init__(self, user: SimpleNamespace) -> None:
        self.user = user

    async def get_by_id(self, user_id: uuid.UUID, for_update: bool = False):
        assert user_id == self.user.id
        assert for_update
        return self.user


class FakeOnboardingRepository:
    def __init__(self, active=None, skipped=None) -> None:
        self.active = active
        self.skipped = skipped
        self.locked = False

    async def active_session(self, user_id: uuid.UUID, for_update: bool = False):
        assert for_update
        self.locked = True
        return self.active

    async def latest_skipped_session(self, user_id: uuid.UUID):
        return self.skipped


def _user(*, completed: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        onboarding_completed=completed,
        onboarding_skipped_at=None,
    )


@pytest.mark.asyncio
async def test_fresh_skip_is_explicit_and_idempotent_without_a_provider_call(monkeypatch) -> None:
    user = _user()
    repository = FakeUserRepository(user)
    monkeypatch.setattr("app.services.onboarding.UserRepository", lambda _: repository)
    onboarding = FakeOnboardingRepository()
    service = OnboardingService(FakeSession(), AsyncMock())
    service._onboarding = onboarding

    first = await service.skip(user.id)
    recorded_at = user.onboarding_skipped_at
    second = await service.skip(user.id)

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.skipped is True
    assert first.completed is False
    assert first.session_id is None
    assert first.status == "SKIPPED"
    assert isinstance(recorded_at, datetime) and recorded_at.tzinfo == UTC


@pytest.mark.asyncio
async def test_in_progress_skip_preserves_session_answers_and_never_calls_provider(
    monkeypatch,
) -> None:
    user = _user()
    active = SimpleNamespace(
        id=uuid.uuid4(),
        status="IN_PROGRESS",
        generation_state="GENERATING",
        completion_reason=None,
        answers=[{"value": "Goa"}],
    )
    repository = FakeUserRepository(user)
    monkeypatch.setattr("app.services.onboarding.UserRepository", lambda _: repository)
    service = OnboardingService(FakeSession(), AsyncMock())
    service._onboarding = FakeOnboardingRepository(active=active)

    result = await service.skip(user.id)

    assert result.session_id == active.id
    assert active.status == "SKIPPED"
    assert active.generation_state == "IDLE"
    assert active.completion_reason == "USER_SKIPPED"
    assert active.answers == [{"value": "Goa"}]
    service._adaptive_provider.generate_next_question.assert_not_awaited()


@pytest.mark.asyncio
async def test_completed_onboarding_cannot_be_downgraded(monkeypatch) -> None:
    user = _user(completed=True)
    repository = FakeUserRepository(user)
    monkeypatch.setattr("app.services.onboarding.UserRepository", lambda _: repository)
    onboarding = FakeOnboardingRepository()
    service = OnboardingService(FakeSession())
    service._onboarding = onboarding

    with pytest.raises(ImportValidationError, match="already completed"):
        await service.skip(user.id)

    assert onboarding.locked is False
