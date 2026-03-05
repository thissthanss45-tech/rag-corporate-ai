from __future__ import annotations

from dataclasses import dataclass

import aiohttp

from app.config import settings


@dataclass(frozen=True)
class UploadResult:
    task_id: str
    status: str
    message: str


@dataclass(frozen=True)
class TaskStatusResult:
    task_id: str
    status: str
    detail: str | None


@dataclass(frozen=True)
class AskResult:
    answer: str
    sources: list[str]


@dataclass(frozen=True)
class TranscribeResult:
    text: str


class FastAPIClient:
    def __init__(self, base_url: str, api_prefix: str, timeout_seconds: int, service_token: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_prefix = api_prefix.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._session: aiohttp.ClientSession | None = None
        self._service_token = service_token.strip()

    def _auth_headers(self, client_id: str | None = None) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self._service_token:
            headers["X-Service-Token"] = self._service_token
        normalized_client_id = (client_id or "").strip()
        if normalized_client_id:
            headers["X-Client-Id"] = normalized_client_id
        return headers

    async def open(self) -> None:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def upload_document(self, file_name: str, file_bytes: bytes) -> UploadResult:
        session = self._ensure_session()
        url = f"{self._base_url}{self._api_prefix}/documents/upload"

        form = aiohttp.FormData()
        form.add_field("file", file_bytes, filename=file_name, content_type="application/octet-stream")

        async with session.post(url, data=form, headers=self._auth_headers()) as response:
            data = await response.json(content_type=None)
            self._raise_on_error(response.status, data)
            return UploadResult(
                task_id=str(data.get("task_id", "")),
                status=str(data.get("status", "queued")),
                message=str(data.get("message", "")),
            )

    async def get_task_status(self, task_id: str) -> TaskStatusResult:
        session = self._ensure_session()
        url = f"{self._base_url}{self._api_prefix}/documents/status/{task_id}"

        async with session.get(url, headers=self._auth_headers()) as response:
            data = await response.json(content_type=None)
            self._raise_on_error(response.status, data)
            return TaskStatusResult(
                task_id=str(data.get("task_id", task_id)),
                status=str(data.get("status", "not_found")),
                detail=data.get("detail"),
            )

    async def ask(
        self,
        question: str,
        model: str = "llama",
        conversation_context: str | None = None,
        client_id: str | None = None,
    ) -> AskResult:
        session = self._ensure_session()
        url = f"{self._base_url}{self._api_prefix}/chat/ask"
        payload: dict[str, str] = {"question": question, "model": model}
        normalized_context = (conversation_context or "").strip()
        if normalized_context:
            payload["conversation_context"] = normalized_context

        async with session.post(url, json=payload, headers=self._auth_headers(client_id=client_id)) as response:
            data = await response.json(content_type=None)
            self._raise_on_error(response.status, data)
            sources = [str(item) for item in data.get("sources", [])]
            return AskResult(answer=str(data.get("answer", "")), sources=sources)

    async def transcribe(self, file_name: str, file_bytes: bytes) -> TranscribeResult:
        session = self._ensure_session()
        url = f"{self._base_url}{self._api_prefix}/chat/transcribe"

        form = aiohttp.FormData()
        form.add_field("file", file_bytes, filename=file_name, content_type="audio/ogg")

        async with session.post(url, data=form, headers=self._auth_headers()) as response:
            data = await response.json(content_type=None)
            self._raise_on_error(response.status, data)
            return TranscribeResult(text=str(data.get("text", "")).strip())

    @staticmethod
    def _raise_on_error(status_code: int, payload: dict) -> None:
        if 200 <= status_code < 300:
            return
        detail = payload.get("detail") if isinstance(payload, dict) else None
        message = str(detail or f"API request failed with status {status_code}")
        raise RuntimeError(message)

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            raise RuntimeError("API client session is not initialized")
        return self._session


def create_api_client() -> FastAPIClient:
    return FastAPIClient(
        base_url=settings.API_BASE_URL,
        api_prefix=settings.API_PREFIX,
        timeout_seconds=settings.API_TIMEOUT_SECONDS,
        service_token=settings.API_SERVICE_TOKEN,
    )
