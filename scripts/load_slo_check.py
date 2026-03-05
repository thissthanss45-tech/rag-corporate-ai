#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    k = (len(values) - 1) * p
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return values[int(k)]
    d0 = values[f] * (c - k)
    d1 = values[c] * (k - f)
    return d0 + d1


def make_health_request(url: str, timeout_sec: float) -> tuple[bool, float, int, str]:
    started = time.perf_counter()
    req = urllib.request.Request(url=url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as response:
            _ = response.read()
            elapsed = time.perf_counter() - started
            return 200 <= response.status < 300, elapsed, response.status, ""
    except urllib.error.HTTPError as exc:
        elapsed = time.perf_counter() - started
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="ignore")[:300]
        except Exception:
            detail = str(exc)
        return False, elapsed, exc.code, detail
    except Exception as exc:
        elapsed = time.perf_counter() - started
        return False, elapsed, 0, str(exc)


def main() -> int:
    parser = argparse.ArgumentParser(description="Simple load check for API SLO validation")
    parser.add_argument("--base-url", default=os.getenv("API_BASE_URL", "http://localhost:8000"))
    parser.add_argument("--api-prefix", default=os.getenv("API_PREFIX", "/api/v1"))
    parser.add_argument("--total-requests", type=int, default=300)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--timeout-sec", type=float, default=5.0)
    parser.add_argument("--max-error-ratio", type=float, default=0.02)
    parser.add_argument("--max-p95-sec", type=float, default=8.0)
    args = parser.parse_args()

    if args.total_requests <= 0 or args.concurrency <= 0:
        raise SystemExit("total-requests and concurrency must be > 0")

    url = f"{args.base_url.rstrip('/')}{args.api_prefix.rstrip('/')}/health"

    latencies: list[float] = []
    errors = 0
    error_samples: list[dict[str, str | int]] = []

    started_all = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = [executor.submit(make_health_request, url, args.timeout_sec) for _ in range(args.total_requests)]
        for fut in as_completed(futures):
            ok, elapsed, code, detail = fut.result()
            latencies.append(elapsed)
            if not ok:
                errors += 1
                if len(error_samples) < 5:
                    error_samples.append({"status": code, "detail": detail})

    total_elapsed = time.perf_counter() - started_all
    latencies.sort()

    success = args.total_requests - errors
    error_ratio = errors / args.total_requests
    p95 = percentile(latencies, 0.95)
    p50 = percentile(latencies, 0.50)
    rps = args.total_requests / total_elapsed if total_elapsed > 0 else 0.0

    report = {
        "url": url,
        "total_requests": args.total_requests,
        "concurrency": args.concurrency,
        "success": success,
        "errors": errors,
        "error_ratio": round(error_ratio, 6),
        "latency_sec": {
            "p50": round(p50, 4),
            "p95": round(p95, 4),
            "max": round(max(latencies) if latencies else 0.0, 4),
            "mean": round(statistics.fmean(latencies) if latencies else 0.0, 4),
        },
        "throughput_rps": round(rps, 2),
        "thresholds": {
            "max_error_ratio": args.max_error_ratio,
            "max_p95_sec": args.max_p95_sec,
        },
        "error_samples": error_samples,
    }

    passed = (error_ratio <= args.max_error_ratio) and (p95 <= args.max_p95_sec)
    report["slo_passed"] = passed

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
