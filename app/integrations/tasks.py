"""Background task dispatch boundary."""

import uuid

from celery import Celery


class TaskDispatcher:
    """Queue named Celery tasks without coupling services to Celery internals."""

    def __init__(self, celery_app: Celery) -> None:
        self._celery = celery_app

    def import_csv(self, job_id: uuid.UUID) -> None:
        """Queue a CSV import."""
        self._celery.send_task("app.workers.tasks.import_csv", args=[str(job_id)])

    def process_reviews(self) -> None:
        """Queue review attribute extraction."""
        self._celery.send_task("app.workers.tasks.process_reviews")

    def recalculate_hotel(self, hotel_id: uuid.UUID) -> None:
        """Queue one hotel score recalculation."""
        self._celery.send_task("app.workers.tasks.recalculate_hotel", args=[str(hotel_id)])

    def normalize(
        self,
        scope: str = "GLOBAL",
        country_id: uuid.UUID | None = None,
        region_id: uuid.UUID | None = None,
        city_id: uuid.UUID | None = None,
        hotel_type: str | None = None,
        hotel_ids: list[uuid.UUID] | None = None,
    ) -> None:
        """Queue bell-curve normalization."""
        self._celery.send_task(
            "app.workers.tasks.normalize_scores",
            args=[
                scope,
                str(country_id) if country_id else None,
                str(region_id) if region_id else None,
                str(city_id) if city_id else None,
                hotel_type,
                [str(item) for item in hotel_ids] if hotel_ids else None,
            ],
        )

    def retry_scrape_failure(self, failure_id: uuid.UUID) -> None:
        """Queue a bounded scraper failure retry."""
        self._celery.send_task("app.workers.tasks.retry_scrape_failure", args=[str(failure_id)])

    def scrape_run(self, run_id: uuid.UUID) -> None:
        """Queue an explicitly scoped, resumable source scrape."""
        self._celery.send_task("app.workers.tasks.run_scrape", args=[str(run_id)])

    def scan_data_quality(self) -> None:
        """Queue a database consistency scan."""
        self._celery.send_task("app.workers.tasks.scan_data_quality")
