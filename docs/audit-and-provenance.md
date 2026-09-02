# Audit and provenance

Meaningful catalogue mutations append one typed event in the same SQLite transaction as the state change. Reads and SSE keepalives are not logged unless an operator explicitly enables read-event policy.

Events use UTC timestamps, a monotonic per-project sequence, a correlation ID, a bounded redacted payload, a previous-event SHA-256, and a SHA-256 over canonical JSON. The journal contains IDs, state, hashes, byte counts, versions, and safe reasons. It excludes raw audio, lyrics/transcripts, tensors, secrets, stack traces, and physical paths.

Exports and verification:

```text
GET /api/projects/{project_id}/audit
GET /api/projects/{project_id}/audit.jsonl
GET /api/projects/{project_id}/audit.csv
GET /api/projects/{project_id}/audit/verify

cd backend
python -m app.diagnostics.audit
```

The chain is tamper evidence. It is not cryptographic proof of the human who operated the local machine.

Artifacts have owner, type, schema/media versions, byte size, SHA-256, producer versions, current/superseded state, and an internal relative storage key that is never public. Revisions keep parent, reason, artifact hash, schema, and audit reference. Existing revisions are not mutated in place.

