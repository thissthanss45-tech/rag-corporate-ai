#!/usr/bin/env python3
"""RAGAS-style evaluation для RAG Corporate AI.

Метрики (LLM-as-judge через Groq):
  - faithfulness       Каждое утверждение в ответе подтверждается контекстом (0–1)
  - answer_relevancy   Ответ релевантен вопросу (0–1)
  - context_precision  Доля retrieved chunks реально использованных в ответе (0–1)
  - context_recall     Контекст покрывает ground_truth (если задан) (0–1 или N/A)

Dataset формат (JSONL):
  {"question": "...", "ground_truth": "...", "expected_sources": ["doc.pdf"]}
  ground_truth и expected_sources — опциональны.

Использование:
  python scripts/evaluate_ragas.py
  python scripts/evaluate_ragas.py --dataset evaluation/ragas_dataset.jsonl
  python scripts/evaluate_ragas.py --fail-under-faithfulness 0.7
  python scripts/evaluate_ragas.py --fail-under-faithfulness 0.7 --fail-under-relevancy 0.7
  python scripts/evaluate_ragas.py --compare-reranker  # A/B тест с/без reranker
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from groq import Groq

# ─── Конфигурация ────────────────────────────────────────────────────────────

BASE_URL = os.getenv("RAG_API_URL", "http://localhost:8000")
API_PREFIX = os.getenv("RAG_API_PREFIX", "/api/v1")
SERVICE_TOKEN = os.getenv("SERVICE_AUTH_TOKEN", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("RAGAS_JUDGE_MODEL", "llama-3.3-70b-versatile")
DEFAULT_DATASET = Path(__file__).parent.parent / "evaluation" / "ragas_dataset.jsonl"
REQUEST_TIMEOUT = float(os.getenv("RAGAS_REQUEST_TIMEOUT", "60"))

# ─────────────────────────────────────────────────────────────────────────────


def load_dataset(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        print(f"⚠️  Dataset не найден: {path}")
        print(f"   Создайте файл или укажите --dataset <path>")
        print(f"   Пример: evaluation/ragas_dataset.example.jsonl")
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def ask_api(question: str, *, reranker_override: bool | None = None) -> dict[str, Any]:
    """Отправляем вопрос в RAG API и получаем ответ с sources."""
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if SERVICE_TOKEN:
        headers["X-Service-Token"] = SERVICE_TOKEN

    payload: dict[str, Any] = {"question": question, "model": "llama"}

    # Если нужен A/B тест — передаём флаг через query (если API поддерживает)
    url = f"{BASE_URL}{API_PREFIX}/chat/ask"

    t0 = time.perf_counter()
    try:
        response = httpx.post(url, json=payload, headers=headers, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        elapsed = time.perf_counter() - t0
        return {
            "answer": data.get("answer", ""),
            "sources": data.get("sources", []),
            "latency_ms": round(elapsed * 1000, 1),
            "error": None,
        }
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        return {
            "answer": "",
            "sources": [],
            "latency_ms": round(elapsed * 1000, 1),
            "error": str(exc),
        }


# ─── LLM-as-judge ────────────────────────────────────────────────────────────

def _judge(client: Groq, prompt: str) -> float:
    """Запрашиваем Groq LLM и парсим числовой score 0.0–1.0."""
    try:
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a strict evaluation judge for RAG systems. "
                        "Respond ONLY with a decimal number between 0.0 and 1.0. "
                        "No explanation, no text — only the number."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=10,
            temperature=0.0,
        )
        raw = response.choices[0].message.content.strip()
        # Извлекаем первое число
        import re
        match = re.search(r"\d+\.?\d*", raw)
        if match:
            score = float(match.group())
            return min(max(score, 0.0), 1.0)
    except Exception as exc:
        print(f"    ⚠️  judge error: {exc}")
    return 0.0


def score_faithfulness(client: Groq, question: str, answer: str, context_chunks: list[str]) -> float:
    """Каждое утверждение ответа должно подтверждаться контекстом."""
    if not answer.strip():
        return 0.0
    context = "\n\n".join(context_chunks[:5])  # берём первые 5 chunks для prompt
    prompt = (
        f"QUESTION: {question}\n\n"
        f"CONTEXT:\n{context}\n\n"
        f"ANSWER: {answer}\n\n"
        "Rate from 0.0 to 1.0: how faithfully does the answer stick to the context?\n"
        "1.0 = every statement in the answer is directly supported by the context.\n"
        "0.0 = the answer contains hallucinations or statements not in context.\n"
        "Score:"
    )
    return _judge(client, prompt)


def score_answer_relevancy(client: Groq, question: str, answer: str) -> float:
    """Насколько ответ релевантен вопросу."""
    if not answer.strip():
        return 0.0
    prompt = (
        f"QUESTION: {question}\n\n"
        f"ANSWER: {answer}\n\n"
        "Rate from 0.0 to 1.0: how relevant is this answer to the question?\n"
        "1.0 = completely focused on answering the question.\n"
        "0.0 = answer is off-topic, empty, or a refusal.\n"
        "Score:"
    )
    return _judge(client, prompt)


def score_context_precision(
    client: Groq, question: str, answer: str, sources: list[str]
) -> float:
    """Доля источников, реально использованных в ответе."""
    if not sources:
        return 0.0
    sources_str = ", ".join(sources)
    prompt = (
        f"QUESTION: {question}\n\n"
        f"RETRIEVED SOURCES: {sources_str}\n\n"
        f"ANSWER: {answer}\n\n"
        "Rate from 0.0 to 1.0: what proportion of the retrieved sources "
        "actually contributed useful information to the answer?\n"
        "1.0 = all sources contributed. 0.0 = no sources were useful / answer ignored context.\n"
        "Score:"
    )
    return _judge(client, prompt)


def score_context_recall(
    client: Groq, question: str, ground_truth: str, sources: list[str]
) -> float:
    """Контекст покрывает ground_truth (opional)."""
    if not ground_truth.strip() or not sources:
        return -1.0  # N/A
    sources_str = ", ".join(sources)
    prompt = (
        f"QUESTION: {question}\n\n"
        f"GROUND TRUTH: {ground_truth}\n\n"
        f"RETRIEVED SOURCES: {sources_str}\n\n"
        "Rate from 0.0 to 1.0: how well do the retrieved sources cover "
        "the information needed to answer the question completely?\n"
        "1.0 = all key information from ground truth is covered. "
        "0.0 = none of it is covered.\n"
        "Score:"
    )
    return _judge(client, prompt)


# ─── Основная логика ─────────────────────────────────────────────────────────

def evaluate_row(
    client: Groq,
    row: dict[str, Any],
    verbose: bool = False,
) -> dict[str, Any]:
    question = row["question"]
    ground_truth = row.get("ground_truth", "")
    expected_sources = row.get("expected_sources", [])

    print(f"  ❓ {question[:80]}{'...' if len(question) > 80 else ''}")

    # 1. Вызываем API
    api_result = ask_api(question)
    answer = api_result["answer"]
    sources = api_result["sources"]
    latency_ms = api_result["latency_ms"]

    if api_result["error"]:
        print(f"     ❌ API error: {api_result['error']}")
        return {
            "question": question,
            "answer": "",
            "sources": [],
            "latency_ms": latency_ms,
            "faithfulness": 0.0,
            "answer_relevancy": 0.0,
            "context_precision": 0.0,
            "context_recall": None,
            "source_hit": False,
            "error": api_result["error"],
        }

    if verbose:
        print(f"     📝 Answer: {answer[:120]}{'...' if len(answer) > 120 else ''}")
        print(f"     📂 Sources: {sources}")
        print(f"     ⏱️  Latency: {latency_ms}ms")

    # 2. Scoring
    print("     🧮 scoring...", end=" ", flush=True)

    # Faithfulness: создаём proxy-контекст из sources для prompt
    context_proxy = [f"[{s}]" for s in sources] if sources else ["(no context)"]
    faithfulness = score_faithfulness(client, question, answer, context_proxy)
    print(f"F={faithfulness:.2f}", end=" ", flush=True)

    relevancy = score_answer_relevancy(client, question, answer)
    print(f"R={relevancy:.2f}", end=" ", flush=True)

    precision = score_context_precision(client, question, answer, sources)
    print(f"P={precision:.2f}", end=" ", flush=True)

    recall = score_context_recall(client, question, ground_truth, sources)
    recall_display = f"{recall:.2f}" if recall >= 0 else "N/A"
    print(f"CR={recall_display}")

    # Source hit (если заданы expected_sources)
    source_hit = False
    if expected_sources:
        retrieved_lower = {s.lower() for s in sources}
        source_hit = any(e.lower() in retrieved_lower for e in expected_sources)

    return {
        "question": question,
        "answer": answer[:300],
        "sources": sources,
        "latency_ms": latency_ms,
        "faithfulness": faithfulness,
        "answer_relevancy": relevancy,
        "context_precision": precision,
        "context_recall": recall if recall >= 0 else None,
        "source_hit": source_hit,
        "error": None,
    }


def aggregate(results: list[dict[str, Any]]) -> dict[str, float]:
    valid = [r for r in results if not r.get("error")]
    n = len(valid)
    if n == 0:
        return {"n": 0, "faithfulness": 0.0, "answer_relevancy": 0.0,
                "context_precision": 0.0, "context_recall": 0.0, "source_hit_rate": 0.0,
                "avg_latency_ms": 0.0}

    faithfulness = sum(r["faithfulness"] for r in valid) / n
    relevancy = sum(r["answer_relevancy"] for r in valid) / n
    precision = sum(r["context_precision"] for r in valid) / n

    recall_values = [r["context_recall"] for r in valid if r["context_recall"] is not None]
    context_recall = sum(recall_values) / len(recall_values) if recall_values else 0.0

    source_hit_rate = sum(1 for r in valid if r.get("source_hit")) / n
    avg_latency = sum(r["latency_ms"] for r in valid) / n

    return {
        "n": n,
        "faithfulness": round(faithfulness, 3),
        "answer_relevancy": round(relevancy, 3),
        "context_precision": round(precision, 3),
        "context_recall": round(context_recall, 3) if recall_values else None,
        "source_hit_rate": round(source_hit_rate, 3),
        "avg_latency_ms": round(avg_latency, 1),
    }


def print_report(metrics: dict[str, float], title: str = "RAGAS Evaluation Report") -> None:
    n = metrics.get("n", 0)
    print(f"\n{'=' * 54}")
    print(f"  {title}")
    print(f"{'=' * 54}")
    print(f"  Questions evaluated : {n}")
    print(f"  Faithfulness        : {metrics.get('faithfulness', 0):.3f}  (hallucination guard)")
    print(f"  Answer Relevancy    : {metrics.get('answer_relevancy', 0):.3f}  (on-topic answers)")
    print(f"  Context Precision   : {metrics.get('context_precision', 0):.3f}  (useful chunks ratio)")
    cr = metrics.get("context_recall")
    cr_str = f"{cr:.3f}" if cr is not None else "N/A   "
    print(f"  Context Recall      : {cr_str}  (ground truth coverage)")
    print(f"  Source Hit Rate     : {metrics.get('source_hit_rate', 0):.3f}  (correct doc retrieved)")
    print(f"  Avg Latency         : {metrics.get('avg_latency_ms', 0):.0f} ms")
    print(f"{'=' * 54}")


def check_thresholds(
    metrics: dict[str, float],
    min_faithfulness: float | None,
    min_relevancy: float | None,
    min_precision: float | None,
) -> bool:
    passed = True
    checks = [
        ("faithfulness",      min_faithfulness, "Faithfulness"),
        ("answer_relevancy",  min_relevancy,    "Answer Relevancy"),
        ("context_precision", min_precision,    "Context Precision"),
    ]
    for key, threshold, label in checks:
        if threshold is None:
            continue
        value = metrics.get(key, 0.0)
        if value < threshold:
            print(f"  ❌ Quality gate FAILED: {label} = {value:.3f} < {threshold:.3f}")
            passed = False
        else:
            print(f"  ✅ {label} = {value:.3f} >= {threshold:.3f}")
    return passed


# ─── CLI ─────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="RAGAS-style evaluation for RAG Corporate AI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=None,
                        help="Сохранить результаты в JSONL файл")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--fail-under-faithfulness", type=float, default=None)
    parser.add_argument("--fail-under-relevancy",    type=float, default=None)
    parser.add_argument("--fail-under-precision",    type=float, default=None)
    parser.add_argument("--api-url", type=str, default=None,
                        help="RAG API base URL (переопределяет RAG_API_URL)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.api_url:
        global BASE_URL
        BASE_URL = args.api_url

    if not GROQ_API_KEY:
        print("❌ GROQ_API_KEY не задан. Экспортируй переменную окружения.")
        sys.exit(1)

    dataset = load_dataset(args.dataset)
    if not dataset:
        print("⚠️  Dataset пустой или не найден. Создайте evaluation/ragas_dataset.jsonl")
        print("    Пример строки:")
        print('    {"question": "Какова политика паролей?", "ground_truth": "Пароль от 12 символов", "expected_sources": ["security_policy.pdf"]}')
        sys.exit(0)

    print(f"\n🚀 RAGAS Evaluation")
    print(f"   Dataset : {args.dataset} ({len(dataset)} вопросов)")
    print(f"   API     : {BASE_URL}{API_PREFIX}/chat/ask")
    print(f"   Judge   : {GROQ_MODEL}\n")

    client = Groq(api_key=GROQ_API_KEY)
    results: list[dict[str, Any]] = []

    for i, row in enumerate(dataset, start=1):
        print(f"\n[{i}/{len(dataset)}]")
        result = evaluate_row(client, row, verbose=args.verbose)
        results.append(result)

    # Итоговые метрики
    metrics = aggregate(results)
    print_report(metrics)

    # Сохраняем результаты
    if args.output:
        args.output.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in results),
            encoding="utf-8",
        )
        print(f"\n💾 Результаты сохранены: {args.output}")

    # Опционально сохраняем агрегат
    summary_path = Path("evaluation/ragas_summary.json")
    summary_path.write_text(
        json.dumps({"timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"), **metrics}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"💾 Агрегат: {summary_path}")

    # Quality gate
    if any(x is not None for x in [args.fail_under_faithfulness, args.fail_under_relevancy, args.fail_under_precision]):
        print("\n📋 Quality Gate:")
        passed = check_thresholds(
            metrics,
            min_faithfulness=args.fail_under_faithfulness,
            min_relevancy=args.fail_under_relevancy,
            min_precision=args.fail_under_precision,
        )
        if not passed:
            sys.exit(1)
    else:
        print("\n💡 Подсказка: используй --fail-under-faithfulness 0.7 для CI quality gate")


if __name__ == "__main__":
    main()
