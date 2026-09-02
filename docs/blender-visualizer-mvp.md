# Blender Visualizer MVP

## Archived catalogue interaction

Catalogue child analyses preserve the existing cue-sheet and visualizer
contracts. An archived project may register cue sheets, Blender build manifests,
scene manifests, and preview manifests as immutable artifacts, but source audio
remains a separate explicit local input and is never copied into a cue sheet.
The default `abstract-geometry` preset, additive `space-journey` preset, ten
`TP_AUDIO_BUS` controls, and canonical `run-trackprompt-to-blender.ps1` runner
are unchanged. Catalogue ingestion never launches Blender or a production
render.

The Blender Visualizer is a local vertical slice from TrackPrompt analysis to a
saved, audio-reactive `.blend`. It provides the backward-compatible
`abstract-geometry` preset and the cinematic `space-journey` preset. It does not
generate characters, lip sync, a photoreal film, or an automatic full-track
render.

## Workflow

1. Complete a Fast or Deep analysis.
2. In **Blender Visualizer**, choose a preset, author or reset its bounded
   controls, select FPS, curve detail, and event/curve toggles, then download the
   validated visualizer configuration and cue sheet.
3. Choose the original audio file separately. The cue sheet contains no audio
   path and the repository does not copy the audio into an export package.
4. Run the headless builder or invoke the same narrow entrypoint through a
   Blender MCP server.
5. Inspect the diagnostics and bounded preview artifacts before making a final
   rendering decision.

Cue data and visual configuration remain separate. The cue sheet contains
analysis-derived timing and normalized controls; the configuration contains only
a preset, bounded visual parameters, a seed, and safe resolution metadata. See
[space-journey-visualizer.md](space-journey-visualizer.md) for every parameter,
range, default, palette, audio mapping, and manifest field.

## Canonical Windows runner

Use `run-trackprompt-to-blender.ps1` for complete local runs. It owns upload,
job polling, cue export, Blender build, preview, and manifests; copying a job ID
into `build-trackprompt-visualizer.ps1` is a deprecated manual handoff retained
only for targeted recovery of an already-completed job.

The Deep/genre/lyrics commands below require the reviewed full-GPU models to
have been provisioned first with `setup-full-gpu.ps1`. `-BuildStack` rebuilds
current source images but deliberately does not install or redownload models.

Rebuild the full-GPU images from current source and run with the bounded preview:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\run-trackprompt-to-blender.ps1 `
  -AudioPath "C:\absolute\permitted-track.wav" `
  -ConfirmPermission `
  -ConfirmLyricsConsent `
  -BuildStack
```

Build Space Journey with its validated defaults and a bounded preview:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\run-trackprompt-to-blender.ps1 `
  -AudioPath "C:\absolute\permitted-track.wav" `
  -VisualizerPreset "space-journey" `
  -ConfirmPermission `
  -ConfirmLyricsConsent `
  -BuildStack
```

Add `-VisualizerConfigPath C:\absolute\space-journey.json` to supply a partial
or complete configuration. The runner rejects malformed, unknown, non-finite,
out-of-range, or preset/seed-conflicting input and writes the complete
`visualizer-config.resolved.json` artifact before scene construction.

Reuse the current cached images. If their healthy backend is stale, the runner
performs at most one least-destructive backend rebuild before upload:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\run-trackprompt-to-blender.ps1 `
  -AudioPath "C:\absolute\permitted-track.wav" `
  -ConfirmPermission `
  -ConfirmLyricsConsent
```

Pass `-AutoRebuildStaleBackend:$false` when automatic image building is not
allowed; a stale live contract then fails with source/live diagnostics.

Build and validate the scene without rendering preview media:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\run-trackprompt-to-blender.ps1 `
  -AudioPath "C:\absolute\permitted-track.wav" `
  -ConfirmPermission `
  -ConfirmLyricsConsent `
  -SkipPreview
```

Disable lyrics explicitly when no transcript consent is intended; no lyrics
consent switch is needed in this form:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\run-trackprompt-to-blender.ps1 `
  -AudioPath "C:\absolute\permitted-track.wav" `
  -ConfirmPermission `
  -EnableLyrics:$false
```

Every run writes `test-output\system-runs\<timestamp>\run-manifest.json`, the
job response and exports, `visual-cues.json`, `visualizer-config.resolved.json`,
the `.blend`, its sibling build manifest, and (unless skipped)
`preview\preview-manifest.json` plus preview media. Space Journey uses
`trackprompt-space-journey.blend` and `space-journey-preview.mp4`; Abstract
Geometry retains its historical filenames. The newest job UUID is also copied to
`test-output\last-trackprompt-job-id.txt`. Inspect the preserved local job with:

```powershell
$jobId = (Get-Content test-output\last-trackprompt-job-id.txt -Raw).Trim()
Invoke-RestMethod "http://127.0.0.1:8000/api/analyses/$jobId" |
  ConvertTo-Json -Depth 100
```

Do not use that saved ID as the routine start of another build. The canonical
runner records the exact job-to-audio relationship and generated artifacts in
one run directory.

From a clean checkout with the backend development environment installed,
generate the synthetic fixtures, then compile a smoke cue sheet. The first
command creates mathematical signals only; no recorded music is downloaded or
committed.

```powershell
.\backend\.venv\Scripts\python.exe tools\generate_test_audio.py `
  --output-dir test-fixtures

.\backend\.venv\Scripts\python.exe tools\generate_visualizer_smoke.py `
  --audio test-fixtures\arrangement_intro_a_b_a_outro.wav `
  --output test-output\blender-smoke\visual-cues.json `
  --fps 30 `
  --curve-detail compact
```

The smoke compiler validates the written file with the same pure-Python cue
loader used by Blender and prints JSON containing `ok: true`, schema, duration,
FPS, frame end, and cue counts.

Use that generated cue and audio directly for a fully synthetic scene build:

```powershell
$smokeRoot = (Resolve-Path test-output\blender-smoke).Path
$smokeCue = (Resolve-Path test-output\blender-smoke\visual-cues.json).Path
$smokeAudio = (Resolve-Path test-fixtures\arrangement_intro_a_b_a_outro.wav).Path

blender --background `
  --python-exit-code 1 `
  --python blender\build_visualizer.py `
  -- `
  --cues $smokeCue `
  --audio $smokeAudio `
  --preset abstract-geometry `
  --seed 84291 `
  --output (Join-Path $smokeRoot "abstract-geometry.blend")
```

The output name must be new: the builder intentionally refuses to overwrite an
existing `.blend`. The same synthetic fixture can also exercise the canonical
system runner without optional model features:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\run-trackprompt-to-blender.ps1 `
  -AudioPath (Resolve-Path test-fixtures\arrangement_intro_a_b_a_outro.wav).Path `
  -Mode fast `
  -EnableGenre:$false `
  -EnableLyrics:$false `
  -ConfirmPermission
```

Build the scene (replace `blender` with its absolute executable path when it is
not on `PATH`):

```powershell
blender --background `
  --python-exit-code 1 `
  --python blender\build_visualizer.py `
  -- `
  --cues C:\absolute\visual-cues.json `
  --audio C:\absolute\track.wav `
  --preset abstract-geometry `
  --seed 84291 `
  --output C:\absolute\abstract-geometry.blend
```

The builder validates cue JSON and all caller-provided paths before clearing the
scene. It parses arguments after `--`, never executes cue text, never invokes a
shell, attaches the approved audio as `TP_AUDIO` in the Video Sequence Editor,
builds F-curves in one Blender Python call, saves the `.blend`, writes a sibling
manifest, prints compact JSON, and returns nonzero on failure.

Render representative stills and a bounded preview clip. The plan uses 10
seconds for a source at least that long and the complete duration for a shorter
source; it never expands to the full duration of a longer track.

```powershell
blender --background C:\absolute\abstract-geometry.blend `
  --python-exit-code 1 `
  --python blender\render_preview.py `
  -- `
  --output C:\absolute\preview `
  --width 1280 `
  --height 720 `
  --ffprobe C:\absolute\ffprobe.exe
```

The preview uses the actual section/transition/energy/vocal evidence stored in
the scene plan. It does not render the full track. When a Blender build cannot
select its movie encoder, the CLI can use an explicit FFmpeg executable with
`--ffmpeg C:\absolute\ffmpeg.exe`. That fallback renders a temporary PNG
sequence, invokes FFmpeg with an argument array and `shell=False`, requests H.264
plus AAC only when an audio strip is present, verifies the output, and removes
the temporary frames after success. The manifest reports whether audio was
muxed; it never silently claims an audio track.

`render_preview.py` resolves executable links to their real file. It discovers
`ffprobe` on `PATH` unless an absolute `--ffprobe` is supplied; when an absolute
`--ffmpeg` fallback is supplied, a sibling `ffprobe` is preferred. A movie run
returns nonzero unless ffprobe confirms video, the expected bounded duration,
and audio presence matching the `TP_AUDIO` strip. Use `--skip-clip` only when a
still-only diagnostic is explicitly intended.

## Scene contract

The importer creates an Empty named `TP_AUDIO_BUS` with these bounded animated
properties:

```text
master_energy  drum_energy  bass_energy  vocal_energy  other_energy
low_band       mid_band     high_band    brightness    transient_activity
```

Continuous controls use linear F-curve interpolation. Missing Fast-mode stem
curves use the declared fallbacks from the cue-sheet documentation; the manifest
and diagnostics record every fallback rather than relabeling it as stem
evidence. Timeline markers use `TP_SECTION_<id>` and
`TP_TRANSITION_<id>`.

Both presets create predictable common collections:

```text
TP_WORLD              TP_CAMERAS       TP_LIGHTS
TP_PRIMARY_GEOMETRY   TP_RINGS         TP_SHARDS
TP_VOCAL_ELEMENTS     TP_BACKGROUND    TP_DEBUG
```

Those nine collections are the shared non-regression contract. Space Journey
adds organized destination, starfield, nebula, travel-path, and environment
collections and records their presence and counts in its manifest.

Its bounded procedural system contains a displaced/emissive central core, four
rings, a 42-shard field, a vocal-reactive wire element, procedural background,
three lights, a section palette system, transition pulses, and a gently animated
camera. Bass controls core scale; master controls displacement and broad motion;
drums/transients pulse rings and shards; high band controls shard spread;
vocals control the secondary element; and brightness/low band shape palette and
depth. There is no per-beat object creation, rapid full-screen flash, severe
camera shake, external asset, or unbounded particle system.

Space Journey instead uses a layered destination, partial orbital arcs,
deterministic parallax star meshes, bounded debris, shader-based atmosphere,
spectral vocal ribbons, and a target-tracking section-aware camera. Macro
keyframes follow section progression and transition energy; existing audio-bus
drivers provide clamped bass breathing, ring/drum accents, vocal atmosphere,
secondary environmental motion, and fine high-band glints. Foreground coverage
is controlled by `ringOcclusion` rather than global opacity alone.

The same cue sheet, preset, Blender version, seed, and preset parameters produce
the same scene structure and animation plan. The seed drives ring variation,
star/debris placement, travel accents, and material-noise variation; palette
selection and the section-aware camera plan remain explicit parameter/cue
decisions. The seed is stored in scene custom properties, the manifest, and
diagnostics.

Preview rendering defaults to Eevee and bounded material/geometry complexity.
Diagnostics include the Blender version, timeline, FPS, camera, TrackPrompt
collections, object/material/F-curve counts, control names, cue schema, preset,
seed, audio-strip status, fallback use, approved output file, preview frames, and
render engine. Space Journey diagnostics additionally include its complete
resolved configuration, defaulted fields, deterministic plan, stable camera
target, preset-specific collection checks, and role-labelled preview frames.

The sibling build manifest contains boolean contract checks for the cue frame
range and FPS, `TP_AUDIO_BUS`, its ten controls and F-curves, the nine required
collections, the `TP_AUDIO` strip, camera, scene F-curves, and saved `.blend`.
`preview-manifest.json` records the preset/configuration, planned and rendered
still roles/frames with byte sizes, the movie frame range, planned and probed
duration, encoder, stream presence, mux status, and corresponding boolean
checks. Space Journey selects six well-distributed section-aware stills and a
representative interior clip rather than clustering review frames at the outro.
`ok: true` therefore means the artifacts were verified, not merely requested
from Blender.

## Tests

Pure Python tests under `blender/tests/` validate cue compatibility, privacy,
paths, fallbacks, preset resolution, parameter bounds, deterministic section
direction, preview planning, and seed planning without `bpy`.
`blender/tests/headless_scene_check.py` opens a generated scene in real Blender,
validates the selected preset and required scene contract, and renders one sample
frame. All committed audio fixtures remain generated and synthetic.
