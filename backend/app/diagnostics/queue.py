from __future__ import annotations

import json

from ..catalog.store import CatalogueStore
from ..config import Settings


def main() -> int:
    store = CatalogueStore(Settings.from_env())
    with store.connect() as connection:
        counts = {
            str(row["state"]): int(row["count"])
            for row in connection.execute(
                "SELECT state, COUNT(*) AS count FROM queue_items GROUP BY state"
            )
        }
        paused = int(
            connection.execute("SELECT COUNT(*) FROM batches WHERE state = 'paused'").fetchone()[0]
        )
        active_uploads = int(
            connection.execute(
                "SELECT COUNT(*) FROM upload_sessions WHERE state IN ('created','uploading','verifying')"
            ).fetchone()[0]
        )
    output = {
        "storedPendingItems": counts.get("stored", 0) + counts.get("queued", 0),
        "activeUploads": active_uploads,
        "activeAnalyses": counts.get("running", 0),
        "activeGpuTasks": "reported live by /api/capabilities",
        "pausedBatches": paused,
        "restartRecoveryState": "running items are transactionally returned to queued on startup",
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

