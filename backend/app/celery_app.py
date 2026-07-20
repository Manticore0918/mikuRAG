from celery import Celery

from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "mikurag",
    broker=settings.redis_url,
    include=["app.ingestion.tasks", "app.uploads.tasks"],
)
celery_app.conf.update(
    accept_content=["json"],
    broker_connection_retry_on_startup=True,
    enable_utc=True,
    task_acks_late=True,
    task_ignore_result=True,
    task_reject_on_worker_lost=True,
    task_serializer="json",
    timezone="UTC",
    worker_prefetch_multiplier=1,
    beat_schedule={
        "cleanup-expired-upload-sessions": {
            "task": "mikurag.uploads.cleanup",
            "schedule": 3600.0,
        }
    },
)


@celery_app.task(name="mikurag.health.ping")
def ping() -> str:
    return "pong"
