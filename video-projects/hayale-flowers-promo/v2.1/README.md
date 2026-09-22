# Hayale Flowers V2.1 — temporary local creation

For the preserved incident run **344ab3d2444c**, use the repository-root
`continue-hayale-v21.ps1` entrypoint. It pins this run, checks the original
owner/provider evidence, uses stable detached execution, and never selects a new
run. See `docs/wnp/INCIDENT_ROOT_CAUSE.md` and `docs/wnp/ACCEPTANCE.md` for the
selected-console handoff gate. The original creation instructions below are
historical preparation instructions, not the continuation path for this incident.

This is a separate local version of the prepared V2 promo. V2's code, inputs, GCP target, failed attempt and launchers are unchanged. No GCP account or payment is used by V2.1.

## Run it yourself

From the existing TrackPrompt-Studio repository:

```powershell
.\run-hayale-promo-v21.ps1 -Action Create
```

Or double-click **RUN-HAYALE-PROMO-V21.cmd**. This starts the actual local generation and finishing workflow. Keep the console open. No further authorization phrase is needed for this local run.

The PowerShell launcher's default action is offline preparation:

```powershell
.\run-hayale-promo-v21.ps1 -Action Prepare
.\run-hayale-promo-v21.ps1 -Action Status
```

`Prepare` only verifies local files and creates the independent input package. `Status` reads saved receipts from disk. Neither starts ComfyUI, loads models, contacts a provider, or renders video. Preparation was performed; actual Hayale V2.1 generation has not been run by the assistant.

## Same promo workflow

The seven prepared first-frame images are copied byte-for-byte from V2 into `.trackprompt-data/hayale-v21/prepared/references/`. Six are the selected/refined photographic references; the seventh is the supplied logo on its landscape plate.

| Unique scene | Treatment |
| --- | --- |
| 001 | Real storefront and visible facade; restrained approach |
| 002 | Tour of the entrance, bouquets and plants |
| 003 | Complementary interior tour and display shelves |
| 004 | Orchid detail |
| 005 | Bouquet hero |
| 006 | Red rose close-up |
| 007 | Artistic logo animation; generated once and reused as intro/outro |

The final edit has **eight events, exactly 1,440 frames at 24 fps, 1920×1080 landscape, 60 seconds**. It retains V2's locally measured onset-aligned cuts. The song does not set the video length. The 59.88-second remaster receives 0.12 seconds of local padding; audio is neither uploaded nor used as model conditioning. Movement inside generated shots is not promised to hit musical beats. Use `-Silent` with `-Action Create` for a music-free export with the same timing.

The generated logo motion blends into the unchanged supplied logo plate between 3.5 and 4 seconds, then holds. The same completed eight-second source appears at both ends of the edit. This protects the final readable mark; inspect the animated opening for lettering distortion.

## Local quality profile

Prepared for the inspected **RTX 3060, 12 GiB VRAM; Ryzen 7 3700X; 32 GiB RAM**, using installed models and tools only:

- Wan 2.2 image-to-video A14B high/low experts, Q5_K_M quantization.
- CPU UMT5 XXL FP8 text encoder and the A14B-compatible Wan 2.1 VAE.
- Default `quality`: 832×480, 129 lossless source frames at the intended 16 fps; 40 Euler/simple steps split 20/20, CFG 3.5, shift 5, fixed scene seeds.
- Sequential scenes, dynamic weight offloading, no node cache, 1.5 GiB reserved VRAM; VAE decode uses 256-pixel spatial tiles and 16-frame temporal tiles.
- After ComfyUI exits and releases its GPU memory: RIFE v4.6 produces 193 interpolated frames. Photographic Real-ESRGAN x4plus upscales them; Lanczos resizing and a small center crop deliver 1920×1080. Each source master is trimmed to 192 frames/eight seconds, in ProRes HQ with explicit BT.709 conversion.
- Existing TrackPrompt FCPXML 1.11, FCP7 XML, EDL and FFmpeg assembly create the final package.

**1080p is the delivery resolution; generation itself is 832×480.** The local approach uses full sampling and the installed larger model for detail, at substantial runtime cost. These 129-frame settings have not been tested by inference on this machine. The older short-clip qualification does not prove this profile fits or looks good. Budget hours rather than expecting a fast cloud-like render; no measured completion estimate is available.

Before Create, close other GPU/model workloads yourself. The manual launcher checks for at least **10,000 MiB free VRAM, 18 GiB free RAM and 80 GiB free disk**. These are startup checks, not a guarantee against later out-of-memory errors. It does not stop your apps or delete files.

If the quality run exhausts memory, an explicitly selected lower-memory profile is available:

```powershell
.\run-hayale-promo-v21.ps1 -Action Create -Profile low-memory -NewRun
```

That uses 640×368 with the same 129 frames/40 steps and finishing. It retains the first run. It is never selected automatically and is not claimed to have been qualified either. `-NewRun` generates a new separate batch, including scenes completed in a previous batch; use it only intentionally.

## Preservation and recovery

All V2.1 writes stay under `.trackprompt-data/hayale-v21/`; source media and installed tools/models are read in place. Runs have independent IDs, per-scene receipts and lossless raw-frame hashes. Audio, private photos, prompts and generated media remain untracked runtime data.

The runner starts an owned ComfyUI process on **127.0.0.1:8189**, with local input/output/temp/user/database/cache directories. An occupied port is refused. Before each mutating request, the socket owner must be this launch or its Windows interpreter descendant. API nodes and all custom nodes except the installed GGUF loader are disabled. Offline model-library settings prohibit automatic model downloads. There is no cloud fallback.

Each scene writes a durable submission intent before queuing. There are **no automatic generation retries**. On completion, the exact prompt history and 129 lossless frames are retained. Rerunning the same Create command:

- Verifies and reuses completed raw scenes and finished masters.
- Recovers frames from proven-completed saved history and this run's local ComfyUI output, without running a model.
- Continues scenes that have never been submitted.
- Stops if a submitted scene lacks completion evidence. An interrupted/unknown submission is not assumed to have failed and is not silently rerolled.

Ctrl+C stops the owned process tree and preserves receipts and outputs. For a native failure, read the retained `server-*.log`, `provider-error.json`, or stage stderr log named by the run. Partial postprocessing/delivery folders are preserved; a later manual run makes a new finishing attempt while reusing verified raw scenes. Completed packages are hash-checked before reuse. Code, profile or audio-mode changes require an explicit separate run.

### Reconcile or reconnect an existing run

For the existing `344ab3d2444c` run, use:

```powershell
.\run-hayale-promo-v21.ps1 -Action Recover -RunId 344ab3d2444c
```

Recover reads the live queue/history and verifies its prompt/workflow/client identity against the saved submission. It verifies completed raw-frame hashes first, records a timestamped observation, and authorizes only the compatible orchestration-code update without changing `run.json`, prepared inputs, scene prompts or model settings. It never submits, starts, stops or restarts ComfyUI. A held production lease keeps the existing foreground worker in charge.

If the old foreground worker has exited while its ComfyUI process is still healthy:

```powershell
.\run-hayale-promo-v21.ps1 -Action Resume -RunId 344ab3d2444c
```

Resume adopts the existing pending/running/completed prompt under the exclusive production lease. It stops after recovering that raw checkpoint, before later scenes. Repeating Resume reuses that checkpoint. If the existing foreground worker still holds the lease, Resume reports that worker and makes no duplicate submission. A missing or failed prompt remains a reconciliation case; two empty history reads alone do not prove that history was never evicted, and do not authorize a reroll.

After the recovered boundary has been verified, ordinary `-Action Create` can continue the same run with its original profile/audio options. It accepts only the exact compatible-code receipt produced by Recover. It borrows the verified live server, or starts a replacement only when the recorded process is stopped, the port is closed, and no incomplete submitted scene remains. It never restarts a healthy server. Existing server receipts/logs are retained. GPU finishing checks the provider's idle state and waits for model allocations to be released, including finishing-only resumes.

The updated runner writes `stage.json` and `supervisor.json` to distinguish queue wait, generation/history wait, frame download, raw verification and finalization. A raw checkpoint remains authoritative when a supervisor heartbeat is old. `Status` labels saved stage/observation timestamps and keeps the immutable submission receipt state separate. A legacy worker already in memory continues running its original code; Recover can provide an accurate timestamped observation without interrupting it. Do not use the old submission receipt or an open log's modification time as a liveness test.

The compatible orchestration update is an exception to the separate-run rule above: **do not use `-NewRun` to bypass recovery of this existing run**. Preparation, finishing, profile or audio-policy changes still require separate review rather than silently rebinding existing generation evidence.

## Delivery

The final console report identifies the unique output folder beneath `runs/<run-id>/deliveries/`. It contains:

- `hayale-promo-v21-60s.mp4`
- `hayale-promo-v21.fcpxml`, `.xml`, `.edl`
- Edit sheet, markers, relink map, derived timeline media and local audio master
- Source provenance, grade plan, frame/duration checks and verification report

The runner verifies decoded video frame count, frame rate, dimensions, audio format, XML media references, edit timing, EDL endpoint, coverage and unchanged source hashes. A technical pass still requires your artistic review and an actual Resolve import. Inspect facade/sign fidelity, store layout, flower identities, temporal shimmer, interpolation artifacts, color consistency and logo lettering before publishing.

## Implementation reuse

This project adapter uses `app.local_video.comfyui.ComfyUIClient`, `production_lease`, the shared bounded subprocess runner/atomic JSON helpers and `app.video_generation.exporter`. The installed workflow snapshot comes from TrackPrompt's registered `wan22-i2v-a14b-q5-v1` graph; only the scene prompts, duration, profile, tiled decoding and lossless-frame output are adapted.

The general local music-video orchestrator assumes sixteen shots and an audio-duration clock. The original V2 entrypoint is GCP-specific and its finisher hardcodes V2 export names. A small project adapter supplies the seven-scene fixed-clock contract without changing either implementation. No new models or dependencies were installed.

Model/workflow background: [official Wan 2.2 repository](https://github.com/Wan-Video/Wan2.2), [ComfyUI's Wan 2.2 workflow](https://docs.comfy.org/tutorials/video/wan/wan2_2), [GGUF loader](https://github.com/city96/ComfyUI-GGUF), [RIFE NCNN implementation](https://github.com/nihui/rife-ncnn-vulkan), [Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN). Existing local model licensing records remain in `docs/model-licenses.md`.
