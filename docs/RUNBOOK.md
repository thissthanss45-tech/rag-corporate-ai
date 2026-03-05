# Incident Runbook

## 1) Бот не отвечает

1. Проверить контейнер: `docker compose ps`
2. Проверить health: `docker inspect --format='{{json .State.Health}}' rag-bot-client`
3. Проверить логи: `docker compose logs -f bot`
4. Перезапуск: `docker compose restart bot`

Дополнительно:
- Проверить API-метрики: `curl -s http://localhost:8000/metrics | head`
- Убедиться, что растут `rag_requests_total` и `rag_request_duration_seconds`
- Проверить scrape-цели: `curl -s http://localhost:9090/api/v1/targets | jq '.data.activeTargets[] | {job: .labels.job, health: .health}'`

## 2) Ошибки LLM/Groq

1. Проверить доступность API и валидность `GROQ_API_KEY`
2. Проверить rate limits в логах
3. Временно уменьшить нагрузку (снизить `TOP_K`)

## 3) Поиск не находит документы

1. Проверить наличие индекса: `indices/faiss_store.index`
2. Пересобрать индекс: `python -m app.main build-index`
3. Проверить метрики индексации и алерты Prometheus

## 4) Медленные ответы

1. Проверить p95 в Grafana
2. Проверить загрузку CPU/RAM и размер индекса
3. Оптимизировать: уменьшить `TOP_K`, увеличить chunk size, пересобрать индекс
4. Проверить алерт `RAGAPISlowResponsesP95` и распределение latency по endpoint в Grafana

Если меняли `CHUNK_SIZE/CHUNK_OVERLAP` для глубокого чтения длинных документов, выполнить переиндексацию текущего корпуса:
- `make reindex-corpus`

## 5) Rollback

1. Откатить образ/коммит
2. Восстановить предыдущий индекс из backup
3. Проверить healthcheck и основные smoke-сценарии

## 6) Нагрузочная проверка SLO

Перед релизом (или после изменений производительности) выполнить:

- `make slo-load`

Критерии прохождения:
- `error_ratio <= 0.02`
- `p95 <= 8s`

Если не проходит:
1. Проверить `RAGAPISlowResponsesP95` и `RAGAPIHighErrorRate` в Grafana/Alertmanager.
2. Увеличить `UVICORN_WORKERS` / `WORKER_CONCURRENCY` в `.env`.
3. Повторить `make slo-load`.

## 7) Проверка backup drill (Qdrant)

Еженедельно выполнять:

- `make backup-drill`

Что проверяет:
- создание snapshot коллекции,
- доступность snapshot через API Qdrant,
- cleanup test snapshot (чтобы не копить мусор).

## 8) Ротация секретов

Ротация каждые 30–60 дней:
- `SERVICE_AUTH_TOKEN`
- `GROQ_API_KEY`
- `DEEPSEEK_API_KEY`

Порядок:
1. Обновить ключи в `.env`.
2. Перезапустить сервисы: `docker compose up -d --build api bot`.
3. Проверить доступность: `curl -s http://localhost:8000/api/v1/health`.
4. Проверить функционал: загрузка документа + `🧠 Задать вопрос` в Telegram.

### Release smoke checklist

Перед релизом убедиться, что проходит:
- `python scripts/smoke_checks.py --mode release`
- `make quality-gate` (использует `evaluation/api_quality.jsonl`)
- `python -m pytest -q services/api/tests`

### Branch protection (обязательные проверки для PR/merge)

Обязательные GitHub checks для защищённой ветки:
- `pre-merge-quality-gate`
- `lint-and-test`

Автоприменение (нужен `gh auth login`):
- `GH_REPO=owner/repo GH_BRANCH=main make branch-protect`

### Monitoring config rollout (без рестарта)

1. Проверить compose-конфиги:
	- `docker compose -f docker-compose.monitoring.yml config`
2. Перезагрузить правила/роутинг:
	- `bash scripts/reload_monitoring.sh`
3. Проверить активные алерты и цели:
	- `curl -s http://localhost:9090/api/v1/rules | jq '.status'`
	- `curl -s http://localhost:9090/api/v1/targets | jq '.status'`
