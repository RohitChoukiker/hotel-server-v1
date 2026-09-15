"""Celery dispatch-boundary tests without a broker."""

import unittest
import uuid

from app.integrations.tasks import TaskDispatcher


class _Celery:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str] | None]] = []

    def send_task(self, name: str, args: list[str] | None = None) -> None:
        self.calls.append((name, args))


class TaskDispatcherTests(unittest.TestCase):
    """Verify heavy work is queued under stable task names."""

    def test_all_task_routes(self) -> None:
        celery = _Celery()
        dispatcher = TaskDispatcher(celery)  # type: ignore[arg-type]
        identifier = uuid.UUID("12345678-1234-5678-1234-567812345678")
        dispatcher.import_csv(identifier)
        dispatcher.process_reviews()
        dispatcher.recalculate_hotel(identifier)
        dispatcher.normalize("CITY")
        dispatcher.retry_scrape_failure(identifier)
        dispatcher.scrape_run(identifier)
        dispatcher.scan_data_quality()
        self.assertEqual(
            [name for name, _ in celery.calls],
            [
                "app.workers.tasks.import_csv",
                "app.workers.tasks.process_reviews",
                "app.workers.tasks.recalculate_hotel",
                "app.workers.tasks.normalize_scores",
                "app.workers.tasks.retry_scrape_failure",
                "app.workers.tasks.run_scrape",
                "app.workers.tasks.scan_data_quality",
            ],
        )


if __name__ == "__main__":
    unittest.main()
