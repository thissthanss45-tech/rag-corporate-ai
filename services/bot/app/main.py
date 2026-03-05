import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer

from app.api_client import create_api_client
from app.config import settings
from app.handlers import router


def configure_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


async def main() -> None:
    configure_logging()
    logger = logging.getLogger(__name__)

    if not settings.TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")

    bot = await _build_bot(logger)
    dispatcher = Dispatcher()
    api_client = create_api_client()

    dispatcher["api_client"] = api_client
    dispatcher.include_router(router)

    logger.info(
        "🔍 bot client started",
        extra={
            "app": settings.APP_NAME,
            "api_base_url": settings.API_BASE_URL,
            "telegram_api_base": settings.TELEGRAM_API_BASE,
        },
    )

    await api_client.open()
    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await dispatcher.start_polling(bot)
    finally:
        await api_client.close()
        await bot.session.close()


async def _build_bot(logger: logging.Logger) -> Bot:
    if not settings.TELEGRAM_LOCAL:
        return Bot(token=settings.TELEGRAM_BOT_TOKEN)

    api_server = TelegramAPIServer.from_base(
        base=settings.TELEGRAM_API_BASE,
        is_local=True,
    )
    session = AiohttpSession(api=api_server)
    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN, session=session)

    try:
        await bot.get_me()
        logger.info("🚀 local telegram api enabled", extra={"telegram_api_base": settings.TELEGRAM_API_BASE})
        return bot
    except Exception as exc:
        logger.warning(
            "↩️ local telegram api unavailable, fallback to default telegram endpoint",
            extra={"telegram_api_base": settings.TELEGRAM_API_BASE, "error": str(exc)},
        )
        await bot.session.close()
        return Bot(token=settings.TELEGRAM_BOT_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
