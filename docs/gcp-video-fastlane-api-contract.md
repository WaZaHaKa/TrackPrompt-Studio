# GCP video fast-lane API and Mission Control contract

All routes are part of the existing loopback Mission Control router. They use its service lifecycle, structured error envelope, SQLite database, and `/events` SSE replay stream.

## Local API surface

```text
GET  /api/mission-control/video/catalog
GET  /api/mission-control/video/jobs
POST /api/mission-control/video/plans
GET  /api/mission-control/video/plans/{job_id}
GET  /api/mission-control/video/plans/{job_id}/requests
POST /api/mission-control/video/plans/{job_id}/authorize
POST /api/mission-control/video/jobs/{job_id}/start
POST /api/mission-control/video/jobs/{job_id}/resume
POST /api/mission-control/video/jobs/{job_id}/cancel
POST /api/mission-control/video/jobs/{job_id}/shots/{shot_id}/retry
POST /api/mission-control/video/jobs/{job_id}/shots/{shot_id}/chain-reference
POST /api/mission-control/video/jobs/{job_id}/shots/{shot_id}/review
POST /api/mission-control/video/jobs/{job_id}/resolve
POST /api/mission-control/video/jobs/{job_id}/export
POST /api/mission-control/video/jobs/{job_id}/assemble
POST /api/mission-control/video/jobs/{job_id}/open
POST /api/mission-control/video/doctor
GET  /api/mission-control/video/jobs/{job_id}/shots/{shot_id}/clip
GET  /api/mission-control/video/jobs/{job_id}/artifacts/{artifact}
GET  /api/mission-control/events?jobId={job_id}&afterSequence={sequence}
```

`video/catalog`, plan compilation, request previews, and local exports do not contact the provider. `video/doctor` performs read-only GCP checks and returns `generationSubmitted: false`; `networkContacted` remains false when a local prerequisite such as `gcloud` is absent and becomes true only when the read-only cloud checks are attempted. Only `start` or an explicit shot retry can reach the paid provider submit path, and both validate the persisted plan authorization first.

## States

Job states:

```text
planned → authorized → smoke_submitted → generating → review_ready
                                                    → timeline_ready → exported → assembling → complete
                     ↘ partial / blocked_budget / blocked_provider_access
                       / blocked_provider_quota / failed / cancelled
```

Shot attempts:

```text
planned → reserved → submitted → running → succeeded → downloaded → verified
                              ↘ filtered
                              ↘ failed
```

Review is an independent `pending | accepted | rejected` field. Technical verification never masquerades as artistic acceptance.

## Exact plan and cost contract

The plan digest covers analysis job identity; every supplied source-artifact hash; request-contract version; model/profile; prompt and negative prompt; continuity profile and group membership; master seed and seed-lock state; deterministic derived seed and variation index; reference asset identity/hash/GCS URI; continuation relationship; duration; aspect ratio; resolution; sample count; audio/enhancement/compression/person parameters; exact GCS prefix; per-shot estimate; pricing snapshot; base and conservative estimate; and maximum spend.

Authorization stores only a digest of the operator phrase. Before each submit/retry, Mission Control validates:

```text
authorization.projectId == job.projectId
authorization.planDigest == job.planDigest
reservedCost + nextRequestCost <= maxSpendUsd
```

The confirmation phrase includes the first 12 characters of the plan digest, so a changed seed, prompt, model, request field, price, maximum, or reference image produces a visibly new phrase. The reservation and deterministic attempt identity are persisted before a provider call. If the process exits between reservation and receiving a durable operation name, Mission Control fails closed with `provider_submission_outcome_unknown`; it does not risk an automatic duplicate.

The retry request body is `{ "mode": "same_setup" }` or `{ "mode": "new_variation" }`. Same-setup retry uses the existing authorization and exact request while the budget ceiling still permits another reservation. New-variation retry never submits immediately: it archives the prior plan, request previews, and authorization receipt; increments the variation; derives a new seed; clears authorization; and returns the newly planned batch for review.

`chain-reference` accepts `{ "sourceShotId": "shot-NNN" }`. The source must be the target's declared previous shot and must have an operator-accepted, technically verified local clip. Mission Control extracts its final frame locally, hashes it, binds its exact future GCS URI into the target request, changes the plan digest, and returns to `planned` for fresh authorization.

## Persistence and events

Video jobs and events use additive tables in the existing `mission-control.sqlite3`. Render events and video events allocate from one monotonically increasing sequence and replay through the same SSE route. `event: video_generation` payloads expose only safe progress, state, reservation, and bounded error fields.

Provider operation names, output URIs, local paths, reference-image paths, audio path, GCP project, bucket, and authorization material remain private persisted state. Public API views use IDs, hashes, prompts, safe artifact URLs, bounded provider status, diagnostic IDs, and booleans rather than physical paths or raw provider bodies.

## Idempotency and media

Attempt identity is derived from:

```text
planDigest + shotId + attempt + modelId + exact request payload
```

Existing submitted/running attempts resume by operation name. Downloads use an attempt-scoped destination and refuse overwrite. The provider GCS URI must remain below the exact authorized shot prefix. Local ffprobe verification requires the exact dimensions, 24 FPS, expected duration, one video stream, and no generated audio before a clip enters the timeline.
