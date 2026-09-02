# GCP video fast-lane runbook

The supported operator surface is the existing Mission Control application. The Python CLI remains useful for contract development, but it is not a second production scheduler or dashboard.

## 1. Analyze the track locally

Open the analysis workspace, analyze the real track, and compile its StoryPlan and ShotPlan. Keep the original audio in the analysis job or select the same local master from the Video page later.

```text
http://127.0.0.1:8765/?workspace=analysis
```

Audio, lyrics, stems, source metadata, and the local filename remain local.

## 2. Run offline validation

```powershell
Set-Location "C:\Users\theon\GitHub\TrackPrompt-Studio"
.\tools\VERIFY-GCP-VIDEO-FASTLANE.ps1
```

This discovers every video content package, compiles its Fast, Quality and smoke profiles, confirms optional GA 4K profiles fail closed, runs the fake-provider/Mission Control tests, exercises timeline/export logic with synthetic media and real FFmpeg/ffprobe, and runs the focused React workflow. It makes no cloud generation request.

## 3. Prepare GCP without generating video

Preview the bounded setup commands:

```powershell
.\tools\SETUP-GCP-VIDEO-FASTLANE.ps1 `
  -GcpProjectId "PROJECT_ID" `
  -BucketName "UNIQUE_PRIVATE_BUCKET"
```

Apply only after reviewing them:

```powershell
.\tools\SETUP-GCP-VIDEO-FASTLANE.ps1 `
  -GcpProjectId "PROJECT_ID" `
  -BucketName "UNIQUE_PRIVATE_BUCKET" `
  -Apply
```

Setup can enable APIs and create the named bucket. It does not call Veo and creates no service-account key.

## 4. Launch the integrated workflow

```powershell
.\tools\RUN-GCP-VIDEO-FASTLANE.ps1
```

Validation without starting the local service:

```powershell
.\tools\RUN-GCP-VIDEO-FASTLANE.ps1 -ValidateOnly
```

The launcher opens `/?section=video` on the loopback Mission Control instance.

## 5. Compile, review, and authorize once

On **Video**:

1. Select the completed analysis, the intended content package, and a reviewed
   1080p profile. **Static Into Signal uses Veo 3.1 Quality · 1080p** as its
   completion profile; Fast remains the lower-cost option for a newly compiled
   plan. Current real packages are `The Glitch Is Me` and `Static Into Signal`
   (`The Signal Bearer`).
2. Enter the GCP project and private GCS bucket. Audio is a local finishing input and is not part of the provider plan digest.
3. Review the continuity profile. Keep the master seed locked for reproducible planning, or explicitly generate a new seed before compilation. Optionally attach one private JPEG/PNG first-frame reference; its hash and future GCS URI become part of the exact plan.
4. Run **GCP readiness check**. It contacts only read-only GCP capability surfaces and explicitly does not submit a generation.
5. Choose **Compile exact video plan**. Review all 16 prompts, exact request JSON, source hashes, pricing snapshot, base/conservative estimates, and hard maximum spend.
6. Type the displayed digest-specific phrase exactly and choose **Authorize this complete exact batch once**.
7. Choose **Start smoke shot and complete batch**. This is the first action allowed to submit a paid request.

Mission Control reserves the request cost before every submission. The smoke shot uses the same plan, model, resolution, and parameters as the batch. Only after it downloads and passes local MP4 verification does the unchanged remaining batch continue automatically.

## 6. Review and finish

Per-shot cards expose progress, safe failure/filter summaries and diagnostic IDs, verified preview media, accept/reject, and bounded retry. **Retry same setup** creates a new attempt with the same seed/reference/request and is blocked before submission when the next reservation would cross the approved maximum. **Generate new variation** changes the plan and requires a new digest-specific authorization. **Use previous accepted end frame** extracts and hash-binds a local keyframe for the declared next shot, also requiring fresh authorization.

When all latest selected attempts are verified, Mission Control stops in a
truthful `review_ready` state. After a local audio master is bound, the visible
local-finishing actions:

1. **Resolve timeline** creates the complete 24 FPS audio-clock timeline;
2. **Export Resolve package** writes the project blueprint's validated event
   range as FCPXML 1.11, FCP 7 XML, CMX3600 EDL, edit sheet, markers, relink map,
   coverage report, and manifests;
3. **Assemble full preview** creates and verifies a complete H.264/AAC preview
   with the local master;
4. exposes downloads and **Open output**.

These actions are local-only. They never instantiate the provider client, submit
a request, change authorization, or reserve additional spend.

Use DaVinci Resolve only for final grading, transitions, overlays, titles, and other artistic touches.

Bind audio on the saved job's finishing card. **Use retained analysis audio** is shown when the private analysis source still exists; otherwise **Browse for audio…** opens the Windows picker and stages an immutable, hash-verified private copy. A compressed or non-48 kHz source receives a non-destructive 48 kHz stereo PCM finishing WAV. **Replace audio…** invalidates only local timelines, exports, and renders. **Clear binding** removes the association but deletes neither the source nor staged artifacts. These actions never instantiate the provider client or change the provider plan or authorization.

## Recovery and cancellation

- Browser refresh and SSE reconnect do not restart work.
- Mission Control reloads jobs and durable provider operation names from its existing SQLite database and resumes polling rather than submitting a duplicate.
- **Cancel batch safely** stops local continuation and preserves downloaded clips, attempts, and receipts.
- Resume or retry only from the same saved job. Do not delete `.trackprompt-data`, operation receipts, or verified clips to force a restart.
- A changed prompt, input artifact, parameter, pricing snapshot, or profile produces a different plan digest and therefore requires a new exact plan review.
- Provider access, fixed quota, or safety filtering can block a live batch without invalidating the local implementation or exports.
- If the source analysis workspace was deleted by an older build, choose
  **Repair legacy dependency** on the saved job. Existing verified clips remain
  authoritative. Reattach the original audio only if necessary, then use
  **Resolve timeline**, **Export Resolve package**, and **Assemble full preview**.
  The repair receipt proves that plan digest, authorization, reservation,
  provider operations, selected attempts, and clip hashes were not changed.

## Optional profiles

Quality 1080p and standard 4K are explicit higher-cost profiles. A full 4K batch
is not part of completion. For `Static Into Signal`, the operator selected a
complete 16-shot Quality 1080p batch. The tangible 2026-08-13 plan math is 16
outputs × 8 seconds = 128 billed output-seconds; 128 × $0.20 = **$25.60 base**.
The review adds a 50% contingency of **$12.80**, producing the **$38.40
conservative estimate**. The **$45.00 hard ceiling** is a refusal boundary, not
an expected charge: it is $6.60 above the conservative estimate and $19.40 above
base. The exact plan requires its own digest-specific authorization.

## Static Into Signal checkpoint

The package under `video-projects\static-into-signal\` uses the 218-second local
master, exactly 16 Quality 1080p requests in the operator-selected plan,
`shot-001` as the same-plan paid smoke shot, deterministic cluster seeds, and
project-owned editorial recurrence. The resolver returns shots 007 and 008
during chapter 07, ends chapter 06 on shot 012, begins chapter 08 on shot 015,
and sustains shot 016 through the outro. It never automatically reverses human
movement. Its standard output names are `trackprompt-timeline.fcpxml`,
`trackprompt-timeline.xml`, `trackprompt-timeline.edl`, and
`autonomous-preview-1080p.mp4`.

For the operator-selected Quality 1080p batch, stop after compilation until Mission Control shows the exact plan digest, all 16 sanitized request bodies, the $0.20-per-output-second snapshot rate, $25.60 base estimate, $38.40 conservative estimate, $45.00 maximum, and its digest-specific authorization phrase. Do not authorize from documentation or reuse a phrase from another plan.

Runtime state lives below `.trackprompt-data\video-generation\` and remains ignored by Git.
