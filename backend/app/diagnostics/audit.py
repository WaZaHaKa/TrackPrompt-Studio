from __future__ import annotations

import json

from ..catalog.store import CatalogueStore
from ..config import Settings


def main() -> int:
    store = CatalogueStore(Settings.from_env())
    with store.connect() as connection:
        project_ids = [str(row[0]) for row in connection.execute("SELECT id FROM projects")]
        artifact_hashes = {str(row[0]) for row in connection.execute("SELECT sha256 FROM artifacts")}
        revision_hashes = {str(row[0]) for row in connection.execute("SELECT artifact_sha256 FROM revisions")}
    checks = [store.verify_audit(project_id) for project_id in project_ids]
    output = {
        "projectCount": len(project_ids),
        "eventCount": sum(int(item["event_count"]) for item in checks),
        "validProjectChains": sum(1 for item in checks if item["valid"]),
        "invalidProjectChains": sum(1 for item in checks if not item["valid"]),
        "missingArtifactReferences": len(revision_hashes - artifact_hashes),
        "invalidRevisionLinks": 0,
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0 if output["invalidProjectChains"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

