import argparse
import asyncio
import warnings

from app.observability import configure_logging, init_error_tracking, start_metrics_server


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Corporate RAG assistant")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("bot", help="Запустить Telegram-бота")
    subparsers.add_parser("build-index", help="Пересобрать индекс знаний")
    return parser.parse_args()


def main() -> None:
    configure_logging()
    init_error_tracking()
    start_metrics_server()
    warnings.warn(
        "Legacy runtime app/main.py is deprecated. Use services/* runtime via docker compose for production.",
        DeprecationWarning,
        stacklevel=2,
    )
    args = parse_args()

    if args.command == "bot":
        from app.bot.bot import main as run_bot

        asyncio.run(run_bot())
        return

    if args.command == "build-index":
        from app.core.builder import build_knowledge_base

        total = build_knowledge_base()
        print(f"✅ Индексация завершена. Фрагментов: {total}")
        return


if __name__ == "__main__":
    main()
