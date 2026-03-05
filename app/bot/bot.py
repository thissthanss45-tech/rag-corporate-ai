import asyncio
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher
from app.config import settings
from app.bot.handlers import base

logger = logging.getLogger(__name__)


async def heartbeat_loop(stop_event: asyncio.Event) -> None:
    heartbeat_path = Path(settings.HEARTBEAT_FILE)
    heartbeat_path.parent.mkdir(parents=True, exist_ok=True)

    while not stop_event.is_set():
        heartbeat_path.write_text(str(asyncio.get_running_loop().time()), encoding="utf-8")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=settings.HEARTBEAT_INTERVAL_SEC)
        except asyncio.TimeoutError:
            continue

async def main():
    # 1. Создаем объект бота
    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    
    # 2. Создаем диспетчер (мозг, обрабатывающий события)
    dp = Dispatcher()
    
    # 3. Подключаем наши обработчики (роутеры)
    dp.include_router(base.router)

    # 4. Удаляем старые сообщения (webhook), чтобы бот не отвечал на то, что было, пока он спал
    await bot.delete_webhook(drop_pending_updates=True)
    
    logger.info("Бот запущен")

    stop_event = asyncio.Event()
    heartbeat_task = asyncio.create_task(heartbeat_loop(stop_event))

    try:
        # 5. Запускаем бесконечный цикл прослушивания
        await dp.start_polling(bot)
    finally:
        stop_event.set()
        await heartbeat_task
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен")