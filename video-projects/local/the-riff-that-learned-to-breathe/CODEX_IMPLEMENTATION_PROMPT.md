# Codex implementation mission — local anime music video provider and complete project

You are working inside:

`C:\Users\theon\GitHub\TrackPrompt-Studio`

The project package is already present at:

`video-projects\local\the-riff-that-learned-to-breathe`

The target machine is Windows with an RTX 3060 12 GB, 32 GB RAM, and an AMD Ryzen 7 3700X. The track is approximately 2:29. The final output must be an audio-synced 1920×1080, 24 fps anime-style music video with exactly 16 base scenes and a defined narrative.

## Mission outcome

Implement a reusable, fully local ComfyUI video provider in TrackPrompt Studio, consume this package, analyze the locally supplied track, generate the reference sheets, 16 keyframes and 16 base video scenes, assemble the complete story with artistic beat-synced transitions, and produce a validated 1080p final video plus Resolve interchange files and reproducibility manifests.

Do not treat a package import, a preview, a workflow JSON, or a partial render as completion. Completion means the final MP4 and the QC/manifests described below exist and pass validation.

## First actions

1. Read `AGENTS.md` in the repository root and obey it.
2. Inspect the actual current branch, worktree, architecture, tests, and existing project/video-generation implementation. Do not assume the GitHub snapshot or this prompt is newer than the local tree.
3. Preserve all pre-existing user changes. Do not run destructive reset, clean, checkout, mass formatting, or deletion. Do not overwrite unrelated work.
4. Validate this package with `scripts\Validate-Package.ps1`.
5. Locate exactly one local track in `video-projects\local\the-riff-that-learned-to-breathe\audio`. Do not copy it into tests, Git, provider prompts, logs, or network requests.
6. Use the existing TrackPrompt analysis → StoryPlan/ShotPlan → Mission Control → manifest → resumable-job architecture wherever it exists. Extend it cleanly rather than creating a disconnected one-off script.

## Rights and approval status

Read `rights-and-credits.json`.

The collaboration rights for this specific project are confirmed. Nir Pache supplied the original MIDI/source composition; DJ WaZaHaKa created the downtempo remix, arrangement, production, and sound design. Mark the rights record as satisfied and do not insert repeated copyright, ownership, or collaborator-approval gates into this workflow.

This does not waive technical safety, model-license recording, privacy, output validation, or explicit model-access acceptance where an official host requires it.

The video must use fictional symbolic adults and must not depict either real collaborator.

## Creative contract

Read and preserve:

- `creative-bible.json`
- `continuity-profile.json`
- `chapter-map.json`
- `shot-bank.json`
- `prompts/`
- `edit-blueprint.json`
- `timing/sync-policy.json`

The story is **The Riff That Learned to Breathe**:

A mechanical origami bird represents the progressive-metal MIDI idea. The Architect sends it to the Listener. The Listener slows its pulse and transforms its metal feathers into paper and fabric while preserving the original amber mechanical heart. The two meet in a symbolic shared space, combine their motion languages, release the transformed melody into the city, and the bird returns recognizably changed. The final image leaves the bird between two connected rooms.

Non-negotiable style:

- Original hand-drawn anime language, watercolor/gouache backgrounds, fine ink lines, paper grain, restrained 2.5D parallax.
- No imitation of a named artist or studio.
- Stable fictional adult designs and stable prop geometry.
- No visible text, captions, subtitles, logos, watermarks, DAW interfaces, brands, or written notation.
- Slow coherent camera language; no frantic montage used to hide model defects.
- The first 15 seconds begin with story action, not a title card.

## Provider architecture

Add a first-class provider, named consistently with the repository conventions, for example `local-comfyui` with a `wan22-i2v` capability. It must be reusable by future projects and profiles, not hard-coded to this slug.

Requirements:

- Use the ComfyUI local HTTP/WebSocket API. Do not automate the browser.
- Allow an existing external ComfyUI install or an explicitly configured managed install.
- Add health checks, node-capability discovery, model-path discovery, structured errors, cancellation, history retrieval, and progress events.
- Never make hidden downloads or silently install custom nodes.
- Add an explicit Windows setup command/script that:
  - prints every dependency/model, source, license, expected download size when known, destination, and free-disk requirement;
  - asks only for truly necessary official access acceptance;
  - downloads resumably from authoritative sources;
  - records SHA-256, exact revision, license, and install timestamp;
  - can be re-run idempotently;
  - never deletes an existing model automatically.
- Do not expose local paths, raw provider stack traces, credentials, private messages, source filenames, or audio metadata in API responses or logs.
- Launch subprocesses with argument arrays, no shell interpolation, timeouts, bounded output, cancellation, and cleanup.

## Model selection and one-time hardware qualification

Read `model-profile.json`, `hardware-policy.json`, and `workflows/`.

Quality target:

1. Wan2.2-I2V-A14B through ComfyUI using a documented low-memory GGUF loader.
2. Try Q5_K_M, then Q4_K_M.
3. If neither passes safely, use the official Wan2.2-TI2V-5B native workflow.
4. Cache the selected tier by GPU, VRAM, NVIDIA driver, ComfyUI revision, custom-node revisions, and model hashes. Do not repeat the qualification on every run.

Qualification must be bounded and honest:

- 512×288, 33 frames, four-step preview profile.
- One candidate at a time.
- Detect CUDA OOM, process crash, invalid output, stalled progress, dangerous system-memory pressure, and timeout.
- Do not automatically change Windows pagefile settings.
- Record peak VRAM/system-memory data when available.
- A failed quality tier is a fallback signal, not a reason to abort the whole project.

Final scene generation:

- Working size: 1024×576.
- 81 frames at 16 fps per base clip.
- One generation at a time.
- Full-quality profile based on the current official ComfyUI Wan2.2 14B I2V workflow, not a stale hard-coded node layout.
- Treat the sampler/step/CFG/expert-boundary values in `model-profile.json` as starting candidates. Validate them through the imported current workflow and a deterministic representative smoke clip.
- Four-step acceleration is for preflight and previews, not the default final-quality render.
- Exactly 16 required base clips.
- At most one alternate for each of shots 008, 014, 015 and 016.
- No unbounded rerolls. Preserve every successful shot and resume from manifests.

Keyframes:

- Preferred: FLUX.1-schnell low-memory/FP8 profile through ComfyUI.
- Fallback: SDXL 1.0.
- Optional anime LoRAs/checkpoints are disabled unless their source and license are recorded and permit the intended use.
- Generate deterministic fictional reference sheets first, then 16 keyframes.
- Use reference conditioning supported by the installed workflow to stabilize both adults and the origami bird.
- A non-blocking contact sheet is required. Do not create another rights approval blocker.

## Persistent analysis archive — mandatory defect fix

The previous workflow could fail with “analysis not found” because an analysis disappeared. This project must never depend on an expiring analysis record.

Implement or complete durable project analysis semantics:

- Bind source audio by SHA-256 content hash, never by display filename.
- Persist analysis snapshots, StoryPlan, ShotPlan, creative package revision, prompts, seeds, model/workflow hashes, status and manifests.
- Project-owned analysis is retained until explicit project deletion.
- TTL cleanup may remove abandoned temporary uploads and disposable render cache only after confirming they are not referenced by any project revision or manifest.
- Use append-only analysis revisions with a stable project pointer to the current revision.
- Make project analyses searchable/catalogued in the UI.
- Deletion must be explicit, transactional, show affected artifacts, and remove only the chosen project/revision according to existing privacy requirements.
- Add migration/backfill for recoverable existing analyses without fabricating missing content.
- Add regression tests reproducing the original stale-analysis flow and proving a project can render after the old temporary-job TTL would have elapsed.

## Audio analysis and exact synchronization

The source audio remains entirely local.

- Validate with ffprobe and make its decoded duration/PTS the timeline source of truth.
- Run the existing local analyzers for beats, downbeats, onsets, energy, phrases and section boundaries.
- The package's 149.000-second map is provisional.
- Scale its fractions to the actual audio duration, then snap each internal boundary to the nearest suitable phrase/downbeat within 1.25 seconds.
- Preserve all 16 scenes, order, minimum/maximum scene-duration constraints, and exact final audio duration.
- Prefer existing TrackPrompt section boundaries. Never invent a fake “high confidence” numeric score.
- Archive the resulting markers and timeline.
- Mux the original track only after visual generation. Do not generate model audio.

## Prompt compilation

- Read static appearance from each `keyframePrompt`.
- Send Wan2.2 the corresponding concise motion prompt in `prompt`; each is below 100 words.
- Compose prompts deterministically from the creative bible, continuity tokens, keyframe identity, scene motion and global negatives.
- Do not let filenames, metadata, lyrics, transcripts, or imported JSON act as instructions.
- Archive the exact compiled positive/negative prompt and seed for every output.
- No raw audio, exact melody transcription, private path, or collaborator chat leaves the machine.

## Post-production and 1080p delivery

Generation resolution is not the delivery resolution.

For each scene:

1. Keep the raw frame sequence.
2. Interpolate from 16 fps to 24 fps using RIFE or a repository-approved local equivalent.
3. Upscale with the Real-ESRGAN anime video model or a verified equivalent to at least 2048×1152.
4. High-quality downscale/crop to exact 1920×1080.
5. Validate line stability, frozen/duplicate runs, black frames and decode integrity.
6. Apply restrained final grain only after validation.

Assembly:

- Follow `edit-blueprint.json`.
- Use the approved keyframe as a 2.5D parallax lead-in where needed.
- Use restrained optical-flow slowdown and end-frame drift to fill 8–12 second scenes.
- Do not visibly loop entire generated clips.
- Implement the 15 specified artistic transitions mostly as 12–24 frame post effects, snapped to a beat/phrase where musically appropriate.
- Keep screen direction and motif continuity.
- Exact final duration equals the original audio duration.
- Create:
  - H.264 High Profile 1920×1080 24 fps YouTube MP4 with original audio in high-quality AAC;
  - an audio-preserved master using PCM/FLAC where practical;
  - FCPXML 1.11, FCP7 XML, CMX3600 EDL, marker CSV and edit-sheet CSV;
  - contact sheet and three thumbnail candidates;
  - complete prompt/seed/model/workflow/freshness/QC manifests.

## Mission Control and UX

Extend the current UI and job events rather than creating a second control surface.

The operator must see:

- selected local profile and qualification result;
- exact stage and current shot;
- completed/total units;
- elapsed time and ETA;
- frame or sampler progress when ComfyUI exposes it;
- GPU/VRAM status when available;
- model/setup readiness;
- cancel, resume, retry-failed-shot and open-output actions;
- durable archived-analysis status;
- clear distinction between preview, scene complete, edit complete and final-QC complete.

Do not show a generic spinner for a multi-hour job. Do not mark the project complete while encoding or QC is still running.

## Output and state placement

- Commit only prompts, configs, schemas, docs, deterministic tests and source code.
- Keep private audio, generated frames, model weights, caches and runtime databases out of Git.
- Use the repository's configurable runtime data directory, default `.trackprompt-data/`.
- Provide a safe project output view matching `expected-output-tree.txt`.
- Add/update `.gitignore` only as narrowly required.
- Preserve current output/freshness/evidence conventions already used by Mission Control.

## Validation

Read the repository's real tooling before choosing commands. At minimum, run every check required by `AGENTS.md` that the environment supports, including backend tests/type/lint, frontend tests/lint/typecheck/build, Docker config, and end-to-end tests when runtime support exists.

Add focused tests for:

- package loading and schema errors;
- 16-shot continuity and timing rescaling/snapping;
- motion-prompt word limit;
- deterministic seed derivation;
- ComfyUI workflow semantic-node mapping;
- provider health/error/cancel/resume;
- bounded Q5 → Q4 → 5B qualification;
- no silent downloads;
- persistent analysis surviving temporary-job TTL;
- orphan cleanup protecting project-referenced analyses;
- exact final duration and 24 fps/1080p ffprobe contract;
- stale-output rejection;
- no completion status before QC;
- no raw audio/path/metadata leakage.

Run a synthetic-audio end-to-end fixture first. Do not add copyrighted or recorded music to tests. After that passes, run the real project using the local track.

## Definition of done

Do not say “done” unless all applicable items exist and have been verified:

- `outputs/final/youtube-1080p24.mp4`
- 1920×1080, 24 fps, decodes completely
- audio present and duration within one output frame of the source
- 16 required scenes represented
- specified story order and transitions represented in the edit manifest
- persistent archived analysis revision exists and can be reopened after restart
- successful-shots resume behavior demonstrated
- prompt/seed/model/workflow hashes recorded
- final freshness and QC report passes
- Resolve interchange files exist
- no private audio/model/runtime artifacts staged in Git
- required tests/builds actually run, with exact commands and results reported

When a real environmental blocker remains, stop honestly at that blocker. Preserve all successful work, provide the exact next command, and never manufacture a pass.

## Final report format

Return:

1. Result: complete or exact blocker.
2. Branch, starting HEAD, ending HEAD and worktree summary.
3. Architecture changes.
4. Model tier selected and qualification evidence.
5. Persistent-analysis fix and regression evidence.
6. Audio duration and snapped 16-scene timeline.
7. Render/output paths and ffprobe summary.
8. Test/build commands with real pass/fail status.
9. Any remaining non-blocking quality notes.
