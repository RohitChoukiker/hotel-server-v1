"""Persisted database data-quality scan business logic."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.logging import get_logger
from app.repositories.operations import DataQualityRepository


class DataQualityService:
    """Run idempotent consistency scans outside the HTTP lifecycle."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._quality = DataQualityRepository(session)

    async def scan(self) -> int:
        """Persist new unresolved findings and return their count."""
        created = 0
        for finding in await self._quality.detect():
            issue_type = str(finding["issue_type"])
            entity_type = str(finding["entity_type"])
            source_entity_id = str(finding["source_entity_id"])
            if await self._quality.unresolved_exists(
                issue_type, entity_type, source_entity_id
            ):
                continue
            await self._quality.add(
                issue_type,
                "ERROR",
                dict(finding["details"]),
                entity_type=entity_type,
                source_entity_id=source_entity_id,
            )
            created += 1
        await self._session.commit()
        get_logger().info("data_quality_scan_completed", issues_created=created)
        return created
