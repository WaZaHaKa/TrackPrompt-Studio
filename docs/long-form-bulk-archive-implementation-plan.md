# Long-form, bulk, and archive implementation plan

Status: implemented as the catalogue schema 1.0.0 milestone. The current repository remains the source of truth for ordinary single-track analysis and the Blender visualizer.

## Baseline measured before this milestone

- Ordinary multipart upload: `MAX_UPLOAD_MB=200` (200 MiB), streamed in 1 MiB reads.
- Ordinary analysis duration: `MAX_DURATION_SECONDS=1200` (20 minutes).
- Ordinary completed-analysis retention: persistent until explicit deletion.
- Analysis concurrency: one worker by default.
- Admission: two running or waiting ordinary jobs in Compose.
- Heavy GPU work: one task at a time by default.
- Isolated DSP/Deep worker timeout: 600 seconds.
- Persistence: lifecycle metadata in SQLite; analysis payloads in private UUID job directories.
- Restart behavior: interrupted ordinary jobs fail safe and their private media is removed.

These limits remain valid for the existing `/api/analyses` path except for the
superseded timed-retention baseline above. A 12-hour source is not an ordinary
analysis. Incomplete resumable upload sessions still have their own bounded
abandonment cleanup; that is not analysis retention.

## Architecture

The milestone adds three separate concepts:

1. A source asset is one validated physical file, retained once in the project archive and identified by complete-file SHA-256.
2. A virtual segment is a revisioned time range over an asset. It records stable-core and transition ranges without permanently copying audio.
3. An analysis job is bounded. Child analysis decodes only a reviewed segment range and uses the existing TrackPrompt pipeline. Long-form scanning has an independent timeout and streaming resource policy.

Catalogue data uses transactional, idempotent SQLite migrations. Archived payloads live below `archive/projects/<project UUID>/`; partial resumable sessions live below `uploads/<upload UUID>/`; ordinary temporary jobs remain below `jobs/<job UUID>/`. Public APIs return storage state and hashes, never physical paths.

## Delivery sequence

1. Add validated resource and retention settings and capability reporting.
2. Add versioned catalogue migrations for clients, projects, batches, blobs, assets, upload sessions, segment revisions, durable queue items, artifacts, revisions, and audit events.
3. Add append-only per-project audit events with canonical JSON SHA-256 chaining.
4. Add resumable, strictly ordered chunk uploads with idempotency, rolling byte accounting, complete SHA-256 validation, disk/quota admission, and ffprobe validation.
5. Add client/project/batch CRUD and paginated asset, segment, artifact, revision, and audit APIs.
6. Add bounded streaming coarse scanning and deterministic multi-signal boundary selection/refinement. Crossfades remain mixed transition regions; the system never claims source separation.
7. Add revisioned boundary replace/add/move/delete/merge/split/restore operations and manual CUE/CSV/JSON import.
8. Add persistent child-analysis queue semantics and batch pause/resume/cancel/retry. Heavy work continues to use the existing worker/GPU limits.
9. Add set-level JSON, Markdown, and CSV comparisons using only available measurements and explicit withheld counts.
10. Add a virtualized/resumable React catalogue workspace while retaining the current single-track workspace.
11. Add archive backup, verify, restore, storage, queue, long-form, and audit diagnostics.
12. Add synthetic tests, migration/restart tests, and update operational/privacy documentation.

## Safety contracts

- `MAX_LONGFORM_DURATION_SECONDS=43200` applies only to source ingestion and segmentation.
- `MAX_SINGLE_TRACK_ANALYSIS_SECONDS` defaults to the existing `MAX_DURATION_SECONDS` value and bounds every ordinary or child analysis.
- Chunk requests are bounded independently; nginx must permit a chunk plus protocol overhead, not an entire multi-gigabyte source request.
- There is no arbitrary item-count cap. Disk reserve, source-size, archive-quota, upload concurrency, analysis concurrency, and GPU concurrency are the actual controls.
- Long-form scanning reads bounded FFmpeg PCM windows and stores only low-rate feature observations. It does not build a full-source waveform or STFT.
- Archived means retained locally until explicit deletion. It is not protection against disk failure; verified backups are required for durability.
- Audit chaining is tamper evidence, not proof of operator identity.
- Source names, client text, cue labels, transcripts, and metadata are untrusted and never become storage paths or instructions.
- Temporary decodes and stems are deleted. A content-addressed archived source is stored once and linked logically to batches.
- The existing visualizer presets and canonical runner are preserved. This milestone does not launch Blender or a production render.
