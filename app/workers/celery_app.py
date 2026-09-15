"""Celery application wiring only."""

from celery import Celery

from app.config import get_settings

settings = get_settings()
celery_app = Celery(
    "hotel-platform",
    broker=settings.redis.url.get_secret_value(),
    backend=settings.redis.url.get_secret_value(),
    include=["app.workers.tasks.jobs"],
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    task_time_limit=settings.worker.task_time_limit_seconds,
    broker_connection_retry_on_startup=True,
    beat_schedule={
        "worker-heartbeat": {
            "task": "app.workers.tasks.worker_heartbeat",
            "schedule": 30.0,
        },
        "hourly-data-quality-scan": {
            "task": "app.workers.tasks.scan_data_quality",
            "schedule": 3600.0,
        },
    },
)
