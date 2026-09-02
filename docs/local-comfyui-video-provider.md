# Local ComfyUI video provider

TrackPrompt's `local-comfyui` provider uses the local ComfyUI HTTP and WebSocket APIs. It does not automate a browser, send audio to ComfyUI, install custom nodes, or download models during normal API requests.

## Configuration

Set `TRACKPROMPT_COMFYUI_URL` to a loopback endpoint (default `http://127.0.0.1:8188`) and `TRACKPROMPT_COMFYUI_ROOT` to the reviewed ComfyUI installation. Non-loopback endpoints fail closed unless `TRACKPROMPT_COMFYUI_ALLOW_NON_LOOPBACK=true` is deliberately set for a private local network deployment.

Run the setup plan before downloading anything:

```powershell
.\tools\setup-local-comfyui.ps1 -ComfyUIRoot 'D:\ComfyUI' -Tier Q5_K_M
```

Download mode is explicit and license-gated:

```powershell
.\tools\setup-local-comfyui.ps1 -Mode Download -AcceptModelLicenses -ComfyUIRoot 'D:\ComfyUI' -Tier Q5_K_M
```

If no external install exists, the same command supports an explicit managed install pinned to the reviewed official ComfyUI release:

```powershell
.\tools\setup-local-comfyui.ps1 -Mode Download -AcceptModelLicenses -InstallManagedComfyUI -ComfyUIRoot 'D:\TrackPrompt-ComfyUI' -Tier Q5_K_M
```

The script uses pinned source revisions, resumable transfers, SHA-256 recording, idempotent existing-file preservation, and a 60 GiB working reserve. It never deletes or overwrites an existing model. Q4 and native 5B are separate explicit runs if qualification falls back.

GGUF tiers require a separately reviewed City96/ComfyUI-GGUF installation. RIFE and Real-ESRGAN are also explicit dependencies; configure `TRACKPROMPT_RIFE_PATH` and `TRACKPROMPT_REALESRGAN_PATH` after reviewing their releases and licenses.

For the managed `D:\TrackPrompt-ComfyUI` installation, install or repair the pinned GPU and post-production dependencies with:

```powershell
.\tools\setup-local-generation-dependencies.ps1 -ComfyUIRoot 'D:\TrackPrompt-ComfyUI'
.\tools\start-local-comfyui.ps1
```

The dependency lock records CUDA PyTorch, the exact ComfyUI-GGUF commit, and the exact RIFE and Real-ESRGAN releases and hashes. The launcher binds only to `127.0.0.1`, disables cloud API nodes and metadata, and validates the HTTP, object-info, queue, and WebSocket surfaces before reporting ready.

## Workflow registration

Export the current ComfyUI workflow in API format and register it with `POST /api/mission-control/video/local/workflows`. TrackPrompt maps start image, prompt encoders, Wan latent, high/low samplers, model loaders, and output nodes by semantic class/title/input roles. It does not depend on numeric node IDs.

Readiness is available at `GET /api/mission-control/video/local/provider/readiness`. It reports only safe node/model identities and device measurements, never local installation paths.

## Project lifecycle

`POST /api/mission-control/video/local/projects/prepare`:

1. validates a package under `video-projects/local/`;
2. binds its sole audio asset by SHA-256;
3. reuses a matching persistent TrackPrompt analysis or runs the existing fast local analyzers;
4. scales and snaps all 16 scene boundaries to measured structure/beat/onset candidates;
5. archives immutable analysis, StoryPlan, ShotPlan, prompts, seeds, timeline, package revision and a private audio copy;
6. reports provider setup or qualification as a technical blocker without losing the archived analysis.

Project analysis revisions remain until explicit project deletion. The deletion API first returns an affected-artifact preview and exact confirmation phrase.

Qualification is sequential and bounded: Q5_K_M, Q4_K_M, then native 5B. Its cache key includes GPU, VRAM, driver, ComfyUI revision, custom-node revisions and exact model hashes. Four-step 512x288/33-frame probes are never treated as final-quality renders.

Final-quality defaults remain 1024x576, 81 frames, 16 fps and one generation at a time. Post-production uses RIFE v4.6 to 24 fps, Real-ESRGAN `realesrgan-x4plus-anime` at 4x working resolution, and high-quality Lanczos scaling to 1920x1080. A resumable manifest preserves completed units and cannot enter `complete` until final QC passes.

## Real qualification and production handoff

The current project qualification is a real FLUX-to-Wan-to-post run, not a structural probe. Its report and playable 1080p24 clip are under `video-projects/local/the-riff-that-learned-to-breathe/outputs/qualification/`. Importing the report into Mission Control registers the exact successful API workflows and hash-validates the final clip before readiness can become `fully_production_ready`:

```powershell
& '.\backend\.venv\Scripts\python.exe' -m app.local_video.cli sync-qualification --project-id 'the-riff-that-learned-to-breathe' --repository-root "$PWD" --data-root "$PWD\.trackprompt-data" --ffmpeg 'C:\Users\theon\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.1-full_build\bin\ffmpeg.exe' --ffprobe 'C:\Users\theon\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.1-full_build\bin\ffprobe.exe'
```

Inspect the full-track plan without launching inference:

```powershell
& 'D:\TrackPrompt-ComfyUI\.venv\Scripts\python.exe' '.\tools\render-local-anime-production.py' --plan
```

Only an explicit `--start` crosses the production boundary. The launcher reads the exact current timeline from Mission Control's private archive, creates four canonical reference sheets, conditions every scene keyframe from a canonical reference image, runs the qualified Q5 Wan workflow one scene at a time, preserves intermediates and resumable state, crossfades the 16 masters, restores `audio/track.wav` as the master soundtrack, and verifies exact-duration 1920x1080 at 24 fps. The complete render is intentionally not started by setup or qualification.
