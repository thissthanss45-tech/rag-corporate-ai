from celery.result import AsyncResult

from app.celery_client import celery_client, process_document_task
from app.schemas import DocumentStatusResponse, TaskStatus


class CeleryTaskBroker:
    def enqueue_document(self, file_path: str, original_filename: str) -> str:
        async_result = process_document_task.delay(file_path, original_filename)
        return async_result.id

    def get_status(self, task_id: str) -> DocumentStatusResponse:
        result: AsyncResult = celery_client.AsyncResult(task_id)
        mapped_status, detail = self._map_state(result)
        return DocumentStatusResponse(task_id=task_id, status=mapped_status, detail=detail)

    @staticmethod
    def _map_state(result: AsyncResult) -> tuple[TaskStatus, str]:
        state = (result.state or "").upper()

        if state == "SUCCESS":
            return "completed", "Задача успешно завершена"
        if state in {"FAILURE", "REVOKED"}:
            return "failed", "Задача завершилась с ошибкой"
        if state in {"STARTED", "RETRY"}:
            return "processing", "Задача обрабатывается"
        if state == "PENDING":
            return "queued", "Задача в очереди или ожидает воркер"

        return "queued", f"Текущий статус Celery: {state}"
