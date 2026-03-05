from typing import Literal

from pydantic import BaseModel, Field


TaskStatus = Literal["queued", "processing", "completed", "failed", "not_found"]


class UploadDocumentResponse(BaseModel):
    task_id: str
    status: TaskStatus
    message: str


class DocumentStatusResponse(BaseModel):
    task_id: str
    status: TaskStatus
    detail: str | None = None


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=4000)
    top_k: int = Field(default=7, ge=1, le=20)
    model: Literal["llama", "deepseek"] = "llama"
    conversation_context: str | None = Field(default=None, max_length=6000)


class AskResponse(BaseModel):
    answer: str
    sources: list[str] = Field(default_factory=list)


class TranscribeResponse(BaseModel):
    text: str
