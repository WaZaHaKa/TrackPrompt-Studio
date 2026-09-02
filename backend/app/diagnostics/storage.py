from __future__ import annotations

import json
from pathlib import Path

from ..catalog.store import CatalogueStore
from ..config import Settings


def _size(root: Path) -> int:
    try:
        return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())
    except OSError:
        return 0


def main() -> int:
    settings = Settings.from_env()
    store = CatalogueStore(settings)
    with store.connect() as connection:
        referenced = {
            str(row[0]) for row in connection.execute("SELECT storage_key FROM archive_blobs")
        } | {str(row[0]) for row in connection.execute("SELECT storage_key FROM artifacts")}
    actual = {
        path.relative_to(settings.archive_dir).as_posix()
        for path in settings.archive_dir.rglob("*")
        if path.is_file()
    }
    output = {
        "freeBytes": store.free_storage_bytes(),
        "archiveBytes": _size(settings.archive_dir),
        "temporaryJobBytes": _size(settings.jobs_dir),
        "partialUploadBytes": _size(settings.uploads_dir),
        "modelBytes": _size(settings.model_cache_dir),
        "archiveQuotaBytes": settings.max_archive_bytes,
        "minimumFreeDiskBytes": settings.minimum_free_disk_bytes,
        "orphanCount": len(actual - referenced),
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

