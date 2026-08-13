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

This compiles all three profiles, runs the fake-provider/Mission Control tests, exercises timeline/export logic with synthetic media and real FFmpeg/ffprobe, and runs the focused React workflow. It makes no cloud generation request.

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

1. Select the completed analysis, `The Glitch Is Me` content package, and **Veo 3.1 Fast · 1080p**. This is the default completion target.
2. Enter the GCP project and private GCS bucket. Select the original local audio master when it is not retained in the analysis job.
3. Review the continuity profile. Keep the master seed locked for reproducible planning, or explicitly generate a new seed before compilation. Optionally attach one private JPEG/PNG first-frame reference; its hash and future GCS URI become part of the exact plan.
4. Run **GCP readiness check**. It contacts only read-only GCP capability surfaces and explicitly does not submit a generation.
5. Choose **Compile exact video plan**. Review all 16 prompts, exact request JSON, source hashes, pricing snapshot, base/conservative estimates, and hard maximum spend.
6. Type the displayed digest-specific phrase exactly and choose **Authorize this complete exact batch once**.
7. Choose **Start smoke shot and complete batch**. This is the first action allowed to submit a paid request.

Mission Control reserves the request cost before every submission. The smoke shot uses the same plan, model, resolution, and parameters as the batch. Only after it downloads and passes local MP4 verification does the unchanged remaining batch continue automatically.

## 6. Review and finish

Per-shot cards expose progress, safe failure/filter summaries and diagnostic IDs, verified preview media, accept/reject, and bounded retry. **Retry same setup** creates a new attempt with the same seed/reference/request and is blocked before submission when the next reservation would cross the approved maximum. **Generate new variation** changes the plan and requires a new digest-specific authorization. **Use previous accepted end frame** extracts and hash-binds a local keyframe for the declared next shot, also requiring fresh authorization.

When all latest selected attempts are verified and a local audio master is bound, Mission Control automatically:

1. resolves the complete 24 FPS audio-clock timeline;
2. exports FCPXML 1.11, FCP 7 XML, CMX3600 EDL, edit-sheet CSV, and marker CSV;
3. assembles and verifies a complete H.264/AAC preview with the local master;
4. exposes downloads and **Open output**.

Use DaVinci Resolve only for final grading, transitions, overlays, titles, and other artistic touches.

## Recovery and cancellation

- Browser refresh and SSE reconnect do not restart work.
- Mission Control reloads jobs and durable provider operation names from its existing SQLite database and resumes polling rather than submitting a duplicate.
- **Cancel batch safely** stops local continuation and preserves downloaded clips, attempts, and receipts.
- Resume or retry only from the same saved job. Do not delete `.trackprompt-data`, operation receipts, or verified clips to force a restart.
- A changed prompt, input artifact, parameter, pricing snapshot, or profile produces a different plan digest and therefore requires a new exact plan review.
- Provider access, fixed quota, or safety filtering can block a live batch without invalidating the local implementation or exports.

## Optional profiles

Standard 1080p and standard 4K are explicit optional rerender profiles. A full 4K batch is not part of completion. Review the finished 1080p edit before choosing a higher-cost rerender.

Runtime state lives below `.trackprompt-data\video-generation\` and remains ignored by Git.
