# Hayale: sixteen generated scenes, all media work in GCP

From TrackPrompt-Studio, run:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\FINISH-HAYALE-GCP.ps1
```

Or double-click `FINISH-HAYALE-GCP.cmd`, which explicitly invokes PowerShell 7 (`pwsh.exe`). The installed Python 3.12 runtime and Google Cloud CLI are reused. The launcher reconstructs the repository, isolated authentication binding, exact project/bucket/region, private controller directory and delivery directory in a fresh shell. It does not use Windows ADC, service-account key files or an unrelated gcloud configuration.

The existing Microsoft PowerShell 7.6.6 WindowsApps installation was verified with the persisted user/machine PATH, without Codex's injected environment. The CMD wrapper also resolves the system, per-user or WindowsApps installation directly, so Codex does not need to remain open.

The foreground window performs setup, submits a bounded Batch job, observes it and downloads/opens the verified result. Once accepted, the cloud job continues without the window or Codex. If the window closes, run the same command again to reconnect and receive the result. A local controller lock prevents two windows operating the same checkout simultaneously; cloud object generations protect against competing controllers elsewhere.

## Fixed scope

The visually reviewed source map is `video-projects/hayale-flowers-promo/v4-gcp-only/source-map.json`. It binds fifteen distinct originals under `D:\HayaleFlowers\promo-2026-09-15`: one logo, three customers, two exteriors, three product photos, three atmosphere photos and three interior-tour photos. It also binds the approved original soundtrack by SHA-256. Filenames are locations, never classification rules or prompt instructions. The launcher only hashes and uploads these originals. No directory-wide media upload occurs.

The order is logo, exterior 1, exterior 2, customer 1, product 1, vibe 1, tour 1, customer 2, product 2, vibe 2, tour 2, customer 3, product 3, vibe 3, tour 3, logo. All sixteen slots use Vertex image-to-video. The old `FINISH-HAYALE.cmd`, its native jobs and its 11-cloud/5-local film remain historical; they cannot satisfy this workflow.

The canonical run ID is derived from the immutable cloud-only plan and selected source hashes. A private GCS binding prevents a changed map or scope from silently becoming another authorized run. Editing that map is not a way to reset attempt limits. Prompts, source hashes, prepared seed identities, accepted operation names, terminal responses, transfers and final receipts remain in the private run prefix.

## Cloud execution

The launcher enables the required APIs and creates/reuses only the named Hayale resources in `nexus-core-481713`: Artifact Registry repository `trackprompt-hayale`, service accounts `trackprompt-hayale-build` and `trackprompt-hayale-worker`, source-addressed Cloud Build images and deterministic Batch jobs. IAM additions target those dedicated identities: model invocation, Batch reporting/logging, repository push/pull and prefix-scoped GCS object access. Existing policy bindings and etags are preserved. No persistent VM, downloaded key or global gcloud change is required. Missing administrator permissions produce one explicit blocker.

The build archive uses an explicit source-code allowlist. It never includes the checkout's private runtime, media, credentials, Git history or other projects. Images are selected by immutable registry digest. The production worker is one `e2-standard-4` Batch task, with a 12-hour lifetime and at most two task retries. At most three successive worker job identities may recover a failed infrastructure task; all share the same cloud manifest and generation attempt history.

The worker verifies its attached Google metadata identity before invoking media tools. Pillow prepares all 1920×1080 seeds in GCP with uniform scaling and source-derived background extension. Customer images retain their whole subjects; the logo uses its original artwork and matching edge background. The one explicitly reviewed shelf crop is applied only in GCP. No generated lettering is used to prepare the logo.

Veo requests use `veo-3.1-fast-generate-001`, `us-central1`, four seconds, native 1080p, 16:9, one sample and no generated audio. Real adult customer scenes use `allow_adult`. An output with the wrong native dimensions, timing or frame count fails rather than being upscaled or substituted. Subject identity and lettering preservation are prompted constraints; provider creative fidelity is not claimed by the technical verifier.

The current documented generation estimate is USD 6.40 for 64 seconds at USD 0.10 per generated second, checked 2026-09-22. The manifest records the rate/date/source and observed attempts. Compute, build, storage and transfer are additional metered usage; their exact future amounts and the eventual invoice are unknown. These figures are telemetry, not the obsolete USD 4.40 admission ceiling. See [Google pricing](https://cloud.google.com/vertex-ai/generative-ai/pricing) and the [Vertex image-to-video contract](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/video/generate-videos-from-an-image).

## Recovery guarantees

GCS `ifGenerationMatch` conditions protect the authoritative manifest. A submission intent precedes every provider POST. Accepted operation identities have separate immutable receipts. Poll failures resume that operation; transfer failures resume that object generation. A lost acknowledgement without a recoverable accepted-operation receipt remains explicitly uncertain and cannot trigger another generation. The operation's output prefix is inspected as evidence without inventing an operation identity.

Terminal output failures retain their evidence and permit a maximum of three real generation attempts per scene using the same image and restrained intent. Only narrowly proven unsupported optional settings can be corrected; the native resolution, duration, adult-person contract and audio policy cannot be relaxed. There are no creative alternates, unlimited retries or RTX fallback.

The existing repository legacy-reconciliation capability remains available and tested: it examines all attempts, keys facts by job + shot + attempt, retains original jobs/reservations, and uses transaction/recheck semantics. The distinct Batch workflow does not consult unrelated historical SQLite jobs as a global lock. It has its own explicit single-worker/single-generation scope in the authoritative cloud manifest.

An interrupted finishing task resumes against identical verified inputs. The cloud creates an immutable package containing the verified master and its two reports before publishing the delivery objects, so a partially published delivery does not require another generation or encode.

## Finishing and delivery

The original soundtrack object is immutable. In GCP, the approved arrangement uses source segments `(0, 43.24)`, `(38.76, 4.48)`, `(43.24, 16.28)` seconds and five-millisecond in-place join fades. It records the original hash, segment list, fade rule and arranged-audio hash. It does not stretch, append the opening blindly, generate music or pad silence.

Cloud finishing assembles sixteen separate generated clips at exactly 96 frames per slot. The actual H.264/AAC file must have 1920×1080 dimensions, constant 24 fps, 1,536 frames, the exact 64-second frame timeline, audio starting at zero and successful complete video/audio decode. Receipts bind all scene slots to distinct provider outputs and their selected sources. The authoritative verifier runs inside Batch.

Only after that passes does Windows download the MP4, verification JSON and delivery report into `%USERPROFILE%\Videos\Hayale-Final-1080P`. Windows compares object-generation-bound SHA-256 receipts and the cloud verification, preserves any different prior delivery under `history`, installs the verified files and opens the MP4. It performs no media decode, resizing, rendering, audio edit or encode. Download verification is transport verification only.

WNP uses the existing enrolled node when available and projects content-free durable cloud progress. It never creates a replacement node, submits work or blocks cloud execution. No photos, prompts, tokens, private paths or full provider operation names are projected. A disconnected Companion is not evidence of a stopped cloud job.

## Maintainer checks

The optional `-Check` switch hashes the bound originals and reads cloud setup state without uploading media or starting generation. `-SetupOnly` builds the cloud worker without uploading production inputs. `-Smoke` performs a bounded real object round trip in Batch: create, metadata/read/download/hash, prefix listing, pinned generation read, conditional JSON replacement, metadata update and disposable-object cleanup. It cannot invoke Veo. The normal command automatically requires this image-bound proof; no extra operator command is required. See `docs/hayale-gcp-object-io-repair.md` for the production observer race and its repair.

Synthetic tests cover source roles, cloud execution guards, frame/sample timelines, operation recovery, unknown submission protection, retry bounds, transfer resumption, generation fencing, native PowerShell/CMD invocation, and historical delivery preservation. A synthetic encode is test evidence; real production completion requires the cloud receipts and downloaded-byte proof for this distinct run.
