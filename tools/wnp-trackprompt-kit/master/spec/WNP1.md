# WNP/1 wire contract — implementation profile 1.0.0

**Purpose:** identity, durable state, history, evidence, independently timestamped
observations, and secure read-only synchronization. Humans and machines see the
same underlying objects. Execution and scheduling are outside this protocol.
`MUST`, `MUST NOT`, and `SHOULD` below are requirements for this package.

## 1. Public documents

| Document | Required fields | Meaning |
|---|---|---|
| `wnp.json` | protocol, project, kind, capabilities | Project identity, portable and normally tracked |
| `.wnp/node.json` | protocol, node, project, stream, agent | Local execution/history identity |
| `.wnp/state.json` | protocol, stream, sequence, timestamp, state, health | Latest materialized durable snapshot |
| `.wnp/events.ndjson` (each line) | stream, sequence, timestamp, type | Ordered durable fact |
| Evidence reference | path, sha256 | Bound supporting bytes |

`protocol` is exactly `wnp/1`. Project and node are stable slugs. Stream is a
canonical UUID, stable across restarts and reconnects. Agent is an implementation
name/version string (at most 128 characters). Node project MUST match project
manifest. `(project,node,stream)` identifies one observed history.

Capabilities are unique lowercase dotted/sluggable strings. `events` is mandatory
for this reference profile. `evidence`, `metrics.gpu`, `metrics.system` are optional
advertisements and not credentials. Kind is descriptive, e.g. `media.pipeline` or
`generic`. Never infer permissions from kind or capabilities.

## 2. Common types and bounds

Identifiers: `[a-z0-9][a-z0-9._-]{0,127}`. This applies to project, node, run, task,
attempt, kind, capability, event type and detail. Provider job IDs may be opaque
strings; do not force their format into the project-ID grammar.

UTF-8 JSON without BOM or duplicate object keys. NDJSON has one physical line per
event, LF terminated. No NaN/Infinity. Writers emit UTC RFC3339 timestamps; readers
accept explicit timezone offsets. Ordering uses sequence, not clocks or mtimes.
Snapshot sequence is an integer 0..9007199254740991; events start at 1, contiguous.
Readers ignore unknown optional fields but MUST NOT reinterpret known fields.

Limits in this profile: 64 KiB per event including LF; 1 MiB per sync response;
500 events requested by default, at most 5000; 8 MiB per evidence object; at most
32 evidence references/event; message at most 2048 characters. Byte limits take
precedence over requested event count. Larger media must use small manifests.

Absent = not represented. Explicit null = explicitly none, for nullable fields
such as run/task/attempt. A snapshot is a complete view, not a client merge patch.
A local writer's `patch` preserves absent keys and explicitly clears with null;
its emitted full `snapshot` has no merge ambiguity.

## 3. Durable state

States: `idle queued running waiting paused complete failed degraded unknown`.
Health: `ok degraded error unknown`. Keep both independent. `degraded` state is
retained for compatibility; prefer a precise workload state plus degraded health.
A failed task does not necessarily imply an unhealthy host. A stale worker does
not imply a failed task. Only the application adapter assigns workload meaning.

Optional durable fields: run, task, attempt, detail, message, progress, extensions.
`progress` when present has finite nonnegative current and a unit slug; optional
finite total is at least current. Unknown total is omitted/null; no fabricated
percentage. A changed total must represent an explicit changed stage/work unit.
`extensions` groups domain details (e.g. `trackprompt.tasks`). Do not put secrets,
source lyrics, raw media, unrestricted paths, or arbitrary executable commands here.

Example durable payload (metadata is added by the host):
```json
{"state":"waiting","health":"degraded","run":"344ab3d2444c","task":"shot-002","attempt":"attempt-001","detail":"render.reconciling"}
```

## 4. Durable events and deterministic replay

Optional event fields: run/task/attempt/detail/message, data (object), evidence
(array), snapshot (post-event durable payload), key and request_sha256 (reference
writer idempotency extension). `state.changed` MUST carry snapshot. For ANY other
event that changes the snapshot, snapshot MUST be included. Without snapshot,
replay keeps the previous payload and advances only event metadata.

The host adds protocol/stream/sequence/timestamp to the replayed payload. It MUST
NOT derive state from English messages or a domain-specific event name.
`task.completed` is scoped by task; never automatically completes a run.

Event types are dotted names: run.started, task.submitted, worker.state,
provider.state, verification.passed, recovery.completed, system.degraded, and
namespaced domain facts. Unknown events remain visible and syncable.

Producer keys identify semantic reports, not provider job identities. Identical
key+request retries return the original event; same key with different request
content is `idempotency_conflict`. Consumers deduplicate by `(stream,sequence)`.
No telemetry timestamps belong in a durable idempotency key.

## 5. Observations (transient)

GET status MAY overlay `observations` on the durable state:
```json
{"sampled_at":"2026-09-18T10:00:00Z","worker":{"state":"stale","heartbeat_at":"2026-09-18T09:55:00Z"},"provider":{"name":"comfyui","job":"opaque-id","state":"unknown","observed_at":"2026-09-18T09:59:50Z"},"metrics":{"gpu":[{"index":0,"utilization_percent":0,"vram_used_mib":2239,"vram_total_mib":12288}]}}
```
Worker optional attributes: id, state (`idle active stale stopped unknown`), pid,
heartbeat_at, lease_expires_at. Provider: name/state, optional job/observed_at.
Metrics may contain cpu_percent, ram_used_mib, disk_free_mib and gpu array with
index, name, utilization_percent, vram_used_mib, vram_total_mib, temperature_c.
Unknown or unavailable readings are null/absent, never zero as a substitute.
Current frame progress may also be sampled here. Snapshot timestamp remains the
last durable event time; observations use their own sampled/source times.

The host stores structurally bounded JSON observations, while the adapter is
responsible for honest source timestamps/types. Expiry is a client freshness
classification, NOT a workload state mutation. An observer returning after days
can recover durable transitions but not unrecorded intermediate GPU samples.

## 6. Evidence and security boundary

A reference has a portable `evidence/...` relative path and 64 lowercase hex
SHA-256; optional bytes/media_type/kind. No drive letters, leading slash, `..`,
empty components, percent escapes, backslashes, symlinks or reparse traversal.
Only evidence referenced by a committed event is remotely fetchable. Bytes are
written and checked before the referencing event is committed. An orphan file
from an interrupted registration is not exposed. Do not automatically delete it.

Evidence MUST be sanitized before registration. A hash proves byte identity,
not provenance or validity of the assertion. Exposed reports must not contain
keys, passwords, environment values, private media, or sensitive command lines.
The host never remotely accepts a requested filesystem root or arbitrary file.

## 7. Durability and recovery

One OS-exclusive writer lock serializes a runtime. Sequence allocation, duplicate
key checks and append are inside that lock. Append complete event + fsync first;
then write/flush a unique same-directory temporary snapshot and atomically replace.
The snapshot MUST equal replay at its stated sequence and MUST NOT exceed head.

The reference stores an immutable genesis snapshot for sequence zero. A missing or
trailing snapshot is rebuilt from history. A snapshot ahead of or inconsistent
with history is an integrity error, not permission to forge history.

Only an unterminated final journal fragment can be explicitly quarantined by local
`recover`; complete malformed/gapped records are not discarded. Preserve incident
bytes. Reads do not silently truncate. Stable media and filesystem guarantees
still depend on the OS/hardware; fsync is not a universal power-loss guarantee.
Native local filesystems only in this implementation; no shared-network writers.

A normal restart retains stream. A destructive rollback/restore starts a new
history only through an explicit operator migration that archives the old identity.
No automatic stream reset or retention deletion exists. Complete active history
is required; rotation/compaction needs a later implementation, not silent pruning.

## 8. Sync, bootstrap and reconnect

Request: stream, after, optional limit (1..5000), optional until (fixed watermark).
Response: protocol, stream, after, head, events, more, next_after; terminal page
also includes snapshot with sequence=head.

First page pins head to the observed committed history. Later pages use until=head
while new events may be appended. Returned events are contiguous after+1..next_after.
An empty page may be terminal only; it MUST NOT jump a cursor. `more` is exactly
next_after<head. Client commits events and its last contiguous cursor together.

First connect may fetch complete history from zero (reference client default) or
accept a current snapshot (explicit current bootstrap); the latter records an
incomplete historical baseline. Changed stream preserves old history and bootstraps
separately. A different node/project at an enrolled endpoint is an identity conflict.

Gap, ahead cursor, inconsistent snapshot or changed bytes cause an error without
advancing the faulty page. Connection loss preserves last-known state with explicit
connection/freshness status. Clock jumps never reorder events. Evidence is fetched
separately and hash-checked. Client caching is not authoritative project state.

## 9. Read-only transports

Implemented HTTP routes:
```
GET /v1/project
GET /v1/node
GET /v1/status
GET /v1/health
GET /v1/sync?stream=UUID&after=N&limit=500&until=H
GET /v1/evidence/evidence/HASH.bin
```
The repeated `evidence/` in the last route is intentional: suffix is the complete
relative evidence path from the reference. Health route reports host service
readiness only, not task success. Mutation methods return 405.

Bearer authentication is mandatory even on loopback. This host binds 127.0.0.1;
remote transport is SSH forwarding with verified host keys, optionally plus TLS.
TLS uses configured certificate/key; no insecure certificate verification bypass.
No CORS permission or redirect following. VPN reachability never replaces identity.

The CLI uses `python master/wnp.py --root PROJECT --json COMMAND` (no installed
`wzhk` executable is assumed). `rpc` accepts one bounded read-only JSON request on
stdin for a fixed SSH forced-command deployment. Do not expose append/init/recover
via a generic remotely supplied command string. API-over-SSH is the primary guide.

## 10. Errors and conformance

Errors: `{ "protocol":"wnp/1", "error":{ "code":"...", "message":"...", "retryable":false } }`.
Codes include invalid_request, unsupported_version, not_found, unauthorized,
forbidden, stream_changed, sequence_gap, cursor_ahead, identity_conflict,
idempotency_conflict, integrity_error, limit_exceeded, temporarily_unavailable,
internal_error. CLI additionally reports local_io_error. No remote stack traces.
HTTP mappings: request errors400, auth401/403, missing404, integrity/conflict409,
limit413, temporary503, unexpected500. Transport loss is client-side unreachable.

Conformance requires identity/version validation, replay, gap handling, stable
stream, bounded sync, evidence checks, OS locking, crash tests, no mutation API,
unknown-field retention and truthful freshness. See tests/ and package report.
