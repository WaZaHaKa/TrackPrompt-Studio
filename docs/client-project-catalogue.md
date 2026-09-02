# Client and project catalogue

TrackPrompt’s professional workspace is a private local catalogue with first-class clients, projects, batches/sets, source assets, virtual segments, artifacts, revisions, and audit events. The public catalogue schema is versioned independently from ordinary analysis and is `1.0.0`. The transactional SQLite migration level is `3`: level 1 creates catalogue entities, level 2 adds content-free project-deletion tombstones, and level 3 adds durable long-form segmentation jobs.

Projects choose one of three explicit retention policies:

- `temporary`: project-managed short-lived work, still requiring an explicit project action;
- `archive`: retained locally until explicit deletion;
- `custom`: a caller-supplied retention date.

“Archive” is not a claim of fault-tolerant permanence. It survives ordinary
application restarts but not disk loss, ransomware, host loss, or manual volume
deletion. Use a verified off-device backup.

The API never returns physical storage paths. Names, notes, tags, labels, and imported boundaries are private untrusted text; UUIDs and SHA-256-derived keys address storage.

Core routes:

```text
GET/POST/PATCH /api/clients
GET/POST/PATCH /api/projects
DELETE         /api/projects/{project_id}?confirm=true
GET/POST/PATCH /api/projects/{project_id}/batches
GET            /api/batches/{batch_id}/assets
POST           /api/assets/{asset_id}/segmentation-jobs
GET/DELETE     /api/segmentation-jobs/{job_id}
GET            /api/projects/{project_id}/artifacts
GET            /api/projects/{project_id}/revisions
```

Migrations run transactionally and idempotently when the backend starts. The
current migrations are additive. Start the service normally or initialize and
inspect the catalogue with:

```text
cd backend
python -m app.diagnostics.storage
```

Permanent deletion refuses an active child analysis, removes the selected
project’s metadata, audit journal, artifacts, partial uploads, and unshared
source blobs, and decrements shared-blob reference counts. It leaves a
content-free hash tombstone in SQLite so the deletion itself remains
verifiable. This action cannot be undone without a verified backup.

The UI’s Client catalogue workspace supports client search, project retention, batches, bulk ingest, segment review, queue control, report exports, and recent audit history. Large lists are paged; TrackPrompt does not mount a waveform or heavy component for every item.
