# Enterprise Corporate RAG Platform

![CI](https://github.com/thissthanss45-tech/rag-corporatet-ai/actions/workflows/ci.yml/badge.svg)
![Python](https://img.shields.io/badge/python-3.11-blue)
![License](https://img.shields.io/badge/license-MIT-green)

Микросервисная платформа корпоративного поиска по документам (RAG) с гибридным поиском (Dense + Sparse), Telegram-клиентом и Prometheus-мониторингом.

## Стек технологий

| Слой | Технологии |
|------|-----------|
| API | FastAPI, Python 3.11, Uvicorn |
| Очереди | Celery + RabbitMQ |
| Векторная БД | Qdrant (гибридный поиск: cosine + BM25) |
| Эмбеддинги | `sentence-transformers` (paraphrase-multilingual-MiniLM-L12-v2) |
| LLM | Groq (llama-3.3-70b) / DeepSeek (с fallback цепочкой) |
| Бот | Aiogram 3.x |
| Мониторинг | Prometheus + Grafana (алерты: RAGAPIDown, HighErrorRate, SlowP95) |
| Контейнеры | Docker Compose (prod / staging / monitoring) |

## Возможности

- 📄 **Загрузка документов** (PDF, DOCX) через API или Telegram-бот
- 🔍 **Гибридный поиск** — dense embedding + sparse BM25 из одного запроса
- 🤖 **AIответы** с верификацией grounded-mode и ссылками на источники
- 🔒 **Security layer**: проверка сигнатур файлов, Bearer-авторизация, audit-лог
- 📊 **Prometheus-метрики** на `/metrics`, Grafana dashboard из коробки
- 🧪 **RAGAS evaluation** + nightly quality gate (recall@k ≥ 0.60)

## Архитектура

```
┌─────────────────────────────────────────────────────┐
│                  Telegram Bot (Aiogram)              │
└───────────────────────┬─────────────────────────────┘
                        │ HTTP
┌───────────────────────▼─────────────────────────────┐
│              FastAPI (services/api)                  │
│  /upload  /status  /chat  /health  /metrics          │
└──────────┬──────────────────┬───────────────────────┘
           │ Celery task      │ Qdrant search
┌──────────▼──────┐  ┌────────▼────────┐
│  Worker         │  │  Qdrant         │
│  (PDF/DOCX      │  │  (векторная БД) │
│   parse+embed)  │  └─────────────────┘
└────────────────┘
           │
    ┌──────▼──────┐  ┌──────────┐
    │  RabbitMQ   │  │  Redis   │
    └─────────────┘  └──────────┘
```

## Быстрый запуск

### 1. Настройка окружения

```bash
cp .env.example .env
# Заполните GROQ_API_KEY, TELEGRAM_BOT_TOKEN, OWNER_ID
```

### 2. Запуск через Make

```bash
make up       # docker compose up -d --build
make logs     # следить за логами
make down     # остановить и удалить volumes
```

### 3. Smoke-check

```bash
curl http://localhost:8000/api/v1/health
python tests/smoke_test.py
```

## Мониторинг

```bash
# Запуск с Prometheus + Grafana
docker compose -f docker-compose.yml -f docker-compose.monitoring.yml up -d --build
```

- Prometheus UI: `http://localhost:9090`
- Grafana UI: `http://localhost:3000` (admin/admin)
- Dashboard **Corporate RAG Overview** подключается автоматически

## Тестирование

```bash
# Unit + integration тесты (services/api)
PYTHONPATH=services/api pytest -q services/api/tests

# Legacy тесты
pytest -q tests

# Quality gate (RAGAS recall@k)
make quality-gate
```

## Структура проекта

```
services/
  api/       ← FastAPI: upload, chat, health, metrics
  worker/    ← Celery: парсинг, чанкинг, эмбеддинги
  bot/       ← Telegram-клиент (aiogram)
app/         ← legacy compatibility layer
infra/       ← Prometheus, Grafana, Alertmanager конфиги
evaluation/  ← RAGAS eval dataset + скрипты
docs/        ← Руководства пользователей
```

## Производительность (capacity profile ~150 сотрудников)

| Параметр | Значение |
|----------|---------|
| Uvicorn workers | 3 |
| Celery concurrency | 4 |
| Rate limit | 360 req/min |
| Max chunk context | 14 000 chars |
| Retrieval chunks | 12 → top-9 |

## Документация

- [Руководство пользователя (RU)](docs/USER_GUIDE_RU.md)
- [Краткая памятка сотрудника](docs/QUICK_MEMO_RU.md)
- [Production readiness checklist](docs/PRODUCTION_READINESS_RU.md)

## Команды управления

```bash
make up       # запustить всё
make down     # остановить
make restart  # перезапустить
make logs     # логи в реальном времени
```


- Каноничный production-контур: `services/api` + `services/worker` + `services/bot`.
- Корневой `app/` оставлен для обратной совместимости и локальных legacy-сценариев.
- Новые изменения и тесты добавляй в `services/*` в первую очередь.

## Stage 3: legacy deprecation policy

- `app/*` считается compatibility-слоем, а не основным runtime.
- Обязательные проверки качества: `services/api/tests` (и далее `services/worker/tests`, `services/bot/tests` по мере добавления).
- Legacy-тесты в `tests/` остаются как обратная совместимость, но не должны блокировать развитие `services/*`.
- Любая новая функциональность должна попадать в `services/*`; в `app/*` допускаются только минимальные compatibility-правки.

## Stage 4: security hardening

- В `services/api` включена проверка сервисного токена через заголовок `X-Service-Token` (если задан `SERVICE_AUTH_TOKEN`).
- Добавлена проверка сигнатур файлов для `PDF`, `DOCX` и `OGG` (не только расширения).
- Добавлены унифицированные audit-события для auth/upload/status/chat/transcribe.

## Stage 5.1: production observability polish

- `rag-api` scrape в Prometheus (`/metrics`) добавлен в `infra/prometheus/prometheus.yml`.
- Добавлены API-алерты: `RAGAPIDown`, `RAGAPIHighErrorRate`, `RAGAPISlowResponsesP95`.
- Grafana dashboard расширен API-панелями (error ratio, throughput by endpoint, p95 by endpoint).
- `scripts/smoke_checks.py --mode release` проверяет monitoring-compose и обязательный services-test contour.

## Final production controls

- Alertmanager маршрутизирует алерты по severity (`critical` / `warning`) и подавляет warning при активном critical.
- Поддерживается zero-downtime reload мониторинга: `bash scripts/reload_monitoring.sh`.
- Release smoke дополнительно валидирует:
	- `promtool check config`
	- `promtool check rules`
	- `amtool check-config`

## Архитектура

- `api` — FastAPI-ядро: upload, status, chat, health, retrieval + generation.
- `worker` — Celery-воркер: парсинг PDF/DOCX, чанкинг, эмбеддинги, запись в Qdrant.
- `bot` — тонкий Telegram-клиент (Aiogram), работает через FastAPI.
- `rabbitmq` — брокер очередей.
- `redis` — backend статусов задач.
- `qdrant` — векторная БД для гибридного поиска.

## Быстрый запуск

1. Создай файл `.env` в корне и укажи минимум:

```env
GROQ_API_KEY=your_key
TELEGRAM_BOT_TOKEN=your_bot_token
BOT_TOKEN=your_bot_token
RABBITMQ_DEFAULT_USER=guest
RABBITMQ_DEFAULT_PASS=guest
```

2. Запусти платформу:

```bash
make up
```

3. Запусти smoke-check:

```bash
python tests/smoke_test.py
```

## Команды управления

```bash
make up       # docker compose up -d --build
make down     # docker compose down -v
make logs     # docker compose logs -f
make restart  # перезапуск контейнеров
```

## Тестирование

```bash
PYTHONPATH=services/api pytest -q services/api/tests
pytest -q tests  # compatibility (legacy)
```

## Пользовательское руководство

- Русское руководство для сотрудников и операторов: [docs/USER_GUIDE_RU.md](docs/USER_GUIDE_RU.md)
- Короткая памятка для рассылки: [docs/QUICK_MEMO_RU.md](docs/QUICK_MEMO_RU.md)
- Памятка сотрудников с лимитами и нагрузкой: [docs/STAFF_MEMO_RU.md](docs/STAFF_MEMO_RU.md)
- Итог готовности к продакшену: [docs/PRODUCTION_READINESS_RU.md](docs/PRODUCTION_READINESS_RU.md)

