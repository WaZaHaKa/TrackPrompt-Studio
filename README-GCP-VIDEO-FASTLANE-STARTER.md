# TrackPrompt Studio — GCP Video Fast Lane starter v0.1.0

## Purpose

Turn TrackPrompt Studio's existing audio analysis, visual cue sheet, StoryPlan, and ShotPlan into a track-agnostic cloud video-generation workflow without creating a second application or repeating the heavy Blender production-authorization process.

The first content package is **DJ WaZaHaKa — The Glitch Is Me**, but the reusable module contains no project-specific branching. Project identity, creative direction, shots, normalized chapters, budget, and output profiles live below `video-projects/`.

## Permanent architecture

```text
existing TrackPrompt analysis
        ↓
existing visual-cues.json
        ↓
existing StoryPlan / ShotPlan
        ↓
new provider-neutral video plan compiler
        ↓
existing Mission Control lifecycle
        ↓
GCP Veo asynchronous operations
        ↓
local download + ffprobe QA
        ↓
original audio clock + deterministic edit resolver
        ↓
FFmpeg autonomous assembly + DaVinci Resolve XML/EDL/CSV package
```

Mission Control remains the one scheduler, persistent store, progress surface, and recovery authority after Codex completes the repository integration.

## Default delivery decision

1080p is the default and is a valid final delivery. The 4K profile is optional and should be selected only after the 1080p material proves that the extra generation cost is artistically justified.

The checked-in pricing table is an explicit snapshot dated **2026-08-13** and must remain versioned/operator-reviewable. The included default package estimates:

| Profile | 16 × 8-second clips | Base estimate | Conservative 1.5× estimate | Package cap |
|---|---:|---:|---:|---:|
| Veo 3.1 Fast, 1080p | 128 output seconds | $12.80 | $19.20 | $24.00 |
| Veo 3.1 standard, 1080p | 128 output seconds | $25.60 | $38.40 | $45.00 |
| Veo 3.1 standard, 4K | 128 output seconds | $51.20 | $76.80 | $80.00 |

Provider pricing and model availability can change. The live application must expose the snapshot date and refuse an unreviewed unknown model/resolution pair.

## Authorization model

The fast lane is intentionally simpler than an exact Blender frame-production authorization:

1. Compile one exact plan and digest.
2. Show the maximum spend and sanitized request preview.
3. Type one exact phrase for that plan and cap.
4. Allow smoke generation, the remaining batch, and bounded retries while the same unexpired authorization still covers the cumulative reserved request cost.

Changing the plan, profile, prompt bank, model, resolution, sample count, or maximum spend changes the plan digest and invalidates the authorization.

## Privacy boundary

- The original song, stems, raw lyrics, transcript, local source paths, model paths, and credentials stay local.
- GCP receives only the compiled visual prompt, negative prompt, generation parameters, and optional separately approved visual references.
- Generated video is video-only. The original master is muxed locally.
- Authentication uses the active `gcloud` account. The package never creates or commits a service-account JSON key.
- Runtime operation receipts, provider responses, clips, authorization, resolved timelines containing local paths, and delivery media remain ignored under `.trackprompt-data/`.

## Package layout

```text
backend/app/video_generation/        reusable contracts and starter implementation
backend/tests/video_generation/      focused offline tests
schemas/                             JSON contracts
video-projects/the-glitch-is-me/     first content package and 16 shot prompts
video-projects/_template/             reusable starting point for future tracks
tools/                               Windows setup/run/verify workflows
docs/                                architecture, API, privacy, editing, and Codex handoff
CODEX-GCP-VIDEO-FASTLANE-PROMPT.md   initial autonomous Codex task
DIRECTORY-MAP.txt                    root-aligned inventory and entry-point map
VALIDATION-REPORT.md                 offline test and packaging evidence
PACKAGE-MANIFEST.json                per-file byte counts and SHA-256 identities
```

## Integration status

This archive is a tested **starter and execution specification**, not a claim that the current TrackPrompt checkout has already registered FastAPI routes, Mission Control task types, UI controls, database migrations, or a live Veo quota. Codex is instructed to inspect the actual repository, integrate the module into existing conventions, run the full validation suite, and truthfully report any external GCP quota/access blocker.

No billable GCP request was made while building or validating this archive.
