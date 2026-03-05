# Итог готовности к продакшену (10/10)

Дата фиксации: 2026-02-27

## 1) Статус
Система готова к промышленной эксплуатации.

Ключевые критерии закрыты:
- функциональность RAG и ingest-пайплайна;
- безопасность API и валидация входных данных;
- наблюдаемость (метрики, алерты, дашборды);
- эксплуатационные проверки (load SLO check, backup drill);
- runbook и release-процедуры.

## 2) Что проверено фактически

### Стабильность API
- `GET /api/v1/health` возвращает `status=ok`.
- Проверки зависимостей: Qdrant, RabbitMQ, disk.

### Нагрузочный SLO-check
Команда:
- `python3 scripts/load_slo_check.py --base-url http://localhost:8000 --api-prefix /api/v1 --total-requests 120 --concurrency 12 --max-error-ratio 0.02 --max-p95-sec 8.0`

Результат:
- `success=120/120`
- `error_ratio=0.0`
- `p95=0.0985s`
- `slo_passed=true`

### Backup drill Qdrant
Команда:
- `python3 scripts/qdrant_backup_drill.py --qdrant-url http://localhost:6333 --collection documents_chunks --cleanup`

Результат:
- snapshot успешно создан,
- snapshot виден через API,
- test snapshot удалён (`cleanup_done=true`).

### Пользовательские сценарии
- загрузка TXT/XLSX/PDF/DOCX,
- статус обработки queued/processing/completed,
- вопрос по документам,
- голосовой вопрос (OGG),
- выбор модели ответа: Llama / DeepSeek,
- смена модели отдельной кнопкой в главном меню.

## 3) Операционные команды
- `make smoke-release`
- `make slo-load`
- `make backup-drill`
- `make monitoring-reload`

## 4) Регулярный регламент
- Еженедельно: `make backup-drill`.
- После важных изменений: `make slo-load`.
- Перед релизом: `make quality-gate` (проверка фактических ответов `/chat/ask` по вашему датасету).
- Перед релизом: `make smoke-release`.
- Раз в 30–60 дней: ротация `SERVICE_AUTH_TOKEN`, `GROQ_API_KEY`, `DEEPSEEK_API_KEY`.

## 5) Критерии “не выпускать в прод”
Не выпускать релиз, если:
- не проходит `smoke-release`;
- не проходит `quality-gate`;
- не проходит `slo-load`;
- не проходит `backup-drill`;
- `health` API не `ok`.

## 6) Защита ветки (обязательно)
Для ветки релизов должна быть включена Branch Protection с required checks:
- `pre-merge-quality-gate`
- `lint-and-test`

Применить можно командой:
- `GH_REPO=owner/repo GH_BRANCH=main make branch-protect`
