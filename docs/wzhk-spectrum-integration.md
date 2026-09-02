# WZHK Spectrum integration and production runbook

## Status and boundary

WZHK Spectrum is an optional local Windows renderer beside the existing TrackPrompt
render paths. Canonical Scattered production uses one owned browser compositor for
WebGL2 geometry and the WZHK logo/artist/title only. Milestone 3.7 removes visible
spectrum bars, ribbon, metadata and diagnostics from production. Web Audio still
drives the geometry. The Rainmeter presentation remains the static fallback, with
new production copies also using identity-only foreground. The renderer never uploads media,
downloads runtime tools, edits the immutable vendor snapshot, or treats preparation
as a finished video.

The approved Scattered master is about 196.620 seconds. This is intentional. The
96-bar musical grid ends at 192.000 seconds; the remaining audio is a render tail,
not a duration mismatch or an invented musical section.

## Timing model

| State | Bars | Time |
|---|---:|---:|
| Intro | `[1, 33)` | `00:00.000–01:04.000` |
| Main | `[33, 89)` | `01:04.000–02:56.000` |
| Outro | `[89, 97)` | `02:56.000–03:12.000` |
| Post-grid tail | none | `03:12.000–ffprobe master EOF` |

Production persists `gridDurationSeconds`, `masterDurationSeconds`, and
`tailDurationSeconds`. A master is valid when its ffprobe duration is at least the
192-second grid and its tail remains inside the configured safety bound. The visual
end fade starts at `max(192, masterDuration - finalFadeSeconds)`. Geometry stays
audio-reactive while its density, deformation, glow and brightness resolve through
the tail to EOF. The approved audio is never truncated,
stretched, normalized, limited, EQ'd, or faded.

Preview reads the configured external player's position and may hold a fixed
intro/main/outro state. Production rejects every fixed preview override and uses
the canonical timeline with the job-owned `trackprompt-production-clock` file.

## Milestone 3.5 static fallback

The background is generated locally from the typed visual preset as three layers
of deterministic Rainmeter `Shape` paths plus a thin line lattice. A bounded
integer seed, fragment count, stroke width, and maximum motion distance produce
byte-identical geometry for identical inputs; no raster background, downloaded
asset, stock media, or external generator is involved.

Intro reveals only a sparse far layer. Main introduces the mid and near layers,
stronger cyan/violet line energy, and restrained depth drift. Outro disperses the
near layer, while the post-grid tail reduces the field to residual far fragments
and settles all motion by master EOF. Motion is derived from the existing preview
or production timeline, so it remains deterministic and does not create a second
clock. Historical 3.5 copies displayed `96 BARS`. New 3.7 production copies omit
the spectrum, baseline, progress and metadata meters; internal controller state
labels remain available only in preview/debug presentation text.

## Milestone 3.6 Generative Geometry

`generative-geometry` materializes a reusable `wzhk-generative-geometry` subsystem
into every generative workspace. A token-authenticated HTTP server binds only to
`127.0.0.1`, serves an exact asset allowlist, and launches an owned Edge/Chrome
profile. CSP, safe routes, and trusted enum values prohibit external URLs, path
traversal, arbitrary JavaScript, and shader injection.

The GPU renderer uses `gl_VertexID` point sprites. Every node keeps its index in a
fixed 64×64 UV domain, so shape changes interpolate coordinates instead of fading
between images. Production uses 4,096 nodes; preview uses 1,600; the bounded high
profile uses 6,400. CPU reference samplers and GLSL share ten canonical families:

```text
sparse-field       lissajous          matrix-field
wave-surface       torus              twisted-torus
trefoil-knot       superformula       spherical-lattice
dispersed-field
```

Morph easing is explicit (`smoothstep`, `smootherstep`, cubic, or sinusoidal).
Correspondence never performs nondeterministic nearest-neighbor matching. The
shader adds restrained orbit/dolly, depth fade, LED core/halo, deterministic node
color, and a seed-driven propagation wave.

Low frequencies influence scale, mids deform/twist the surface, highs affect node
brightness, transients launch topology waves, and overall energy affects movement.
A same-origin hidden audio element analyzes the copied approved master while owned
ffplay remains the audible clock source. The single browser compositor also draws
stable WZHK branding (and, in historical 3.6, a 56-band spectrum) on one canvas. This was selected after
live dual-window alpha and monitor-composition qualification proved unreliable;
the single owned HWND was then qualified at 1080p60.

At 120 BPM, one beat is 0.5 seconds and one 4/4 bar is 2 seconds. Scattered uses 14
declarative continuous transitions:

- Intro (`00:00–01:04`): sparse field → Lissajous → matrix → spherical lattice → torus.
- Main (`01:04–02:56`): torus → twisted torus → trefoil knot → superformula → spherical lattice → wave surface → matrix → twisted torus.
- Outro (`02:56–03:12`): twisted torus → spherical lattice → dispersed field.
- Tail (`03:12–03:16.619796`): dispersed field → sparse residuals while node retention and brightness fall to EOF.

Shape, A→B morph, section, simulated-audio, and shape-lab overrides are preview
only. Production rejects them, keeping the canonical design unchanged. Actual FPS,
frame time, GPU identity, rendered updates, and estimated dropped renderer updates
are runtime evidence and never contaminate the deterministic workspace hash.

## Milestone 3.7 geometry-first composition

The schema `3.1.0` visual preset contains a strict, finite `composition` contract
with revision `scattered-geometry-first-3.7`. Its production flags require logo,
artist and title and forbid spectrum bars, spectral ribbon, technical metadata
and section labels. `IdentityOverlay` draws the transparent identity canvas every
frame. It does not request spectrum bands or draw a baseline in production, even
if diagnostic flags are supplied. Status, fatal-error and developer controls are
hidden from the first production paint; failures still reach the backend.

The old shader rectangle covering the left 36% / upper 58% is gone. Two bounded
elliptical masks protect only the logo and text. Each uses Gaussian falloff
`exp(-2 * distanceSquared)`; retained intensity is the product of
`1 - strength * weight`. Core brightness has a 0.42 floor in the preset and halo
suppression is separate. Neither the cores nor halo can be blacked out. The same
weight locally reduces deformation. The background remains spatially continuous,
including behind the identity, without an opaque recovery panel.

Full-canvas framing is explicit (normalized center, shape scale and depth), not a
hardcoded right-hand offset. Small shape families have normalized extents; tilted
tori, stronger point cores and restrained halos make the silhouette legible.
The deterministic eight-key envelope smoothly interpolates density, brightness,
scale and deformation: restrained formation in Intro, full authority in Main,
loosening in Outro and residual dissolution through the intentional tail. Low,
mid, high and transient features drive scale, twist/local deformation, light and
topology propagation; the bounded propagation configuration now reaches GLSL.
The existing shape order, seed, approved master and capture/mux path are retained.
The presentation shader aligns superformula longitude/latitude polarity with the
neighboring sphere so corresponding nodes do not cancel through the origin at the
two-minute morph midpoint. This is a deterministic presentation correspondence
fix, not a change to the musical timeline or an additional visual layer.

Preparation freezes configuration and source copies in a new UUID workspace.
Historical jobs, state history, final MP4s and recovery evidence are not rewritten.
This is a focused Scattered refinement, not the Milestone 4 track factory.

## Source and private runtime layout

`vendor/wzhk-spectrum-visualizer/` is a pinned, immutable third-party snapshot.
Every workspace operation hashes it before and after copying. License and upstream
provenance remain in the private copy, while all branding and generated controller
changes occur below the ignored data root:

```text
.trackprompt-data/wzhk-spectrum/
  assets/logo/
  assets/track/
  jobs/<uuid>/
    contract.json
    design.json
    generation.json
    manifest.json
    skin/
    geometry/
      index.html
      runtime.js
      runtime.css
      shaders/
      config/runtime-config.json
    assets/
    capture/
    logs/
    output/
      review-frames/
```

If Rainmeter requires its configured Skins directory, TrackPrompt stages only a
job-specific `TrackPrompt-WZHK-<uuid>` folder. A marker records the job ID and
workspace hash. An occupied or mismatched path fails closed. Cleanup deactivates
and removes only that marked deployment; it does not terminate the user's
Rainmeter process or alter unrelated skins.

## Dependencies and availability

The backend independently reports preview and capture readiness using:

- `READY_FOR_PREVIEW` and `READY_FOR_CAPTURE`;
- `MISSING_RAINMETER`, `MISSING_FFMPEG`, or `MISSING_CAPTURE_PROVIDER`;
- `INVALID_WORKSPACE`, `MISSING_MASTER`, or `INVALID_MASTER_DURATION`.

Rainmeter, FFmpeg, ffprobe, ffplay, and Edge/Chrome are discovered locally. Nothing
is silently installed. Generative preflight compiles both shaders and reports
`READY`, `WEBGL2_UNAVAILABLE`, `GPU_RENDERER_UNAVAILABLE`,
`SHADER_COMPILE_FAILED`, `PERFORMANCE_INSUFFICIENT`, or `BROWSER_UNAVAILABLE`.
`READY` requires measured renderer cadence; CFR output is never treated as WebGL
performance evidence. A transient unconfirmed browser handshake receives one
bounded preflight retry.

On Windows, the owned Edge command disables its compatibility-layer self-relaunch
with a job-local launch argument. The watchdog therefore retains the real browser
PID; global browser settings and unrelated windows are not changed. When a PATH
shim is unreliable, set `FFMPEG_PATH` and `FFPROBE_PATH` to the actual installed
executables before qualification/production; ffplay is discovered beside FFmpeg.

Capture capability is verified from FFmpeg's filters and encoders. FFmpeg Windows
Graphics Capture targets the single job-owned browser compositor in generative
mode or the Rainmeter presentation in fallback mode. It writes video-only
1920×1080/60 CFR Matroska. Verified `h264_nvenc` is preferred; `libx264` is the
supported fallback.

## Production sequence and synchronization

The production manager uses the persisted state sequence:

```text
WORKSPACE_READY -> CAPTURE_PREFLIGHT -> CAPTURE_READY -> CAPTURING
-> CAPTURE_COMPLETE -> MUXING -> VALIDATING -> COMPLETE
```

Failures and cancellations remain inspectable. A valid capture plus capture
manifest is reused after a mux/validation failure, so a safe retry does not replay
or recapture the master.

After explicit operator confirmation, TrackPrompt:

1. revalidates dependencies, workspace identity, and the copied approved-master hash;
2. launches either the owned browser compositor or the Rainmeter fallback;
3. resolves that exact owned window and arms video-only FFmpeg capture;
4. waits for the capture artifact and FFmpeg media-progress clock to advance, starts
   owned ffplay playback at time zero, and publishes a 16 ms host-monotonic visual clock;
5. records capture-start, master-zero, capture-stop, and the FFmpeg capture-media
   timestamp observed at the master-zero process boundary;
6. stops only job-owned processes and deterministically trims the measured capture lead;
7. muxes the copied original approved master as AAC 320 kbit/s, without audio DSP;
8. probes and validates the final MP4, then extracts deterministic review frames.

This clock evidence has `host-monotonic-process-boundary` precision. It is honest
process-boundary telemetry, not sample-accurate hardware-loopback measurement.
Dropped capture-frame count remains unknown when the provider cannot report one.
Generative runtime evidence separately records renderer FPS, frame time, rendered
updates, and estimated dropped renderer updates.

## Artifacts and validation

Important artifacts record type, data-root-relative path, SHA-256, size, creation
state, and provenance. Private absolute paths are not returned by the API. The
capture intermediate is crash-resilient Matroska and contains no final soundtrack.
The fallback filename remains `dj-wazahaka-scattered-wzhk-spectrum-visualizer.mp4`.
New 3.7 composition workspaces use
`dj-wazahaka-scattered-wzhk-generative-geometry-milestone-3-7.mp4`.
Historical generative workspaces without that revision and the 3.6 recovery path
retain `dj-wazahaka-scattered-wzhk-generative-geometry-milestone-3-6.mp4`.
Every new composition uses a fresh job; prior renders are not overwritten or
reused as a visual source with old bars/masks already baked in.

Final validation requires a readable MP4 with video and audio streams, 1920x1080,
60 fps within tolerance, duration matching ffprobe's approved-master duration,
non-zero frame count, and a sane non-empty artifact. Review frames are extracted
at 00:10, 01:03, 01:05, 02:00, 02:55, 02:57, 03:11, 03:13, and master EOF minus 0.5 seconds. These checks
support review but do not replace human/operator visual approval.

Historical 3.6 visual sanity still checks its foreground/geometry/spectrum and
keeps matched 3.5/3.6 images under `output/comparison/`. The new composition-review
workflow writes only under the new job's `output/comparison-3.7/`: nine matched
3.6 frames, nine side-by-sides, a comparison manifest and a geometry-first sanity
report. It validates source-video/config/design hashes, re-extracts all nine
current timestamps, and compares decoded RGB hashes (PNG compression need not be
identical). Checks include non-black identity, distinct frames, tail decay and
aggregate left/center/right/lower geometry occupancy. Pixel luminance alone does
not prove absence of bars or aesthetic quality; configuration/runtime tests and
rendered-frame inspection supply separate evidence. User aesthetic approval stays
pending (`visualQaRequired=true`).

After a new job reaches `COMPLETE`, call `create_composition_review` with the
known 3.6 final-video hash, then `validate_composition_review` and
`register_composition_review` from `generative/composition_review.py`. Registration
adds the 20 comparison/sanity artifacts to the new job's typed artifact list.
It preserves all existing records, state and approval fields. An exact replay
validates again without writing; conflicting paths fail closed. Creation refuses
an existing comparison directory, so never overwrite historical review evidence.

A completed validated capture can be reused for deterministic branding
stabilization without replaying the master. Recovery records the source job/hash,
failed target capture, overlay geometry, final validation, and byte-identical AAC
elementary-stream evidence. Recovery evidence is not a claim that a failed capture
became valid; the corrected output is independently probed and reviewed.

## API and operator workflow

```text
GET  /api/renderers/wzhk-spectrum
POST /api/renderers/wzhk-spectrum/jobs
GET  /api/renderers/wzhk-spectrum/jobs/<uuid>
POST /api/renderers/wzhk-spectrum/jobs/<uuid>/capture-preflight
POST /api/renderers/wzhk-spectrum/jobs/<uuid>/production
POST /api/renderers/wzhk-spectrum/jobs/<uuid>/cancel
```

Prepare a production workspace, run capture preflight, and review the exact timing
and dependency report. `Create Final Visualizer` is enabled only for
`CAPTURE_READY`. It displays whether the browser compositor or Rainmeter fallback
will launch, that the complete master will play, and that GPU capture will run.
The production request also requires the typed confirmation phrase
`START WZHK SCATTERED CAPTURE`.

## Verification

```powershell
Set-Location .\backend
python -m pytest tests\test_wzhk_spectrum.py tests\test_wzhk_spectrum_production.py
python -m pytest tests\test_wzhk_generative_geometry.py tests\test_wzhk_geometry_browser_runtime.py
python -m pytest tests\test_wzhk_spectrum_composition.py tests\test_wzhk_composition_review.py tests\test_wzhk_geometry_composition_runtime.py
python -m ruff check app\renderers tests\test_wzhk_spectrum.py tests\test_wzhk_spectrum_production.py
python -m mypy app\renderers
node --check ..\tools\wzhk-spectrum\runtime\runtime.js

Set-Location ..\frontend
npm test -- --run src\features\renderers\RendererSelector.test.tsx
npm run lint
npm run typecheck

Set-Location ..
pwsh -NoProfile -ExecutionPolicy Bypass `
  -File .\tools\wzhk-spectrum\tests\Invoke-WZHK-SpectrumPreflight.Smoke.ps1
pwsh -NoProfile -ExecutionPolicy Bypass `
  -File .\tools\wzhk-spectrum\scripts\Invoke-WZHK-SpectrumPreflight.ps1 `
  -ProjectRoot "C:\Users\theon\GitHub\TrackPrompt-Studio"
```

The full repository checks in `AGENTS.md` remain required before handoff.
