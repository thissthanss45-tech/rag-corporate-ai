from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass

import httpx


API_BASE_URL = os.getenv("SMOKE_API_BASE_URL", "http://localhost:8000")
API_PREFIX = os.getenv("SMOKE_API_PREFIX", "/api/v1")
POLL_INTERVAL_SEC = float(os.getenv("SMOKE_POLL_INTERVAL_SEC", "3"))
POLL_MAX_ATTEMPTS = int(os.getenv("SMOKE_POLL_MAX_ATTEMPTS", "60"))


@dataclass(frozen=True)
class UploadResponse:
    task_id: str
    status: str


def log_step(message: str) -> None:
    print(message, flush=True)


def build_test_pdf_bytes() -> bytes:
    body_parts: list[bytes] = []
    offsets: list[int] = []

    def add(part: bytes) -> None:
        body_parts.append(part)

    add(b"%PDF-1.4\n")

    objects = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        (
            b"3 0 obj\n"
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>\n"
            b"endobj\n"
        ),
        b"4 0 obj\n<< /Length 76 >>\nstream\nBT /F1 18 Tf 72 720 Td (Smoke test document about ACME policy 2026) Tj ET\nendstream\nendobj\n",
        b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
    ]

    current_length = len(b"".join(body_parts))
    for obj in objects:
        offsets.append(current_length)
        add(obj)
        current_length += len(obj)

    xref_start = len(b"".join(body_parts))
    xref_lines = [b"xref\n", b"0 6\n", b"0000000000 65535 f \n"]
    for offset in offsets:
        xref_lines.append(f"{offset:010d} 00000 n \n".encode("ascii"))
    add(b"".join(xref_lines))

    trailer = (
        b"trailer\n"
        b"<< /Size 6 /Root 1 0 R >>\n"
        b"startxref\n"
        + str(xref_start).encode("ascii")
        + b"\n%%EOF\n"
    )
    add(trailer)

    return b"".join(body_parts)


async def upload_document(client: httpx.AsyncClient) -> UploadResponse:
    pdf_bytes = build_test_pdf_bytes()
    files = {"file": ("smoke_test.pdf", pdf_bytes, "application/pdf")}

    response = await client.post(f"{API_PREFIX}/documents/upload", files=files)
    response.raise_for_status()
    data = response.json()

    task_id = str(data.get("task_id", ""))
    status = str(data.get("status", ""))
    if not task_id:
        raise RuntimeError("⚠️ Upload response does not contain task_id")
    return UploadResponse(task_id=task_id, status=status)


async def wait_for_completion(client: httpx.AsyncClient, task_id: str) -> None:
    for attempt in range(1, POLL_MAX_ATTEMPTS + 1):
        response = await client.get(f"{API_PREFIX}/documents/status/{task_id}")
        response.raise_for_status()
        data = response.json()
        status = str(data.get("status", "unknown"))
        detail = data.get("detail")

        if status == "completed":
            log_step(f"✅ Документ обработан. task_id={task_id}")
            return
        if status == "failed":
            raise RuntimeError(f"🗑 Обработка завершилась ошибкой. detail={detail}")

        log_step(f"⏳ Ожидание воркера ({attempt}/{POLL_MAX_ATTEMPTS}), текущий статус: {status}")
        await asyncio.sleep(POLL_INTERVAL_SEC)

    raise TimeoutError("⚠️ Таймаут ожидания обработки документа")


async def ask_question(client: httpx.AsyncClient) -> str:
    payload = {
        "question": "О чем говорится в документе smoke test?",
    }
    response = await client.post(f"{API_PREFIX}/chat/ask", json=payload)
    response.raise_for_status()
    data = response.json()
    answer = str(data.get("answer", "")).strip()
    if not answer:
        raise RuntimeError("⚠️ Пустой ответ от /chat/ask")
    return answer


async def main() -> None:
    log_step("🚀 Старт smoke-теста платформы")
    async with httpx.AsyncClient(base_url=API_BASE_URL, timeout=60) as client:
        health = await client.get(f"{API_PREFIX}/health")
        if health.status_code not in {200, 503}:
            raise RuntimeError(f"⚠️ Неожиданный ответ health-check: {health.status_code}")

        upload = await upload_document(client)
        log_step(f"✅ Файл загружен, task_id={upload.task_id}, status={upload.status}")

        await wait_for_completion(client, upload.task_id)

        answer = await ask_question(client)
        log_step("✅ Ответ ИИ получен")
        print("\n=== AI ANSWER ===")
        print(answer)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f"⚠️ Smoke-тест завершился с ошибкой: {exc}")
        raise
