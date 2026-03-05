.PHONY: up down logs restart monitoring-up monitoring-down monitoring-reload smoke-release slo-load backup-drill quality-gate branch-protect reindex-corpus ragas ragas-ci ragas-compare

up:
	docker compose up -d --build

down:
	docker compose down -v

logs:
	docker compose logs -f

restart:
	docker compose down && docker compose up -d --build

monitoring-up:
	docker compose -f docker-compose.monitoring.yml up -d

monitoring-down:
	docker compose -f docker-compose.monitoring.yml down -v

monitoring-reload:
	bash scripts/reload_monitoring.sh

smoke-release:
	python3 scripts/smoke_checks.py --mode release

slo-load:
	python3 scripts/load_slo_check.py --base-url http://localhost:8000 --api-prefix /api/v1 --total-requests 300 --concurrency 20 --max-error-ratio 0.02 --max-p95-sec 8.0

backup-drill:
	python3 scripts/qdrant_backup_drill.py --qdrant-url http://localhost:6333 --collection documents_chunks --cleanup

quality-gate:
	python3 scripts/api_quality_gate.py --require-dataset

branch-protect:
	bash scripts/setup_branch_protection.sh

reindex-corpus:
	docker compose exec -T worker python -m app.reindex_corpus --chunk-size $${CHUNK_SIZE:-1200} --chunk-overlap $${CHUNK_OVERLAP:-200} --confirm

# ── RAGAS Evaluation ─────────────────────────────────────────────────────
# Требует: запущенный API (make up / docker compose up)
# Базовый запуск
# 
ragas:
	python3 scripts/evaluate_ragas.py --verbose

# RAGAS с quality gate (для CI)
ragas-ci:
	python3 scripts/evaluate_ragas.py \
		--fail-under-faithfulness $${RAGAS_MIN_FAITHFULNESS:-0.65} \
		--fail-under-relevancy    $${RAGAS_MIN_RELEVANCY:-0.65} \
		--fail-under-precision    $${RAGAS_MIN_PRECISION:-0.50} \
		--output evaluation/ragas_results_$$(date +%Y%m%d_%H%M%S).jsonl

# A/B сравнение reranker vs no-reranker
ragas-compare:
	@echo "▶ Run WITH reranker (RERANKER_ENABLED=true)"
	RERANKER_ENABLED=true python3 scripts/evaluate_ragas.py \
		--output evaluation/ragas_with_reranker.jsonl 2>&1 | tee /tmp/ragas_with.log
	@echo "▶ Run WITHOUT reranker (RERANKER_ENABLED=false)"
	RERANKER_ENABLED=false python3 scripts/evaluate_ragas.py \
		--output evaluation/ragas_without_reranker.jsonl 2>&1 | tee /tmp/ragas_without.log
	@echo ""
	@echo "📊 -- A/B Result Summary --"
	@echo "✔  WITH reranker:" && tail -n 15 /tmp/ragas_with.log | grep -E "Faithfulness|Relevancy|Precision|Recall"
	@echo "✔  WITHOUT reranker:" && tail -n 15 /tmp/ragas_without.log | grep -E "Faithfulness|Relevancy|Precision|Recall"
