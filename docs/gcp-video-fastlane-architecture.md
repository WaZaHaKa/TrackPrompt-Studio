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
| Editing | timeline resolution, FCPXML/FCP7/EDL/CSV, FFmpeg assembly | editorial blueprint and chapter transitions |
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

The original audio duration is authoritative. The chapter map provides normalized ranges so the content package works before exact track timings are known. During timeline resolution, normalized boundaries are snapped to the nearest existing TrackPrompt ShotPlan boundary within three seconds. Inside each chapter, generated shots alternate deterministically with nonzero in-points on reuse.

The autonomous FFmpeg assembly is intentionally conservative: normalized H.264 segments, straight cuts, complete audio coverage, and local audio mux. Resolve remains the finishing environment for artistic transitions, grading, overlays, speed effects, and final titles.
