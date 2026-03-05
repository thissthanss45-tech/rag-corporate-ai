import os
import sys
import time
from pathlib import Path


HEARTBEAT_FILE = Path(os.getenv("HEARTBEAT_FILE", "/tmp/rag_bot_heartbeat"))
MAX_AGE_SEC = int(os.getenv("HEALTHCHECK_MAX_AGE_SEC", "90"))


def main() -> int:
    if not HEARTBEAT_FILE.exists():
        print(f"heartbeat file not found: {HEARTBEAT_FILE}")
        return 1

    age = time.time() - HEARTBEAT_FILE.stat().st_mtime
    if age > MAX_AGE_SEC:
        print(f"heartbeat stale: age={age:.1f}s > {MAX_AGE_SEC}s")
        return 1

    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
