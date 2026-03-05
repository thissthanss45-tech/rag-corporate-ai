from celery import Celery

from app.core.config import settings


celery_client = Celery(
    "rag-api-client",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

celery_client.conf.update(
    task_default_queue=settings.CELERY_TASK_DEFAULT_QUEUE,
)


@celery_client.task(name=settings.CELERY_DOCUMENT_TASK_NAME, queue=settings.CELERY_TASK_DEFAULT_QUEUE)
def process_document_task(file_path: str, file_name: str) -> dict[str, str]:
    return {"status": "queued", "file_path": file_path, "file_name": file_name}
