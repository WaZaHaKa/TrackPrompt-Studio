# Persistent Analysis Library and local video recovery

## Retention contract

Ordinary analysis has no time-to-live. A completed analysis is retained until an
operator uses **Delete analysis** and confirms the destructive action a second
time. A legacy `JOB_TTL_MINUTES` environment value is ignored and logged once at
startup.

Successful completion publishes the canonical analysis source and artifacts into
the existing TrackPrompt data directory and SQLite database:

```text
.trackprompt-data/
  trackprompt.sqlite3
  archive/
    blobs/<sha-prefix>/<complete-source-sha>.bin
    analyses/<analysis-uuid>/manifest.json
    analyses/<analysis-uuid>/artifacts/<kind>/<revision-sha>.json
```

Source blobs are deduplicated by complete-file SHA-256 and reference counted.
Artifact revisions are immutable and content addressed. The manifest contains
safe IDs, hashes, versions, timing/media summaries, and dependency counts. It
never contains a physical path or raw lyrics. Writes use private temporary files,
complete hash verification, and atomic publication. Startup reconciliation is
idempotent and does not create duplicate blobs, revisions, or audit events.

The **Analysis Library** lists persistent records with search, sort, status,
archive health, duration, StoryPlan/ShotPlan availability, retained-audio
availability, and dependent video-job count. **Repair archive** reruns bounded
reconciliation from canonical local artifacts; it does not reanalyze audio or
contact a provider.

Explicit deletion removes the live UUID directory, source reference, unshared
source bytes, and every archived private artifact revision. It is blocked when a
dependent video job has not yet received its immutable snapshot. A content-free
catalogue tombstone and append-only deletion event remain so the application does
not silently resurrect or misrepresent deleted work.

## Self-contained video jobs

New video jobs copy the exact StoryPlan and ShotPlan used during compilation into
the private video-job input directory. Their dependency manifest hash-binds those
snapshots, the source analysis ID, and the provider-generation plan digest. Video
resolution uses this order:

1. immutable video-job snapshot;
2. persistent analysis archive;
3. live analysis workspace;
4. the compiled chapter map with an explicit degraded-provenance warning.

Deleting an analysis therefore cannot invalidate a properly snapshotted video
job. A missing legacy analysis is represented by a safe tombstone rather than a
fabricated reconstruction.

## Recover a legacy paid video job

On the saved Mission Control Video job:

1. Confirm all expected provider clips are still **verified** and preserve their
   complete SHA-256 values before repair.
2. Choose **Repair legacy dependency**. This writes only local dependency and
   repair receipts. It must not change the provider plan digest, authorization,
   reserved spend, selected attempt IDs, provider operation names, clip bytes, or
   clip hashes.
3. If needed, choose **Reattach original audio**. An exact expected SHA-256 match
   is bound immediately. A mismatch shows bounded hash prefixes and durations and
   requires the displayed local-only confirmation phrase; that creates a delivery
   revision without altering paid-generation identity.
4. Choose **Resolve timeline**, **Export Resolve package**, then **Assemble full
   preview**. These actions are local FFmpeg/export work and never instantiate the
   provider client.
5. Verify the final preview with `ffprobe`, verify the continuous master-audio
   duration, and re-hash every original provider clip. Confirm reserved spend and
   durable operation names are unchanged.

The local finishing stage stops in a truthful review-ready state until these
operator-visible actions are taken. It does not submit a retry merely because a
legacy analysis workspace is missing.

## Backup and verification

Before repairing valuable local state, create a private backup with SQLite's
online backup API:

```powershell
Set-Location "C:\Users\theon\GitHub\TrackPrompt-Studio"
.\backend\.venv\Scripts\python.exe .\tools\backup_static_video_recovery.py create `
  --data-dir .\.trackprompt-data `
  --job-id <video-job-uuid>
```

The command copies both SQLite databases, the exact video-job directory, and the
existing archive into a timestamped ignored backup. It then verifies database
integrity, every copied file hash, and the provider-clip count. The manifest is
safe to inspect locally but remains private runtime data and must not be committed.

To verify a restart, stop only the descriptor-identified loopback Mission Control
process after checking for active FFmpeg, Blender, render, encode, and mutex work.
Restart with `WZHK-Media-Launcher.cmd`, reopen **Analysis Library** and **Video**,
and confirm the same catalogue entries, job ID, plan digest, clip counts, and
local-finishing state.
