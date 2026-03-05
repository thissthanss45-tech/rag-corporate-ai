# SLO / SLI

## Цели сервиса

- Availability (bot + api metrics): **99.9%** в месяц
- RAG latency p95: **< 8s**
- RAG error ratio: **< 2%**
- Индексация p95: **< 120s**

## SLI и источники

- Availability: `min(up{job=~"rag-bot|rag-api"})`
- Error ratio: `sum(increase(rag_requests_total{job="rag-api",status="error",endpoint!="/metrics",endpoint!="/api/v1/health"}[10m])) / sum(increase(rag_requests_total{job="rag-api",endpoint!="/metrics",endpoint!="/api/v1/health"}[10m]))`
- Latency p95: `histogram_quantile(0.95, sum(rate(rag_request_duration_seconds_bucket{job="rag-api",endpoint!="/metrics",endpoint!="/api/v1/health"}[10m])) by (le))`
- Index latency p95: `histogram_quantile(0.95, sum(rate(index_build_duration_seconds_bucket[30m])) by (le))`

## Политика алертов

- Critical: доступность или error ratio
- Warning: деградация latency/index
- Повтор уведомления: каждые 2 часа

## Операционная верификация

- Нагрузочный SLO-check: `make slo-load`
- Backup drill Qdrant: `make backup-drill`

Оба шага должны проходить после критичных изменений в API/поиске/инфраструктуре.
