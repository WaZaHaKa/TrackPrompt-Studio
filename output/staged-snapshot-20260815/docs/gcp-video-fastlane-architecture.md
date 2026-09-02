# GCP video fast-lane architecture

## Reuse boundary

The new module consumes existing TrackPrompt outputs. It does not re-run or fork music analysis.

Inputs may include:

- completed analysis job ID;
- minimized `visual-cues.json`;
- versioned `StoryPlan` and `ShotPlan`;
- one project creative bible;
- one shot bank;
- normalized chapter map;
- selected generation profile and maximum spend.

The compiler hashes every source artifact it actually uses. The compiled plan contains sanitized visual intent and hashes, but it does not contain the original source locator, transcript, credentials, or raw analysis payload.

## Track-agnostic platform/content split

| Layer | Reusable | Project-specific |
|---|---|---|
| Contracts | profiles, shots, costs, operations, QA, timeline, exports | no project names |
| Planning | hashes, prompt composition, StoryPlan boundary snapping | creative bible, shot bank, chapter map |
| Orchestration | submit, poll, resume, retry, budget reservation, download | selected shot IDs |
| Editing | validated editorial-rule interpretation, timeline resolution, FCPXML/FCP7/EDL/CSV, FFmpeg assembly | treatment version, chapter sequences/recurrence, coverage assertions, export filenames and handoff notes in `edit-blueprint.json` |
| UI | plan, cost, progress, failures, clips, export buttons | titles and shot artwork |

## Mission Control integration

`VideoGenerationController` is owned by the existing `MissionControlService`. It uses the canonical Mission Control SQLite database, service-managed background-task lifecycle, restart recovery, event condition, and SSE replay route. It does not create another FastAPI app, dashboard, SQLite file, analysis worker, or generic authorization subsystem. Provider long-running operations are service-supervised I/O jobs rather than Blender/GPU render tasks, so they follow the same established service-task pattern as persistent encoding without pretending to be frame chunks in `PersistentRenderScheduler`.

Every task/event should carry:

```text
schemaVersion
jobId
analysisJobId
projectId
planDigest
shotId when applicable
provider/model/profile identity
state and attempt
reserved and actual/known cost metadata
operation name without bearer token
artifact identity without unsafe public path
created/updated timestamps
bounded error summary
```

The provider operation name is durable resume state. Browser reconnect must never lose backend-owned progress. Render and video events allocate from one global Mission Control event sequence so `Last-Event-ID` remains unambiguous.

## Resolution strategy

- 1080p Fast is the default first and final profile.
- 1080p standard is an optional quality rerender path.
- 4K standard is optional, not a completion requirement.
- Fast 4K is deliberately rejected by the starter because the reviewed GA model specification for the Fast model lists 720p/1080p.
- Generated and delivery frame rate is 24 FPS.

## Editorial strategy

The verified private audio binding is authoritative. Its exact ffprobe duration and SHA-256 participate in a local edit digest while the completed provider-generation digest and authorization remain unchanged. The chapter map provides normalized ranges; stored ShotPlan boundaries are used only when their inferred clock matches the newly bound master within five percent.

The deterministic 1080p rough cut expands immutable provider clips into project-validated replaceable derived events. The shared engine knows no song, character, motif, chapter name or output stem. It validates and interprets the selected package's `edit-blueprint.json`, including deterministic shot sequences, alternate trims, bounded retimes/crops, optional reviewed treatments, recurrence checks and artifact names. FCPXML, FCP7 XML, and EDL reference those event files plus one continuous 48 kHz stereo master. Resolve remains the finishing environment for grading, refinements, overlays, and titles.

`The Glitch Is Me` retains its established 55–80 event escalation and legacy filenames through its blueprint. `Static Into Signal` uses a conservative 45–64 event treatment, no automatic reverse, chapter-07 recurrence of shots 007/008 and the standard `trackprompt-timeline.*` / `autonomous-preview-1080p.mp4` names. Adding another track requires a new content package, not a branch in shared Python or React code.
