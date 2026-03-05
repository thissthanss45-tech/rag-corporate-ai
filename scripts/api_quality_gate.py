#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import time
import urllib.error
import urllib.request
from pathlib import Path


def load_dataset(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def ask_api(base_url: str, api_prefix: str, token: str, question: str, model: str, timeout_sec: float) -> tuple[str, float]:
    payload = {"question": question, "model": model}
    url = f"{base_url.rstrip('/')}{api_prefix.rstrip('/')}/chat/ask"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Service-Token": token,
            "X-Client-Id": "quality-gate",
        },
        method="POST",
    )

    started = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout_sec) as response:
        data = json.loads(response.read().decode("utf-8"))
    elapsed = time.perf_counter() - started
    return str(data.get("answer", "")), elapsed


def normalize(text: str) -> str:
    return " ".join(text.lower().split())


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-zа-яё0-9-]+", normalize(text), flags=re.IGNORECASE)


def _stem(token: str) -> str:
    return token[:5] if len(token) > 5 else token


def _token_matches(answer_token: str, keyword_token: str) -> bool:
    if answer_token == keyword_token:
        return True

    answer_stem = _stem(answer_token)
    keyword_stem = _stem(keyword_token)
    if answer_stem == keyword_stem:
        return True

    answer_root = answer_token[:4] if len(answer_token) >= 4 else answer_token
    keyword_root = keyword_token[:4] if len(keyword_token) >= 4 else keyword_token
    if answer_root and keyword_root and (answer_root == keyword_root):
        return True

    return answer_stem.startswith(keyword_root) or keyword_stem.startswith(answer_root)


def keyword_in_answer(answer: str, keyword: str) -> bool:
    answer_norm = normalize(answer)
    keyword_norm = normalize(keyword)
    if keyword_norm in answer_norm:
        return True

    answer_tokens = _tokenize(answer_norm)
    keyword_tokens = _tokenize(keyword_norm)
    if not keyword_tokens:
        return False

    for token in keyword_tokens:
        if not any(_token_matches(answer_token, token) for answer_token in answer_tokens):
            return False
    return True


def evaluate(dataset: list[dict], base_url: str, api_prefix: str, token: str, model: str, timeout_sec: float) -> dict:
    if not dataset:
        return {
            "total": 0,
            "passed": 0,
            "failed": 0,
            "pass_rate": 0.0,
            "p95_sec": 0.0,
            "avg_sec": 0.0,
            "samples": [],
        }

    latencies: list[float] = []
    passed = 0
    failed = 0
    samples: list[dict] = []

    for row in dataset:
        question = str(row.get("question", "")).strip()
        expected_keywords = [str(item).strip() for item in row.get("expected_keywords", []) if str(item).strip()]
        if not question or not expected_keywords:
            continue

        try:
            answer, elapsed = ask_api(base_url, api_prefix, token, question, model, timeout_sec)
        except urllib.error.HTTPError as exc:
            failed += 1
            if len(samples) < 8:
                samples.append({
                    "question": question,
                    "status": exc.code,
                    "ok": False,
                    "reason": "http_error",
                })
            continue
        except Exception as exc:
            failed += 1
            if len(samples) < 8:
                samples.append({
                    "question": question,
                    "status": 0,
                    "ok": False,
                    "reason": f"exception: {exc}",
                })
            continue

        latencies.append(elapsed)
        missing = [kw for kw in expected_keywords if not keyword_in_answer(answer, kw)]
        ok = len(missing) == 0
        if ok:
            passed += 1
        else:
            failed += 1

        if len(samples) < 8:
            samples.append(
                {
                    "question": question,
                    "ok": ok,
                    "missing": missing,
                    "latency_sec": round(elapsed, 3),
                }
            )

    total = passed + failed
    if latencies:
        sorted_lat = sorted(latencies)
        idx = int(round(0.95 * (len(sorted_lat) - 1)))
        p95 = sorted_lat[idx]
        avg = statistics.fmean(latencies)
    else:
        p95 = 0.0
        avg = 0.0

    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "pass_rate": (passed / total) if total else 0.0,
        "p95_sec": p95,
        "avg_sec": avg,
        "samples": samples,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="API quality gate for /chat/ask")
    parser.add_argument("--dataset", default=os.getenv("API_QG_DATASET", "evaluation/api_quality.jsonl"))
    parser.add_argument("--base-url", default=os.getenv("API_BASE_URL", "http://localhost:8000"))
    parser.add_argument("--api-prefix", default=os.getenv("API_PREFIX", "/api/v1"))
    parser.add_argument("--service-token", default=os.getenv("SERVICE_AUTH_TOKEN", "change-me-in-prod"))
    parser.add_argument("--model", default=os.getenv("API_QG_MODEL", "llama"))
    parser.add_argument("--timeout-sec", type=float, default=float(os.getenv("API_QG_TIMEOUT_SEC", "120")))
    parser.add_argument("--min-pass-rate", type=float, default=float(os.getenv("API_QG_MIN_PASS_RATE", "0.85")))
    parser.add_argument("--max-p95-sec", type=float, default=float(os.getenv("API_QG_MAX_P95_SEC", "20")))
    parser.add_argument("--require-dataset", action="store_true")
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    dataset = load_dataset(dataset_path)
    if not dataset:
        print(f"⚠️ Dataset missing or empty: {dataset_path}")
        return 1 if args.require_dataset else 0

    report = evaluate(
        dataset=dataset,
        base_url=args.base_url,
        api_prefix=args.api_prefix,
        token=args.service_token,
        model=args.model,
        timeout_sec=args.timeout_sec,
    )

    print(json.dumps(report, ensure_ascii=False, indent=2))

    pass_rate_ok = report["pass_rate"] >= args.min_pass_rate
    p95_ok = report["p95_sec"] <= args.max_p95_sec
    if pass_rate_ok and p95_ok:
        return 0

    print(
        f"❌ quality gate failed: pass_rate={report['pass_rate']:.3f} (need >= {args.min_pass_rate:.3f}), "
        f"p95={report['p95_sec']:.3f}s (need <= {args.max_p95_sec:.3f}s)"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
