from __future__ import annotations

import asyncio
import logging
import os
from io import BytesIO

from aiogram import F, Router
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.api_client import FastAPIClient
from app.config import settings
from app.keyboards import back_inline, cancel_keyboard, main_menu_keyboard, question_model_keyboard, upload_status_inline
from app.states import BotStates

router = Router()
logger = logging.getLogger(__name__)
MAX_DIALOG_TURNS = 4
_USER_PENDING_TASKS: dict[int, set[str]] = {}


def _add_pending_task(user_id: int | None, task_id: str) -> None:
    if not user_id or not task_id:
        return
    tasks = _USER_PENDING_TASKS.setdefault(user_id, set())
    tasks.add(task_id)


def _drop_pending_task(user_id: int | None, task_id: str) -> None:
    if not user_id or not task_id:
        return
    tasks = _USER_PENDING_TASKS.get(user_id)
    if not tasks:
        return
    tasks.discard(task_id)
    if not tasks:
        _USER_PENDING_TASKS.pop(user_id, None)


async def _has_pending_ingest(user_id: int | None, api_client: FastAPIClient) -> bool:
    if not user_id:
        return False

    pending = set(_USER_PENDING_TASKS.get(user_id, set()))
    if not pending:
        return False

    has_pending = False
    for task_id in pending:
        try:
            status = await api_client.get_task_status(task_id)
        except Exception as exc:
            logger.warning("↩️ pending task status check failed", extra={"task_id": task_id, "error": str(exc)})
            has_pending = True
            continue

        if status.status in {"completed", "failed", "not_found"}:
            _drop_pending_task(user_id, task_id)
        else:
            has_pending = True

    return has_pending


def _build_conversation_context(dialog_history: list[dict[str, str]]) -> str | None:
    if not dialog_history:
        return None

    lines: list[str] = []
    for turn in dialog_history[-MAX_DIALOG_TURNS:]:
        question = turn.get("question", "").strip()
        answer = turn.get("answer", "").strip()
        if not question or not answer:
            continue
        compact_answer = answer if len(answer) <= 400 else f"{answer[:400]}..."
        lines.append(f"Q: {question}\nA: {compact_answer}")

    if not lines:
        return None
    return "\n\n".join(lines)


async def _append_dialog_turn(state: FSMContext, question: str, answer: str) -> None:
    state_data = await state.get_data()
    dialog_history_raw = state_data.get("dialog_history")
    dialog_history = dialog_history_raw if isinstance(dialog_history_raw, list) else []

    dialog_history.append({"question": question.strip(), "answer": answer.strip()})
    await state.update_data(dialog_history=dialog_history[-MAX_DIALOG_TURNS:])


@router.message(CommandStart())
async def command_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "👋 Привет! Я клиент корпоративной RAG-системы. Выбери действие в меню.",
        reply_markup=main_menu_keyboard(),
    )


@router.message(F.text == "📄 Загрузить документ")
async def start_upload_mode(message: Message, state: FSMContext) -> None:
    await state.set_state(BotStates.waiting_for_document)
    await message.answer(
        "📎 Отправь PDF, DOCX, XLSX, TXT или RTF. Для экстренного выхода нажми ↩️ Отмена.",
        reply_markup=cancel_keyboard(),
    )


@router.message(F.text == "🧠 Задать вопрос")
async def start_question_mode(message: Message, state: FSMContext) -> None:
    await state.set_state(BotStates.waiting_for_question_model)
    await message.answer(
        "Выбери модель для ответа: Llama или DeepSeek.",
        reply_markup=question_model_keyboard(),
    )


@router.message(F.text == "🔁 Сменить модель")
async def change_question_model(message: Message, state: FSMContext) -> None:
    await state.set_state(BotStates.waiting_for_question_model)
    await message.answer(
        "Выбери модель для ответа: Llama или DeepSeek.",
        reply_markup=question_model_keyboard(),
    )


async def _select_question_model(message: Message, state: FSMContext, model: str, model_title: str) -> None:
    await state.update_data(question_model=model, dialog_history=[])
    await state.set_state(BotStates.waiting_for_question)
    await message.answer(
        f"✅ Выбрана модель: {model_title}.\n🧠 Напиши вопрос по документам или отправь голосовое сообщение.",
        reply_markup=cancel_keyboard(),
    )


@router.message(StateFilter(BotStates.waiting_for_question_model), F.text == "🦙 Llama")
async def select_llama_model(message: Message, state: FSMContext) -> None:
    await _select_question_model(message=message, state=state, model="llama", model_title="Llama")


@router.message(StateFilter(BotStates.waiting_for_question_model), F.text == "🐋 DeepSeek")
async def select_deepseek_model(message: Message, state: FSMContext) -> None:
    await _select_question_model(message=message, state=state, model="deepseek", model_title="DeepSeek")


@router.message(StateFilter(BotStates.waiting_for_question_model), F.text == "↩️ Отмена")
async def cancel_model_selection(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("↩️ Действие отменено. Возвращаю в главное меню.", reply_markup=main_menu_keyboard())


@router.message(StateFilter(BotStates.waiting_for_question_model))
async def waiting_question_model_choice(message: Message) -> None:
    await message.answer(
        "⚠️ Выбери одну из кнопок: 🦙 Llama или 🐋 DeepSeek.",
        reply_markup=question_model_keyboard(),
    )


@router.message(F.text == "↩️ Отмена")
async def cancel_any_mode(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("↩️ Действие отменено. Возвращаю в главное меню.", reply_markup=main_menu_keyboard())


@router.callback_query(F.data == "back:main")
async def callback_back_main(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if callback.message:
        await callback.message.answer("↩️ Возврат в главное меню.", reply_markup=main_menu_keyboard())
    await callback.answer()


@router.message(StateFilter(BotStates.waiting_for_document), ~F.document)
async def waiting_document_only_file(message: Message) -> None:
    await message.answer(
        "⚠️ Сейчас ожидается файл PDF, DOCX, XLSX, TXT или RTF. Если нужно выйти — нажми ↩️ Отмена.",
        reply_markup=cancel_keyboard(),
    )


@router.message(StateFilter(BotStates.waiting_for_document), F.document)
async def handle_document_upload(
    message: Message,
    state: FSMContext,
    api_client: FastAPIClient,
) -> None:
    if not message.document:
        return

    file_name = message.document.file_name or "document.bin"
    suffix = file_name.lower().split(".")[-1] if "." in file_name else ""
    if suffix not in {"pdf", "docx", "xlsx", "txt", "rtf"}:
        await message.answer("⚠️ Поддерживаются PDF, DOCX, XLSX, TXT и RTF.")
        return

    wait_message = await message.answer("⚙️ Загружаю файл и отправляю в очередь...")

    try:
        file_buffer = BytesIO()
        await message.bot.download(message.document, destination=file_buffer)
        file_bytes = file_buffer.getvalue()

        upload_result = await api_client.upload_document(file_name=file_name, file_bytes=file_bytes)
        _add_pending_task(message.from_user.id if message.from_user else None, upload_result.task_id)
        await wait_message.edit_text(
            f"✅ Файл принят. task_id: {upload_result.task_id}",
            reply_markup=upload_status_inline(upload_result.task_id),
        )

        asyncio.create_task(
            _poll_status_and_notify(
                message,
                api_client,
                upload_result.task_id,
                message.from_user.id if message.from_user else None,
            )
        )
    except Exception as exc:
        logger.exception("🗑 upload flow failed", extra={"file_name": file_name, "error": str(exc)})
        await wait_message.edit_text("⚠️ Не удалось отправить файл в API. Попробуй позже.", reply_markup=back_inline())
    finally:
        await state.clear()
        await message.answer("Главное меню активировано.", reply_markup=main_menu_keyboard())


@router.callback_query(F.data.startswith("status:"))
async def callback_check_status(callback: CallbackQuery, api_client: FastAPIClient) -> None:
    task_id = callback.data.split(":", maxsplit=1)[1]
    try:
        status_result = await api_client.get_task_status(task_id)
        text = f"🔍 Статус задачи {status_result.task_id}: {status_result.status}"
        if status_result.detail:
            text += f"\n{status_result.detail}"
        if callback.message:
            await callback.message.edit_text(text, reply_markup=upload_status_inline(task_id))
    except Exception as exc:
        logger.warning("↩️ status check failed", extra={"task_id": task_id, "error": str(exc)})
        await callback.answer("⚠️ Не удалось получить статус", show_alert=True)
    else:
        await callback.answer()


@router.message(StateFilter(BotStates.waiting_for_question), F.text)
async def handle_question(
    message: Message,
    state: FSMContext,
    api_client: FastAPIClient,
) -> None:
    if await _has_pending_ingest(message.from_user.id if message.from_user else None, api_client):
        await message.answer(
            "⏳ Документ ещё обрабатывается. Подожди завершения индексации и повтори вопрос.",
            reply_markup=cancel_keyboard(),
        )
        return

    user_text = (message.text or "").strip()
    if not user_text:
        await message.answer("⚠️ Вопрос пустой. Попробуй снова или нажми ↩️ Отмена.")
        return

    state_data = await state.get_data()
    model = str(state_data.get("question_model", "llama"))
    dialog_history_raw = state_data.get("dialog_history")
    dialog_history = dialog_history_raw if isinstance(dialog_history_raw, list) else []
    conversation_context = _build_conversation_context(dialog_history)
    model_title = "DeepSeek" if model == "deepseek" else "Llama"

    wait_message = await message.answer(f"🧠 Анализирую контекст ({model_title})...")
    try:
        client_id = str(message.from_user.id) if message.from_user else ""
        ask_result = await api_client.ask(
            question=user_text,
            model=model,
            conversation_context=conversation_context,
            client_id=client_id,
        )
        final_answer = ask_result.answer
        await _append_dialog_turn(state=state, question=user_text, answer=final_answer)
        await wait_message.edit_text(final_answer, reply_markup=back_inline())
        await message.answer(
            "✅ Можешь задать следующий вопрос в этом же режиме или нажать ↩️ Отмена.",
            reply_markup=cancel_keyboard(),
        )
    except Exception as exc:
        logger.exception("🗑 ask flow failed", extra={"error": str(exc)})
        await wait_message.edit_text("⚠️ Не удалось получить ответ от API. Попробуй позже.", reply_markup=back_inline())


@router.message(StateFilter(BotStates.waiting_for_question), F.voice)
async def handle_voice_question(
    message: Message,
    state: FSMContext,
    api_client: FastAPIClient,
) -> None:
    if not message.voice:
        return

    if await _has_pending_ingest(message.from_user.id if message.from_user else None, api_client):
        await message.answer(
            "⏳ Документ ещё обрабатывается. Подожди завершения индексации и повтори голосовой вопрос.",
            reply_markup=cancel_keyboard(),
        )
        return

    wait_message = await message.answer("🎧 Распознаю голос и подготавливаю вопрос...")
    state_data = await state.get_data()
    model = str(state_data.get("question_model", "llama"))
    dialog_history_raw = state_data.get("dialog_history")
    dialog_history = dialog_history_raw if isinstance(dialog_history_raw, list) else []
    conversation_context = _build_conversation_context(dialog_history)
    local_voice_path: str | None = None
    try:
        telegram_file = await message.bot.get_file(message.voice.file_id)
        local_voice_path = telegram_file.file_path

        file_buffer = BytesIO()
        await message.bot.download_file(telegram_file.file_path, destination=file_buffer)
        voice_bytes = file_buffer.getvalue()

        transcribe_result = await api_client.transcribe(file_name=f"voice_{message.voice.file_unique_id}.ogg", file_bytes=voice_bytes)
        transcribed_text = transcribe_result.text.strip()
        if not transcribed_text:
            await wait_message.edit_text("⚠️ Не удалось распознать голос. Попробуй ещё раз.", reply_markup=back_inline())
            return

        preview = transcribed_text if len(transcribed_text) <= 700 else f"{transcribed_text[:700]}..."
        await message.answer(f"🎙️ Распознано:\n{preview}")

        client_id = str(message.from_user.id) if message.from_user else ""
        ask_result = await api_client.ask(
            question=transcribed_text,
            model=model,
            conversation_context=conversation_context,
            client_id=client_id,
        )
        final_answer = ask_result.answer
        await _append_dialog_turn(state=state, question=transcribed_text, answer=final_answer)
        await wait_message.edit_text(final_answer, reply_markup=back_inline())
        await message.answer(
            "✅ Можешь отправить ещё один вопрос/голос в этом же режиме или нажать ↩️ Отмена.",
            reply_markup=cancel_keyboard(),
        )
    except Exception as exc:
        logger.exception("🗑 voice ask flow failed", extra={"error": str(exc)})
        await wait_message.edit_text(
            "⚠️ Не удалось обработать голосовое сообщение. Попробуй позже.",
            reply_markup=back_inline(),
        )
    finally:
        if local_voice_path and local_voice_path.startswith("/var/lib/telegram-bot-api/"):
            try:
                if os.path.exists(local_voice_path):
                    os.remove(local_voice_path)
                    logger.info("🗑 local voice file deleted", extra={"file_path": local_voice_path})
            except OSError as exc:
                logger.warning("⚠️ failed to delete local voice file", extra={"file_path": local_voice_path, "error": str(exc)})


@router.message(StateFilter(BotStates.waiting_for_question), ~F.text)
async def waiting_question_only_text(message: Message) -> None:
    await message.answer(
        "⚠️ Сейчас ожидается текст вопроса или голосовое сообщение. Для экстренного выхода нажми ↩️ Отмена.",
        reply_markup=cancel_keyboard(),
    )


async def _poll_status_and_notify(message: Message, api_client: FastAPIClient, task_id: str, user_id: int | None) -> None:
    for _ in range(settings.STATUS_POLL_MAX_ATTEMPTS):
        try:
            result = await api_client.get_task_status(task_id)
        except Exception as exc:
            logger.warning("↩️ polling retry", extra={"task_id": task_id, "error": str(exc)})
            await asyncio.sleep(settings.STATUS_POLL_INTERVAL_SEC)
            continue

        if result.status in {"completed", "failed"}:
            _drop_pending_task(user_id, task_id)
            icon = "✅" if result.status == "completed" else "🗑"
            text = f"{icon} Обработка документа завершена: {result.status}"
            if result.detail:
                text += f"\n{result.detail}"
            await message.answer(text, reply_markup=main_menu_keyboard())
            return

        await asyncio.sleep(settings.STATUS_POLL_INTERVAL_SEC)
