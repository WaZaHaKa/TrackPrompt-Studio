# Hayale V2.1 local preparation

**Goal:** Prepare a manual local replacement for the blocked GCP V2 run. Do not execute inference or a real delivery. Preserve all V2 bytes and history.

**Architecture:** A project-specific adapter reuses TrackPrompt's ComfyUI client, production lease, bounded subprocesses, atomic JSON and DaVinci exporter. The existing local music-video orchestrator assumes sixteen shots tied to audio duration; this promo needs seven image-conditioned requests and eight events on a fixed 1440-frame clock. V2's provider and finishing entrypoints hardcode GCP/V2 identities and remain untouched.

**Specification:** The user's September 17 instructions and `docs/codex-handoffs/trackprompt-hayale-promo/`. Landscape, storefront plus two tours, six floral/store scenes and one logo generation reused twice; local audio optional; original D: sources immutable.

## Implementation and checks

- [x] Add `backend/tests/video_generation/test_hayale_v21.py` first. Test the 7/8/1440 contract, isolated paths, safe prepare, workflow settings, prompt submission intent, no blind retry, cached output integrity, and postprocessing commands. Run it and confirm missing implementation fails.
- [x] Add `video-projects/hayale-flowers-promo/v2.1/prepare.py`: snapshot existing V2 reference/cue/edit data into independent private preparation; bind existing model/tool identities; create photoreal Wan graphs from the installed registered graph. Prepare only copies files and hashes local inputs.
- [x] Add `runtime.py`: owned loopback ComfyUI server on port 8189, writable input/output/temp/user/database/cache beneath V2.1, serial requests with durable intent and receipts, bounded waits, immutable raw frames. Refuse occupied ports and ambiguous submissions. Reuse `ComfyUIClient` and `production_lease`.
- [x] Add `finish.py`: RIFE v4.6 129-to-193 frames, photographic x4plus then downsample/trim to 192 frames at 1080p24; original-logo lock and local audio padding; reuse `export_davinci_package`. Every attempt and delivery has a separate directory and technical verification.
- [x] Add `run.py`, `run-hayale-promo-v21.ps1`, and `RUN-HAYALE-PROMO-V21.cmd`. Default PS action is Prepare. Create explicitly starts the foreground pipeline. Resume reuses verified scene receipts; NewRun explicitly preserves and starts a new attempt. Status is disk-only. No cloud option or silent fallback/download.
- [x] Run focused offline tests with synthetic media only, parser/lint/type checks, required repository checks and a separate static review. Verify protected V2 snapshot and pre-existing dirty files after changes. Commit only this adapter/tests/documentation. Evidence: `video-projects/hayale-flowers-promo/v2.1/verification-report.json`; full suite retains the exact 22 pre-existing Andromeda failures.

## Quality decision

Installed RTX 3060 12 GiB, 32 GiB RAM, Wan 2.2 I2V A14B high/low Q5_K_M, UMT5 FP8 on CPU and Wan 2.1 VAE. Default 832x480, 129 frames at 16 fps, Euler/simple, CFG 3.5, shift 5, 40 steps split 20/20, tiled VAE. An explicit low-memory profile uses 640x368 with the same sampler steps; never select it silently. Photographic Real-ESRGAN x4plus and RIFE v4.6 are already installed. No new models/dependencies.

The requested long-clip profile has not been qualified by inference. Free GPU/RAM were insufficient during inspection; operator startup must recheck. Final visual quality and Resolve import remain operator review items. The preparation does not assert local output equivalence to Veo.
