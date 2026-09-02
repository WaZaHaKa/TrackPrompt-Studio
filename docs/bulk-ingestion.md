# Bulk and resumable ingestion

Bulk selection has no arbitrary product file-count cap. Acceptance is still bounded by source size, archive quota, minimum free disk, upload concurrency, analysis concurrency, and GPU concurrency.

The ordinary `POST /api/analyses` multipart route remains capped at 200 MiB and 20 minutes by default. Large or long-form sources use resumable sessions:

```text
POST   /api/upload-sessions
PATCH  /api/upload-sessions/{upload_id}
GET    /api/upload-sessions/{upload_id}
POST   /api/upload-sessions/{upload_id}/complete
DELETE /api/upload-sessions/{upload_id}
```

Creation records batch, safe display name, total bytes, user order, permission confirmation, and an idempotency key. A PATCH must provide `Content-Range`, `Upload-Offset`, and optionally `X-Chunk-SHA256`. Chunks are append-only and strictly ordered; overlap, gap, total mismatch, and premature completion are rejected. The default chunk is 32 MiB. The backend permits three simultaneous chunk requests by default.

Completion streams a complete-file SHA-256, verifies any expected hash, rechecks disk admission, runs ffprobe, enforces the 43,200-second source limit, and atomically moves bytes under a content-derived archive key. Duplicate SHA-256 content reuses the existing blob and creates a distinct logical batch asset; duplicate identity is never inferred from filename or size.

The browser stores session IDs and durable offsets in local storage. After reload, it shows retained progress and asks the user to reselect matching local files because browsers do not preserve `File` handles. The queue renders 50 rows at a time and bounds upload workers by `/api/capabilities`.

Nginx’s resumable-session location uses a separate 34 MiB body ceiling for the
default 32 MiB chunk plus bounded protocol overhead, so it never receives one
enormous 12-hour request. Operators who change `UPLOAD_CHUNK_MB` must update
that independent ingress ceiling. The backend still applies the authoritative
chunk-size check.
