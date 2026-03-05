from typing import Protocol

from app.schemas import DocumentStatusResponse


class TaskBroker(Protocol):
    def enqueue_document(self, file_path: str, original_filename: str) -> str:
        ...

    def get_status(self, task_id: str) -> DocumentStatusResponse:
        ...


class RagService(Protocol):
    def ask(self, question: str, top_k: int) -> tuple[str, list[str]]:
        ...
