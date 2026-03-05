# Services Skeleton

На этапе 1 добавлен каркас микросервисной архитектуры:

- `api/` — FastAPI ядро RAG
- `worker/` — асинхронная обработка документов (Celery/RabbitMQ)
- `bot/` — Telegram-клиент (Aiogram), общается только с API
