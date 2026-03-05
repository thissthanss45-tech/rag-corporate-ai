from __future__ import annotations

import time
from pathlib import Path

from app.config import settings


def cleanup_folder(folder: str, retention_days: int) -> int:
    root = Path(folder)
    if not root.exists():
        return 0

    threshold = time.time() - retention_days * 24 * 60 * 60
    deleted = 0

    for file_path in root.iterdir():
        if not file_path.is_file():
            continue
        if file_path.stat().st_mtime < threshold:
            file_path.unlink(missing_ok=True)
            deleted += 1

    return deleted


def main() -> None:
    retention_days = settings.RETENTION_DAYS
    data_deleted = cleanup_folder(settings.DATA_PATH, retention_days)
    print(f"✅ Retention cleanup done. Removed files from data/: {data_deleted}")


if __name__ == "__main__":
    main()
