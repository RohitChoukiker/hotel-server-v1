"""Production operations, data-quality, and account administration."""

import re
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.cache import Cache
from app.common.logging import get_logger
from app.common.pagination import InvalidCursorError, cursor_uuid, decode_cursor, encode_cursor
from app.dto.admin import (
    AlgorithmVersionRead,
    DashboardRead,
    DataQualityIssueRead,
    ImportJobRead,
    SystemSettingRead,
)
from app.dto.common import Pagination
from app.dto.locations import CityRead, CityWrite
from app.dto.users import UserProfile
from app.enums import ScopeType
from app.exceptions import ImportValidationError, NotFoundError
from app.repositories.locations import LocationRepository
from app.repositories.operations import (
    AdminRepository,
    AuditRepository,
    DataQualityRepository,
    ImportJobRepository,
)
from app.repositories.users import UserRepository


class AdminService:
    """Own operational read models."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._admin = AdminRepository(session)
        self._jobs = ImportJobRepository(session)
        self._quality = DataQualityRepository(session)
        self._users = UserRepository(session)
        self._audit = AuditRepository(session)
        self._locations = LocationRepository(session)

    async def audit_action(
        self,
        actor_id: uuid.UUID,
        action: str,
        resource_type: str,
        resource_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        """Append and commit a privileged operational action."""
        await self._audit.record(
            actor_id, action, resource_type, resource_id, metadata or {}
        )
        await self._session.commit()

    @staticmethod
    def validate_normalization_scope(
        scope: ScopeType,
        country_id: uuid.UUID | None,
        region_id: uuid.UUID | None,
        city_id: uuid.UUID | None,
        hotel_ids: list[uuid.UUID],
    ) -> None:
        """Reject normalization jobs that cannot identify their population."""
        valid = {
            ScopeType.GLOBAL: True,
            ScopeType.COUNTRY: country_id is not None,
            ScopeType.REGION: region_id is not None,
            ScopeType.CITY: city_id is not None,
            ScopeType.SEARCH_SET: bool(hotel_ids),
        }[scope]
        if not valid:
            raise ImportValidationError(
                "Normalization scope is missing its required identifier"
            )
        if len(hotel_ids) > 2_000:
            raise ImportValidationError("Search-set normalization is limited to 2000 hotels")

    async def dashboard(self) -> DashboardRead:
        """Return operational aggregate counters."""
        return DashboardRead.model_validate(await self._admin.dashboard())

    async def import_job(self, job_id: uuid.UUID) -> ImportJobRead:
        """Return one import job."""
        job = await self._jobs.get(job_id)
        if job is None:
            raise NotFoundError("Import job not found")
        return ImportJobRead.model_validate(job)

    async def imports(
        self, limit: int, cursor: str | None
    ) -> tuple[list[ImportJobRead], Pagination]:
        """Return a keyset page of imports."""
        values = decode_cursor(cursor)
        cursor_id = cursor_uuid(values, "id")
        if cursor and cursor_id is None:
            raise InvalidCursorError()
        jobs = await self._jobs.list(limit, cursor_id)
        has_more = len(jobs) > limit
        selected = jobs[:limit]
        next_cursor = encode_cursor({"id": selected[-1].id}) if has_more and selected else None
        return (
            [ImportJobRead.model_validate(item) for item in selected],
            Pagination(next_cursor=next_cursor, has_more=has_more, limit=limit),
        )

    async def quality_issues(
        self, issue_type: str | None, unresolved_only: bool, limit: int
    ) -> list[DataQualityIssueRead]:
        """Return persisted quality issues."""
        return [
            DataQualityIssueRead.model_validate(item)
            for item in await self._quality.list(issue_type, unresolved_only, limit)
        ]

    async def users(
        self, limit: int, cursor: str | None
    ) -> tuple[list[UserProfile], Pagination]:
        """Return a keyset page of users."""
        values = decode_cursor(cursor)
        cursor_id = cursor_uuid(values, "id")
        if cursor and cursor_id is None:
            raise InvalidCursorError()
        users = await self._users.list_users(limit, cursor_id)
        has_more = len(users) > limit
        selected = users[:limit]
        next_cursor = encode_cursor({"id": selected[-1].id}) if has_more and selected else None
        return (
            [UserProfile.model_validate(item) for item in selected],
            Pagination(next_cursor=next_cursor, has_more=has_more, limit=limit),
        )

    async def user(self, user_id: uuid.UUID) -> UserProfile:
        """Return one user for administration."""
        user = await self._users.get_by_id(user_id)
        if user is None:
            raise NotFoundError("User not found")
        return UserProfile.model_validate(user)

    async def algorithms(self) -> list[AlgorithmVersionRead]:
        """List immutable scoring algorithm versions."""
        return [
            AlgorithmVersionRead.model_validate(item)
            for item in await self._admin.algorithm_versions()
        ]

    async def settings(self) -> list[SystemSettingRead]:
        """List administrator-managed non-secret settings."""
        return [
            SystemSettingRead.model_validate(item)
            for item in await self._admin.settings()
        ]

    async def update_setting(
        self, actor_id: uuid.UUID, key: str, value: dict[str, object]
    ) -> SystemSettingRead:
        """Persist a non-secret setting and audit the administrator action."""
        normalized_key = key.strip().casefold()
        if not re.fullmatch(r"[a-z][a-z0-9_.-]{1,119}", normalized_key):
            raise ImportValidationError("Invalid setting key")
        forbidden_fragments = {"secret", "password", "token", "api_key", "dsn"}
        nested_keys = self._nested_keys(value)
        if any(
            fragment in candidate
            for candidate in {normalized_key, *nested_keys}
            for fragment in forbidden_fragments
        ):
            raise ImportValidationError("Secrets must be stored in Secret Manager")
        setting = await self._admin.upsert_setting(
            normalized_key, value, actor_id
        )
        await self._audit.record(
            actor_id,
            "settings_changed",
            "system_setting",
            normalized_key,
            {"key": normalized_key},
        )
        await self._session.commit()
        return SystemSettingRead.model_validate(setting)

    async def upsert_city(
        self,
        actor_id: uuid.UUID,
        payload: CityWrite,
        cache: Cache | None = None,
    ) -> CityRead:
        """Create or update a canonical city for deterministic imports."""
        if await self._locations.get_region(payload.region_id) is None:
            raise ImportValidationError("Region not found")
        city = await self._locations.upsert_city(
            payload.region_id,
            payload.name,
            payload.latitude,
            payload.longitude,
            payload.timezone,
        )
        await self._audit.record(
            actor_id,
            "city_upserted",
            "city",
            str(city.id),
            {"region_id": str(payload.region_id), "name": payload.name},
        )
        await self._session.commit()
        if cache is not None:
            key = f"locations:region:{payload.region_id}:cities:v1"
            try:
                await cache.delete(key)
            except Exception:
                get_logger().warning("cache_delete_failed", key=key)
        return CityRead.model_validate(city)

    @classmethod
    def _nested_keys(cls, value: object) -> set[str]:
        if isinstance(value, dict):
            keys = {str(key).casefold() for key in value}
            for item in value.values():
                keys.update(cls._nested_keys(item))
            return keys
        if isinstance(value, list):
            keys: set[str] = set()
            for item in value:
                keys.update(cls._nested_keys(item))
            return keys
        return set()
