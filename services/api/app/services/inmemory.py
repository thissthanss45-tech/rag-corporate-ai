import logging
from uuid import uuid4

from app.schemas import DocumentStatusResponse

logger = logging.getLogger(__name__)


class InMemoryTaskBroker:
    def __init__(self) -> None:
        self._tasks: dict[str, DocumentStatusResponse] = {}

    def enqueue_document(self, file_path: str, original_filename: str) -> str:
        task_id = str(uuid4())
        logger.info(
            "Queueing document task",
            extra={"task_id": task_id, "file_path": file_path, "original_filename": original_filename},
        )
        self._tasks[task_id] = DocumentStatusResponse(
            task_id=task_id,
            status="queued",
            detail=f"Документ '{original_filename}' поставлен в очередь",
        )
        return task_id

    def get_status(self, task_id: str) -> DocumentStatusResponse:
        status = self._tasks.get(task_id)
        if status is None:
            return DocumentStatusResponse(task_id=task_id, status="not_found", detail="Задача не найдена")
        return status


class StubRagService:
    def ask(self, question: str, top_k: int) -> tuple[str, list[str]]:
        logger.info("RAG ask called", extra={"question": question, "top_k": top_k})
        answer = (
            "Сервис RAG API работает. На этапе 1 подключен скелет ответа. "
            "На этапе 2 сюда подключается гибридный ретривер Qdrant + BM25."
        )
        sources = ["stub://knowledge-base"]
        return answer, sources
