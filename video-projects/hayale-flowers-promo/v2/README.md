# Hayale Flowers V2 — prepare now, create manually

The project is prepared for a **60-second, 1920×1080 landscape promo at 24 fps**.
It contains six scene references refined from the supplied photographs, one
reference made from the unchanged supplied logo, seven Veo scene prompts, local
music cues, and the manual creation command. Preparation has not called GCP,
uploaded references to GCS, authorized a batch, or generated any Veo video.

## Run creation yourself

Double-click **`RUN-HAYALE-PROMO-V2.cmd`** in the TrackPrompt repository root.
Or run this from the repository:

```powershell
./run-hayale-promo-v2.ps1 -Create
```

On the first run, supply the GCP project ID and existing private GCS bucket.
The command displays the exact seven-request plan, target and generation
reservation limit, then requests the existing TrackPrompt authorization phrase.
Only after you type that phrase may it obtain credentials, upload the seven
prepared images and call Veo. No cloud setup, permission changes or model
installation happen automatically. Existing signed-in `gcloud`, Vertex access,
an enabled billing project, an existing private bucket, and installed FFmpeg /
ffprobe must be available. Their live cloud readiness was intentionally not checked.

For a fully specified first run:

```powershell
./run-hayale-promo-v2.ps1 -Create -GcpProjectId 'YOUR_PROJECT_ID' -GcsBucket 'YOUR_PRIVATE_BUCKET'
```

**Default is preparation only:** `./run-hayale-promo-v2.ps1` validates the staged
references and regenerates local planning JSON. It cannot launch video creation.
The `TRACKPROMPT_HAYALE_PREPARE_ONLY=1` environment variable also rejects creation
even when a creation flag or exact authorization is supplied.

## Story and references

| Scene | Reference basis | Planned movement |
| --- | --- | --- |
| 1 — storefront reveal | Original full-front photograph, `still-001` | Slow approach; actual awning, doorway, facade and plant racks remain visible |
| 2 — entrance tour | Original flower-display view, `still-017` | Modest dolly through layered plants and bouquets; preserve the compact layout |
| 3 — second interior tour | Original interior/shelf view, `still-007` | Gentle sideways move across orchids, hanging greenery and actual shelves |
| 4 — orchid detail | Pale yellow/blush orchid, `still-112` | Small macro drift and controlled focus transition |
| 5 — bouquet hero | Orange/cream/purple arrangement, `still-103` | Restrained five-degree orbit around the recognizable arrangement |
| 6 — red rose | Single rose, `still-054` | Subtle close push, resolving toward the burgundy logo palette |
| 7 — artistic logo signature | Supplied `logo/hayale-logo.png` | Gold tracing, gentle floral/paper movement, then a legible hold |

The six photographic references were generated with the built-in image tool,
based on inspected source photographs. They are AI-refined interpretations,
not documentary proof that every petal or small background detail is unchanged.
The supplied layout and products remain the subject constraints for Veo.
`reference-prompts.json` records the complete image prompt set; `shot-bank.json`
contains the seven video prompts.

Only **seven unique clips** are requested, each eight seconds, one candidate
per request. Scene 7 is generated once and reused as the intro and outro;
there is no eighth generation. Local finishing blends to the original logo plate
between 3.5 and 4 seconds of that clip to protect the original lettering before
the hold. The generated first seconds still require visual review for letter drift.

The fixed edit has eight events: logo, storefront, two store tours, three floral
scenes, logo. Internal cuts snap to nearby detected audio onsets within 0.35 s.
Small local retimes fit an eight-second source around those cuts. No room or
flower morphing, aggressive transitions, invented signage or people is requested.

## The audio is replaceable; the duration is fixed

Default audio is the supplied `audio/hayale-remaster-17-09-2026.wav`.
Local inspection measured **59.88 seconds, stereo, 44.1 kHz PCM**. Finishing makes
a private 48 kHz stereo derivative and pads **0.12 seconds** to reach 60 seconds.
It does not time-stretch, loop, loudness-normalize or alter the original WAV.
Longer replacement audio is trimmed to 60 seconds; shorter audio is padded.
The film remains exactly **1,440 frames** in every case, including muted output.

Audio, its duration, waveform, onsets, lyrics, and filename are excluded from the
Veo plan digest and request bodies. Only the seven image references are uploaded.
The existing TrackPrompt rhythm analyzer estimates tempo around 107.1 BPM;
onset locations are used as local editorial cues, not asserted musical downbeats
or a promise that Veo-generated movement will land on a beat.

After generation, replace the song without another Veo request:

```powershell
./run-hayale-promo-v2.ps1 -AssembleOnly -AudioPath 'C:/path/to/replacement.wav'
./run-hayale-promo-v2.ps1 -AssembleOnly -Mute
```

Both commands reuse the saved seven verified clips and create a new local delivery.
They do not instantiate a provider or call GCP. Default regeneration of planning
JSON is also independent of the video-generation identity.

## Model, cost and current limits

The target is **Veo 3.1 Quality**, `veo-3.1-generate-001`, 1080p, 24 fps, silent.
Google lists the old Veo 3.0 endpoint as retired on June 30, 2026 and recommends
this replacement: [official migration notice](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/veo/3-0-generate).

The repository's reviewed video-only rate is $0.20 per generated output-second:
7 × 8 × $0.20 = **$11.20 estimated generation cost**. This plan reserves no retry
budget. Its $11.20 reservation cap uses that pricing snapshot; it is not a GCP
account-wide billing limit. Storage and transfer charges are separate. Review
[Google's pricing](https://cloud.google.com/vertex-ai/generative-ai/pricing#veo)
when launching; this preparation did not query billing or quota APIs.

## Persistence, interruption and output

The manual runner uses the existing `MissionControlStore` and
`VideoGenerationController`, including reference-upload receipts, pre-submission
cost reservations, operation names, downloads and media verification. It does
not introduce another database or scheduler. Registration is atomic and a
prepared-job lease prevents two controllers from updating or submitting the
same job concurrently.

Prepared foreground jobs are excluded from backend automatic resume. They are
controlled by this foreground command; cancel with Ctrl+C in its window. Do not
try to control the same prepared job through a different Mission Control instance.
No Codex automation or background monitor is installed.

### Recover an expired gcloud sign-in before submission

If reference upload reports reauthentication is required, run these commands
yourself from the repository directory:

```powershell
gcloud auth login
./run-hayale-promo-v2.ps1 -Create -ResumePreSubmit
```

After signing in, `RESUME-HAYALE-PROMO-V2.cmd` is the double-click equivalent of
the second command. It reuses your still-valid authorization for the same exact
plan, without extending its expiry. Only an expired authorization needs the
exact phrase again. The saved GCP project and bucket are also reused.

This recovery is limited to a recorded **reference-upload failure before Veo
submission**, with no operation/output evidence and unchanged reference bytes.
It archives the original failed attempt and authorization, then reuses that
attempt's existing reservation. It does not erase the failure history, reset
the budget, create an extra paid attempt or retry an uncertain submission.
The permission is consumed durably before upload/submit. Unknown transport
outcomes, operation receipts and already-submitted attempts remain blocked.

Inspect the saved cause without any GCP access or database mutation:

```powershell
./run-hayale-promo-v2.ps1 -Status
```

New upload failures report gcloud reauthentication explicitly when identified;
raw credential-bearing stderr is not printed. The terminal also displays the
saved per-shot cause instead of only “did not reach seven verified scenes”.

Re-running an unchanged plan reuses its deterministic saved job and verified
clips. Existing named operations can resume within their authorization period.
Failed, filtered or uncertain submissions are **never automatically resent**.
They stop for review. If a process crash leaves `foreground-worker-lease.json`,
confirm that exact owning process is gone before recovering the lease; do not
erase job records, receipts or reservations to force another request.

Completed local deliveries appear under:

`.trackprompt-data/video-generation/hayale-flowers-promo/<job-id>/deliveries/v2-<timestamp>-<id>/`

The command prints that directory and records it in
`v2-preparation/latest-delivery.json`. Each delivery includes the 60-second MP4,
FCPXML 1.11, FCP7 XML, EDL, edit sheet, markers, relink map, grade instructions,
source provenance and a verification report. Source and generated provider clips
remain immutable. Final grading and visual acceptance happen in Resolve.

## Prepared assets and evidence

All private assets are inside this repository under:

`.trackprompt-data/video-generation/hayale-flowers-promo/v2-preparation/`

- `references/scene-01.png` through `scene-06.png`: six generated references.
- `references/scene-07.png`: the original logo fitted onto an ivory landscape plate.
- `reference-board.jpg`: a contact board of all seven references.
- `references-manifest.json`: exact source and prepared-image hashes and paths.
- `music-cues.json`, `edit-plan.json`: inspected audio evidence and fixed-duration edit.
- `provider-plan.preview.json`, `request-preview/`: offline requests with an explicit
  configuration-at-launch bucket placeholder; never submit those preview files directly.
- `preparation-report.json`: preparation status and counts.

The original D: media, V1 artifacts and unrelated working-tree changes are retained.
V2 is independent of the Hayale product/CMS pipeline. Offline tests and preparation
are not proof of live GCP permission/quota, Veo output quality, Resolve import,
or a finished video. This handoff stops before video creation.
