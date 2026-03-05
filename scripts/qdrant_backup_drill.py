#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request


def request_json(url: str, method: str = "GET", payload: dict | None = None, timeout: float = 10.0) -> dict:
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url=url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        body = response.read().decode("utf-8", errors="ignore")
        if not body:
            return {}
        return json.loads(body)


def collection_exists(base_url: str, collection: str) -> bool:
    try:
        _ = request_json(f"{base_url}/collections/{collection}")
        return True
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        raise


def list_snapshots(base_url: str, collection: str) -> list[dict]:
    payload = request_json(f"{base_url}/collections/{collection}/snapshots")
    result = payload.get("result", [])
    return result if isinstance(result, list) else []


def create_snapshot(base_url: str, collection: str) -> str:
    payload = request_json(f"{base_url}/collections/{collection}/snapshots", method="POST", payload={})
    result = payload.get("result", {})
    if isinstance(result, dict):
        name = result.get("name")
        if isinstance(name, str) and name:
            return name
    raise RuntimeError(f"Could not parse snapshot name from response: {payload}")


def delete_snapshot(base_url: str, collection: str, snapshot_name: str) -> None:
    _ = request_json(f"{base_url}/collections/{collection}/snapshots/{snapshot_name}", method="DELETE")


def wait_for_snapshot(base_url: str, collection: str, snapshot_name: str, timeout_sec: int = 60) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        snapshots = list_snapshots(base_url, collection)
        if any(item.get("name") == snapshot_name for item in snapshots if isinstance(item, dict)):
            return True
        time.sleep(2)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Qdrant backup drill: create and optionally cleanup a snapshot")
    parser.add_argument("--qdrant-url", default=os.getenv("QDRANT_URL", "http://localhost:6333"))
    parser.add_argument("--collection", default=os.getenv("QDRANT_COLLECTION_NAME", "documents_chunks"))
    parser.add_argument("--cleanup", action="store_true", help="Delete test snapshot after validation")
    args = parser.parse_args()

    base_url = args.qdrant_url.rstrip("/")
    collection = args.collection

    if not collection_exists(base_url, collection):
        print(json.dumps({
            "backup_drill_passed": False,
            "reason": "collection_not_found",
            "collection": collection,
        }, ensure_ascii=False, indent=2))
        return 1

    snapshot_name = create_snapshot(base_url, collection)
    present = wait_for_snapshot(base_url, collection, snapshot_name)
    if not present:
        print(json.dumps({
            "backup_drill_passed": False,
            "reason": "snapshot_not_visible_after_create",
            "collection": collection,
            "snapshot": snapshot_name,
        }, ensure_ascii=False, indent=2))
        return 1

    cleanup_done = False
    if args.cleanup:
        delete_snapshot(base_url, collection, snapshot_name)
        cleanup_done = True

    print(json.dumps({
        "backup_drill_passed": True,
        "collection": collection,
        "snapshot": snapshot_name,
        "cleanup_done": cleanup_done,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
