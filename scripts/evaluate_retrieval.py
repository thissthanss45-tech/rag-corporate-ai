import json
import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


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


def evaluate(dataset: list[dict], top_k: int) -> dict[str, float]:
    from app.retrieval.search import SearchEngine

    search_engine = SearchEngine()

    total = len(dataset)
    if total == 0:
        return {"total": 0.0, "recall_at_k": 0.0, "mrr": 0.0}

    hits = 0
    reciprocal_rank_sum = 0.0

    for row in dataset:
        question = row["question"]
        expected = {value.lower() for value in row.get("expected_sources", [])}

        retrieved = search_engine.search_with_meta(question, top_k=top_k)
        retrieved_sources = [str(item.get("source", "")).lower() for item in retrieved]

        rank = None
        for index, source in enumerate(retrieved_sources, start=1):
            if source in expected:
                rank = index
                break

        if rank is not None:
            hits += 1
            reciprocal_rank_sum += 1.0 / rank

    return {
        "total": float(total),
        "recall_at_k": hits / total,
        "mrr": reciprocal_rank_sum / total,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate retrieval quality")
    parser.add_argument(
        "--fail-under-recall",
        type=float,
        default=None,
        help="Fail with exit code 1 if Recall@K is below this value",
    )
    parser.add_argument(
        "--require-dataset",
        action="store_true",
        help="Fail if dataset file is missing or empty",
    )
    return parser.parse_args()


def main() -> None:
    from app.config import settings

    args = parse_args()

    dataset_path = Path(settings.EVAL_DATASET_PATH)
    dataset = load_dataset(dataset_path)

    if not dataset:
        message = (
            f"⚠️ Dataset not found or empty: {dataset_path}. "
            "Create a JSONL dataset to run evaluation."
        )
        print(message)
        if args.require_dataset or settings.EVAL_FAIL_ON_MISSING_DATASET:
            raise SystemExit(1)
        return

    result = evaluate(dataset, top_k=settings.TOP_K)
    print("=== Retrieval Evaluation ===")
    print(f"Dataset size: {int(result['total'])}")
    print(f"Recall@{settings.TOP_K}: {result['recall_at_k']:.3f}")
    print(f"MRR: {result['mrr']:.3f}")

    threshold = args.fail_under_recall
    if threshold is None:
        threshold = settings.EVAL_MIN_RECALL_AT_K
    if result["recall_at_k"] < threshold:
        print(
            f"❌ Quality gate failed: Recall@{settings.TOP_K}={result['recall_at_k']:.3f} < {threshold:.3f}"
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
