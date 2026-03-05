import logging

from celery import Celery
from celery.signals import task_failure, task_retry

from app.config import settings


logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


celery_app = Celery(
    settings.APP_NAME,
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["app.tasks"],
)

celery_app.conf.update(
    task_default_queue=settings.CELERY_TASK_DEFAULT_QUEUE,
    task_track_started=True,
    task_acks_late=settings.CELERY_TASK_ACKS_LATE,
    task_reject_on_worker_lost=settings.CELERY_TASK_REJECT_ON_WORKER_LOST,
    task_soft_time_limit=settings.CELERY_TASK_SOFT_TIME_LIMIT,
    task_time_limit=settings.CELERY_TASK_TIME_LIMIT,
    worker_prefetch_multiplier=settings.CELERY_WORKER_PREFETCH_MULTIPLIER,
    worker_cancel_long_running_tasks_on_connection_loss=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    result_expires=86400,
    task_default_retry_delay=30,
    task_annotations={
        "*": {
            "max_retries": 5,
        }
    },
)


@task_retry.connect
def on_task_retry(sender=None, request=None, reason=None, einfo=None, **kwargs) -> None:
    logger.warning(
        "↩️ task retry requested",
        extra={
            "task_name": getattr(sender, "name", "unknown"),
            "task_id": getattr(request, "id", None),
            "reason": str(reason) if reason else None,
        },
    )


@task_failure.connect
def on_task_failure(sender=None, task_id=None, exception=None, args=None, kwargs=None, traceback=None, einfo=None, **_ignored) -> None:
    logger.error(
        "⚠️ task failed",
        extra={
            "task_name": getattr(sender, "name", "unknown"),
            "task_id": task_id,
            "exception": str(exception) if exception else None,
        },
    )


logger.info(
    "Celery app initialized",
    extra={
        "broker": settings.CELERY_BROKER_URL,
        "backend": settings.CELERY_RESULT_BACKEND,
        "queue": settings.CELERY_TASK_DEFAULT_QUEUE,
    },
)
