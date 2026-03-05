import os
import asyncio
import time
import logging
from pathlib import Path
from app.core.builder import build_knowledge_base
from aiogram import Router, F , Bot
from aiogram.types import Message
from app.config import settings
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext

from app.bot.keyboards.reply import get_main_keyboard
from app.bot.states import ChatStates
# Подключаем наш мозг
from app.core.rag import RAGService
from app.observability import (
    DOCUMENT_UPLOADS_TOTAL,
    INDEX_BUILD_DURATION_SECONDS,
    RAG_REQUEST_DURATION_SECONDS,
    RAG_REQUESTS_TOTAL,
    audit_event,
    increment_counter,
    measure_duration,
)

router = Router()
logger = logging.getLogger(__name__)


def parse_admin_ids(raw: str) -> set[int]:
    result: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            result.add(int(part))
        except ValueError:
            logger.warning("Invalid admin id in ADMIN_IDS: %s", part)
    return result


def user_can_upload(user_id: int | None) -> bool:
    if user_id is None:
        return False

    admin_ids = parse_admin_ids(settings.ADMIN_IDS)
    if settings.OWNER_ID is not None:
        admin_ids.add(settings.OWNER_ID)
    return user_id in admin_ids

# Инициализируем RAG один раз при запуске модуля (загрузка модели занимает ~2-5 сек)
logger.info("Initialising RAG service...")
rag_service = RAGService()
logger.info("RAG service ready")

# --- БАЗОВЫЕ КОМАНДЫ ---

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    # Сбрасываем любые состояния при старте
    await state.clear()
    
    await message.answer(
        text=(
            "👋 Привет, Командир!\n\n"
            "Система RAG готова к работе.\n"
            "Нажми 'AI Chat', чтобы задать вопрос по документам."
        ),
        reply_markup=get_main_keyboard()
    )

# --- ОБРАБОТКА КНОПОК ---

@router.message(F.text == "AI Chat 🧠✨")
async def btn_chat_reaction(message: Message, state: FSMContext):
    # Включаем режим ожидания вопроса
    await state.set_state(ChatStates.waiting_for_question)
    
    await message.answer(
        "🧠 **Режим AI активирован.**\n\n"
        "Напиши свой вопрос, и я поищу ответ в документах.\n"
        "Чтобы выйти, нажми любую другую кнопку меню.",
        parse_mode="Markdown"
    )

@router.message(F.text == "Загрузить документ 📤")
async def btn_upload_reaction(message: Message, state: FSMContext):
    # Включаем режим ожидания файла
    await state.set_state(ChatStates.waiting_for_upload)
    
    await message.answer(
        "📂 **Режим загрузки активирован.**\n\n"
        "Отправь мне файл (PDF или DOCX), и я добавлю его в нашу базу данных.\n"
        "Жду документ...",
        parse_mode="Markdown"
    )

# --- ЛОГИКА ОТВЕТОВ (САМОЕ ГЛАВНОЕ) ---

# Этот хендлер ловит текст ТОЛЬКО если включен режим waiting_for_question
@router.message(StateFilter(ChatStates.waiting_for_question))
async def handle_rag_question(message: Message):
    user_text = message.text
    user_id = message.from_user.id if message.from_user else None
    
    # Игнорируем, если пользователь случайно нажал кнопку меню (они обработаются выше)
    if user_text in ["AI Chat 🧠✨", "Загрузить документ 📤"]:
        return

    # Отправляем уведомление, что "думаем" (полезно для UX)
    wait_msg = await message.answer("🕵️ Анализирую документы...")
    audit_event("rag_question_received", user_id=user_id)

    # Обращаемся к мозгу (это может занять 2-5 секунд)
    # Запускаем в отдельном потоке, чтобы не морозить бота, но пока для простоты так:
    try:
        with measure_duration(RAG_REQUEST_DURATION_SECONDS):
            response = await asyncio.to_thread(rag_service.get_answer, user_text)
        increment_counter(RAG_REQUESTS_TOTAL, "ok")
        audit_event("rag_answer_sent", user_id=user_id)
    except Exception:
        increment_counter(RAG_REQUESTS_TOTAL, "error")
        audit_event("rag_answer_failed", user_id=user_id)
        logger.exception("RAG request failed for user %s", user_id)
        await wait_msg.delete()
        await message.answer("⚠️ Произошла ошибка при обработке запроса. Попробуйте ещё раз.")
        return

    # Удаляем сообщение "Анализирую..."
    await wait_msg.delete()

    # Отправляем ответ
    await message.answer(response)

    # Ловим документы, НО только если мы в режиме waiting_for_upload
@router.message(StateFilter(ChatStates.waiting_for_upload), F.document)
async def handle_document_upload(message: Message, bot: Bot, state: FSMContext):
    document = message.document
    file_name = document.file_name
    user_id = message.from_user.id if message.from_user else None

    if not user_can_upload(user_id):
        increment_counter(DOCUMENT_UPLOADS_TOTAL, "forbidden")
        audit_event("upload_forbidden", user_id=user_id, file_name=file_name)
        await message.answer("⛔ У вас нет прав на загрузку документов.")
        return

    if not (file_name.lower().endswith('.pdf') or file_name.lower().endswith('.docx')):
        increment_counter(DOCUMENT_UPLOADS_TOTAL, "invalid_type")
        audit_event("upload_invalid_type", user_id=user_id, file_name=file_name)
        await message.answer("⚠️ Я понимаю только PDF и DOCX. Попробуй другой файл.")
        return

    max_upload_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if document.file_size and document.file_size > max_upload_bytes:
        increment_counter(DOCUMENT_UPLOADS_TOTAL, "too_large")
        audit_event("upload_too_large", user_id=user_id, file_name=file_name)
        await message.answer(
            f"⚠️ Файл слишком большой. Лимит: {settings.MAX_UPLOAD_SIZE_MB} MB."
        )
        return

    wait_msg = await message.answer(f"📥 Скачиваю файл: {file_name}...")

    safe_file_name = Path(file_name).name
    os.makedirs(settings.DATA_PATH, exist_ok=True)
    destination = os.path.join(settings.DATA_PATH, safe_file_name)

    if os.path.exists(destination):
        stem = Path(safe_file_name).stem
        suffix = Path(safe_file_name).suffix
        safe_file_name = f"{stem}_{int(time.time())}{suffix}"
        destination = os.path.join(settings.DATA_PATH, safe_file_name)

    try:
        # 1. Скачиваем
        await bot.download(document, destination=destination)
        
        await wait_msg.edit_text(
            f"✅ Файл сохранен: `{safe_file_name}`\n"
            "⚙️ **Начинаю чтение и индексацию...**\n"
            "Подождите, я обновляю нейронные связи...",
            parse_mode="Markdown"
        )

        # 2. Пересборка базы (на диске)
        with measure_duration(INDEX_BUILD_DURATION_SECONDS):
            total_chunks = await asyncio.to_thread(build_knowledge_base)
        
        # 3. ВАЖНО: Обновляем оперативную память бота!
        # Мы используем глобальную переменную rag_service, которая объявлена в начале файла
        rag_service.refresh_knowledge()
        
        # 4. Успех
        await message.answer(
            f"🎉 **База знаний обновлена!**\n\n"
            f"Теперь я знаю содержимое `{safe_file_name}`.\n"
            f"Всего фрагментов в памяти: {total_chunks}.\n\n"
            "Смело задавай вопросы!",
            parse_mode="Markdown"
        )
        
        await state.clear()
        increment_counter(DOCUMENT_UPLOADS_TOTAL, "ok")
        audit_event("upload_indexed", user_id=user_id, file_name=safe_file_name, total_chunks=total_chunks)
        
    except Exception as e:
        increment_counter(DOCUMENT_UPLOADS_TOTAL, "error")
        audit_event("upload_failed", user_id=user_id, file_name=file_name)
        logger.exception("Ошибка при обработке загрузки документа")
        await message.answer(f"❌ Произошла ошибка: {e}")
        await state.clear()

        
