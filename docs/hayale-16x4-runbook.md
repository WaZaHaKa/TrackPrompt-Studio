# Hayale 16 scenes / 64 seconds

Run from TrackPrompt-Studio in Windows PowerShell 5.1 or PowerShell 7.
The new version is `video-projects/hayale-flowers-promo/v3-16x4/scenario.json`.
The completed V2.1 film and run `344ab3d2444c` are independent historical artifacts.

## Check and select

```powershell
.\run-hayale-16x4.ps1 -Action Check
.\tools\reauth-gcp.ps1 -Mode Local -NonInteractive
```

Both commands are local checks. They do not upload media, contact a video model,
log in, alter global Google settings or start a render. The auth result presents
existing video project/bucket and account configuration as unadopted hints.
Use the installed stable Python 3.12 runtime with Pillow, HTTP/FastAPI/Pydantic
dependencies, FFmpeg and ffprobe. The launcher does not install software. An
existing interpreter can be passed with `-PythonExecutable` for checks; detached
production must match the verified repository process broker's stable runtime.
`TRACKPROMPT_MC_FFMPEG_PATH` and `TRACKPROMPT_MC_FFPROBE_PATH` select existing media
tools if PATH resolution is insufficient.

Select each scene's actual material. Customer slots are 005, 009 and 013; logo
slots are 001 and 016. The other eleven slots correspond to shop, bouquets,
product, craft and detail material listed in the scenario. Selection verifies
source bytes and stores private paths/hashes under local runtime storage.

```powershell
.\run-hayale-16x4.ps1 -Action Select -Scene shot-005 -Asset 'D:\selected\customer-one.jpg' -PromotionalUse
.\run-hayale-16x4.ps1 -Action Select -Scene shot-001 -Asset 'D:\selected\approved-logo.png' -PromotionalUse
.\run-hayale-16x4.ps1 -Action Select -Scene shot-016 -Asset 'D:\selected\approved-logo.png' -PromotionalUse
.\run-hayale-16x4.ps1 -Action Select -Scene shot-003 -Asset 'D:\selected\bouquet.jpg' -PromotionalUse -CloudUpload
```

`-PromotionalUse` approves use in this edit. `-CloudUpload` separately approves
Google upload of that selected shop/product image during a later authorized
batch. Neither flag starts an upload. Customer photos and logos use local
composition, keeping the entire photo/artwork intact; their cloud flags are
rejected. Select three actual customer photos; invented people/testimonials are
not part of this plan. Unselected media are never discovered for upload.

Selected existing video can replace a paid scene explicitly:

```powershell
.\run-hayale-16x4.ps1 -Action Select -Scene shot-003 -Asset 'D:\selected\verified-bouquet.mp4' -PromotionalUse -RetainedVideo -SourceIn 2
```

The source must contain at least four seconds after that in-point. Source FPS,
duration and input hash are recorded separately from the 96-frame timeline slot.
An unchanged valid local clip is reused without generation.

## Music arrangement

Keep the original soundtrack file. Select an explicit arrangement totaling 64
seconds using `source-in:duration` segments. The simplest case requires a source
that actually contains 64 seconds:

```powershell
.\run-hayale-16x4.ps1 -Action Music -Asset 'D:\selected\original-soundtrack.wav' -MusicSegment '0:64' -ApproveArrangement
```

For a shorter original, choose and review musical edit points. Multiple segments
may be supplied, but their total must be exactly 64 seconds and each must remain
inside the source. This is an explicit musical edit, not automatic looping,
silence padding or time stretching. The source hash stays bound to the music
receipt; a separate hash identifies the arranged PCM. AAC delivery bytes need
not equal source bytes. Analysis, arrangement and mux remain local.

## Preview and generation

```powershell
.\run-hayale-16x4.ps1 -Action Prepare -Profile still-animatic
```

This creates/reuses one canonical Mission Control job and prepares local clips.
Save the returned `jobId`. Four profiles share the scene plan and job system:

| Profile | New provider work | Delivery size |
| --- | --- | --- |
| still-animatic | None; all selected photos/retained clips composed locally | 720p |
| motion-preview | Scenes 003 and 006 only, unless selected retained video | 720p |
| full-draft | Up to eleven first-frame generations | 720p |
| final | Up to eleven first-frame generations; explicit separate plan | 1080p |

The selected provider is `veo-3.1-fast-generate-001` in `us-central1`, one sample,
video only, one concurrent paid request, maximum one attempt per scene. Native
four-second first-frame requests are the selected mode. A deliberately longer
six/eight-second source is separately priced and trimmed to four seconds. Asset
reference-image arrays and unsupported modes are rejected by this adapter.
Model capabilities were checked against the [official model specification](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/models/veo/3-1-generate)
on 2026-09-22; cloud entitlement and successful generation still require live evidence.

The checked [Google pricing table](https://cloud.google.com/vertex-ai/generative-ai/pricing)
supplies the video-only rates used by this repository's output-second estimate:
Fast 720p USD 0.08 and 1080p USD 0.10. The default motion selection estimates
USD 0.64; eleven four-second clips estimate USD 3.52 draft / USD 4.40 final.
Retained local sources reduce paid work. Source-duration changes alter the
estimate. Storage/retention/transfer charges are excluded and depend on your
bucket, location and usage. These are admission estimates, not invoices.
Review current pricing before authorizing; a budget alert cannot stop execution.

For Google access, choose the intended account/project/bucket from the local
diagnostic and run the explicit login flow:

```powershell
.\tools\reauth-gcp.ps1 -Mode Reauth -Account 'chosen@example.com' -ProjectId 'chosen-video-project' -Bucket 'chosen-private-bucket'
.\tools\reauth-gcp.ps1 -Mode Cloud -NonInteractive
.\run-hayale-16x4.ps1 -Action Prepare -Profile motion-preview
```

The auth tool isolates TrackPrompt CLI credentials from global gcloud and GA4
ADC. It never creates a bucket, enables an API, changes IAM/quotas or creates
service-account keys. Read checks distinguish accessible metadata from object
read/create permissions. A model/region declaration is not a billable probe.
See [authentication effects and remedies](gcp-authentication.md).

Review the plan and displayed exact phrase, then authorize once and start:

```powershell
.\run-hayale-16x4.ps1 -Action Authorize -JobId '<saved-job-id>' -Confirmation '<exact displayed phrase>'
.\run-hayale-16x4.ps1 -Action Start -JobId '<saved-job-id>'
.\run-hayale-16x4.ps1 -Action Status -JobId '<saved-job-id>'
```

No paid render is authorized by installing or testing this feature. Start uses
the existing detached Windows broker, proves process independence and waits for
the actual controller to accept this job/plan. Launching a PID alone is not
acceptance. Browser closure does not own that worker. Mission Control's saved
video job shows the same 16 source slots and bounded cost; the dedicated launcher
owns its start/stop/finishing flow. Preview completion never launches final work.

## Resume, stop and finish

```powershell
.\run-hayale-16x4.ps1 -Action Resume -JobId '<saved-job-id>'
.\run-hayale-16x4.ps1 -Action Stop -JobId '<saved-job-id>'
.\run-hayale-16x4.ps1 -Action Export -JobId '<saved-job-id>'
.\run-hayale-16x4.ps1 -Action Verify -JobId '<saved-job-id>'
```

Resume retains accepted operation names, completed outputs and reservations.
Expiration of approval for new spending does not prevent recovery of an already
accepted operation. An uncertain submission is never automatically replayed.
Intentional new attempts require a newly bounded plan; seeds are not deduplication
keys. Identity changes require reconciliation rather than silently switching.

Stop requests that the current legitimate worker stop local scheduling. It does
not claim provider cancellation or refund. Status reports saved worker heartbeat
separately from provider observations; polling status cannot refresh either.
An abandoned worker lease can be explicitly reconciled using `RecoverWorker`
and its retained lease token only after process/start identity proves it stopped.
Live/unknown owners are never stolen merely because a timestamp is old.

Export requires all 16 verified sources and the approved explicit music edit.
It verifies source trims, produces exact cuts, muxes local music, fully decodes
the final file and checks frames, streams and A/V alignment before publication.
Local finishing failures reuse retained generation. Resolve FCPXML/XML/EDL,
timeline and private verification evidence are written beside the delivery.
Technical verification does not assert human creative approval. Review likeness,
logo/Hebrew readability, motion and musical edit points before using the film.

## Companion

Use the existing WNP host and secure SSH transport. Follow the explicit node/run
selection and check commands in [shared WNP coverage](wnp/SHARED-WORKFLOWS.md).
Keep the existing completed Hayale node selected until intentionally switching
observation to the new job, or use an independently initialized node store.
Do not copy a node identity/journal to a second writer.

Transport ONLINE, worker heartbeat, last provider observation, verified source
clips and verified edited scenes are separate facts. GCP exposes no local GPU
metrics or precise sampling ETA. WNP outage does not block rendering. No prompts,
photos, personal filenames, music, full operation names, paths or credentials enter
WNP. This change does not replace the Companion application. Live UI acceptance
remains required where its real installation is reachable.
