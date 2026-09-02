# Space Journey visualizer

> V1 identity note: this document describes `space-journey`. The additive story-driven preset is `space-journey-story`; it is preview-only and cannot reuse V1 calibration or authorization. See [Cinematic Visualizer V2](cinematic-visualizer-v2.md).

`space-journey` is TrackPrompt Studio's cinematic Blender preset for long-form
electronic music. It coexists with the original `abstract-geometry` preset; the
default remains `abstract-geometry` so existing API, CLI, MCP, and PowerShell
calls continue to behave as before.

The preset uses the existing minimized `TrackPromptVisualCueSheet 1.1.0` and
the existing ten-property `TP_AUDIO_BUS`. Visual configuration is a separate,
versioned JSON contract. Preset parameters are never added to the cue sheet and
do not alter its privacy boundary.

## Selecting the preset

The canonical Windows workflow accepts a preset and an optional JSON file:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\run-trackprompt-to-blender.ps1 `
  -AudioPath "C:\absolute\permitted-track.wav" `
  -BlenderExe "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" `
  -VisualizerPreset "space-journey" `
  -ConfirmPermission `
  -ConfirmLyricsConsent `
  -BuildStack
```

Omitting `-VisualizerPreset` keeps the Abstract Geometry default. Supply
`-VisualizerConfigPath C:\absolute\space-journey.json` to override one or more
Space Journey defaults. The runner validates and resolves that file before
Blender scene construction and writes the complete result as
`visualizer-config.resolved.json` inside the unique run directory. An explicit
preset or seed that conflicts with the file is rejected instead of being
silently replaced.

The browser's Blender Visualizer panel can validate and download the same
configuration contract. It does not launch Blender, begin a render, or expose a
browser-to-local-executable bridge. Cue-sheet export remains a separate action.

## Configuration contract

Configuration schema `1.0.0` uses camel-case public parameter names:

```json
{
  "schemaVersion": "1.0.0",
  "preset": "space-journey",
  "parameters": {
    "cameraDistance": 18,
    "cameraOrbitSpeed": 0.15,
    "ringThickness": 0.06,
    "ringOcclusion": 0.2,
    "palette": "andromeda",
    "glowStrength": 1.8,
    "shardDensity": 0.35,
    "fogDepth": 0.5,
    "bassResponse": 1.2,
    "drumResponse": 0.9,
    "vocalResponse": 0.65
  },
  "seed": 84291
}
```

`preset: "space-journey"` is required to select this preset. Its individual
parameters, seed, and schema version may be omitted. Resolution fills the
complete parameter set, records `defaultedParameters`, and rejects unknown keys,
unsupported presets, invalid palettes, non-finite values, invalid types, and
values outside these bounds:

| Parameter | Type | Allowed value | Default | Effect |
| --- | --- | --- | ---: | --- |
| `cameraDistance` | number | 8–40 scene units | 18 | Baseline distance from the tracked destination. |
| `cameraOrbitSpeed` | number | 0–0.5 track rotations | 0.15 | Slow macro orbit over the complete cue timeline; zero is stationary. |
| `ringThickness` | number | 0.02–0.20 scene units | 0.06 | Thickness of orbital arcs and gate structures. |
| `ringOcclusion` | number | 0–1 | 0.20 | Bounded foreground-arc coverage, inclination, and depth bias. |
| `palette` | enum | See palette list below | `andromeda` | Coherent material, atmosphere, and light family. |
| `glowStrength` | number | 0–4 | 1.8 | Bounded emission and compositor glow contribution. |
| `shardDensity` | number | 0–1 | 0.35 | Bounded debris count/visibility; it never creates an unbounded object set. |
| `fogDepth` | number | 0–1 | 0.50 | Layered atmospheric depth without opaque full-frame fog. |
| `bassResponse` | number | 0–2 | 1.2 | Core breathing and inner gravitational pulse. |
| `drumResponse` | number | 0–2 | 0.9 | Local ring, lane, and travel accents. |
| `vocalResponse` | number | 0–2 | 0.65 | Spectral ribbons and atmospheric veils; zero-vocal tracks remain valid. |

Supported palettes are:

```text
andromeda
deep-space
cyan-violet
violet-magenta
monochrome-blue
dark-amber
```

The validation-only API is:

```text
POST /api/visualizer/config/resolve
```

It returns a complete configuration with the resolved seed, defaulted field
names, and bounded warnings. The endpoint never starts Blender and does not
persist configuration in a TrackPrompt job.

## Visual direction

Space Journey builds one evolving world rather than unrelated scenes:

- a dark subdivision-5 destination membrane with bounded two-scale
  displacement, cellular fissures, localized plasma, a camera-facing
  dimensional deep-violet/cyan portal, asymmetric luminous rim and off-axis
  energy shard, sparse mantle/lattice, selected raised facets, unequal
  internal energy filaments, and one opaque asymmetric revelation horizon
  that opens behind the destination at arrival;
- six primary and nine companion orbital objects resolved into three distinct
  families: irregular structural sweeps, long dim elliptical rails, and sparse
  hairline traces, plus seven combined travelling light packets and one bounded
  foreground bracket;
- four deterministic parallax layers of point-like stars, combined orbital
  dust, restrained sliver debris, seven optical glints, and split near/far
  tapered travel accents;
- four offset, two-scale procedural nebula/fog layers with a restrained
  diagonal violet veil rather than a fluid simulation or dense volumetric
  domain;
- an intentional core/rim/fill/underside lighting system plus bounded
  blue/cyan/violet Fresnel, coat, specular, and emission response; and
- a stable target-tracking camera with a directed approach, glide, suspension,
  arrival, and drift-away arc, eased target/lens offsets, intentional
  asymmetry, and no beat-driven shake. The optical face tracks both camera
  azimuth and elevation, independently of the hero's subtle audio rotation.

Macro direction preserves every normalized cue-sheet section and transition,
then merges deterministic cinematic anchors for opening, early development,
groove, breakdown, rebuild, arrival, late crest, and outro. This gives long cue
sections an internal emotional arc without changing analysis or preview
selection. The resulting AUTO_CLAMPED keys control destination awakening,
camera distance/composition, off-axis orbital choreography and convergence,
near/far parallax, foreground travel depth through rising transitions, lighting
distance, atmospheric placement, and outro release.
The representative five-second rise also receives one deterministic midpoint
threshold key: camera, reveal, and orbit motion briefly hold, then accelerate
into arrival while the existing foreground bracket crosses laterally and
advances in depth. Existing near travel accents are sized to remain legible in
the 640-pixel preview, and the release brings the existing halo and light rig
closer gradually rather than firing a full-frame flash.
Micro response stays on the existing audio bus:

| Audio control | Main Space Journey use |
| --- | --- |
| `master_energy` | Restrained global intensity and travel activity. |
| `bass_energy` | Core breathing, inner-ring pressure, and low light. |
| `drum_energy` | Local ring and travel-lane accents. |
| `vocal_energy` | Spectral ribbons and slow atmospheric illumination. |
| `other_energy` | Secondary orbit and environmental drift. |
| `low_band` | Large-scale orbital/fog movement. |
| `mid_band` | Secondary geometry and tightly bounded lateral motion. |
| `high_band` | Star scintillation and fine highlights. |
| `brightness` | Color temperature, atmospheric visibility, and highlight balance. |
| `transient_activity` | Brief local glints and streak activation. |

All audio values are already normalized and smoothed by TrackPrompt. Blender
adds bounded multipliers and clamps; it does not import waveform samples or
create full-frame flashes for individual onsets.

## Scene and manifest contract

Both presets retain the common collections:

```text
TP_WORLD
TP_CAMERAS
TP_LIGHTS
TP_PRIMARY_GEOMETRY
TP_RINGS
TP_SHARDS
TP_VOCAL_ELEMENTS
TP_BACKGROUND
TP_DEBUG
```

Space Journey adds named destination, starfield, nebula, travel-path, and space-
environment collections where useful. The active camera has a deterministic
target. Scene diagnostics and the sibling manifest retain the common frame,
FPS, collection, audio-bus, F-curve, audio-strip, camera, and output checks and
add:

- the selected preset;
- the full resolved configuration and defaulted fields;
- the deterministic seed;
- Blender and cue-sheet versions;
- preset-specific collection checks and scene counts;
- camera target;
- role-labelled representative still frames; and
- bounded warnings or fallback decisions.

Space Journey uses non-colliding output names:

```text
trackprompt-space-journey.blend
trackprompt-space-journey.manifest.json
visualizer-config.resolved.json
preview\space-journey-preview.mp4
preview\preview-manifest.json
```

Abstract Geometry retains its historical filenames. Every canonical run still
uses an isolated `test-output\system-runs\<timestamp>\` directory.

## Bounded review workflow

The preview planner chooses six role-labelled, well-distributed stills covering
opening, development, groove, lower-energy/breakdown behavior, peak, and outro.
After still review, it chooses one musically representative interior segment of
at most ten seconds. The preview CLI renders H.264 video and requests AAC audio
when the scene has `TP_AUDIO`, then uses local ffprobe to verify duration and
actual streams.

No command in the normal UI, runner, MCP entrypoint, or test suite starts a
full-track render. Full-length final-quality rendering is an explicit later
operator decision after artistic approval.

## Bounded MCP revisions

MCP uses the same typed configuration boundary. A revision supplies a preset
configuration or a small validated parameter patch, rebuilds deterministically
to a new approved `.blend`, renders representative stills, and writes a revision
manifest. Appropriate revisions include reducing ring occlusion, changing the
palette, widening the camera distance, or changing one audio response.

MCP does not accept arbitrary public Python, manipulate individual audio
keyframes, overwrite an existing `.blend`, or start a full-track render. Limit
visual revision to two still-based passes before handing the result to an artist.

## Privacy and performance

Configuration contains only preset identifiers, bounded visual values, a seed,
and safe warnings. It contains no source filename/path, lyrics, transcript,
waveform samples, chord sequence, prompt output, model paths, or credentials.
The visualizer remains local and performs no network request.

The preset uses shared materials, combined procedural geometry, multi-spline
curve objects, bounded debris counts, four lights, reusable audio-bus drivers,
and section-scale keyframes. The default deterministic scene remains compact
despite the extra apparent detail: 75 objects, 25 materials, 14 collections,
and 154 F-curves in the verified Blender 5.2 cinematic build. The threshold
motion pass reuses the single foreground bracket and adds only its three
location F-curves, leaving the object, material, collection, transparency, and
audio-bus counts unchanged. It avoids per-particle objects, simulations, dense per-frame
Python handlers, and duplicated 20 Hz curves. Preview settings are
intentionally lower than an operator-selected final render.

Blender 5.2 uses a compositor node group instead of the older scene node-tree
API. Space Journey supports both forms and applies bounded Fog Glow after the
material pass. If a Blender build exposes neither supported API, it reports
`controlled_compositor_glow_unavailable`, keeps the material/lighting fallback,
and preserves that warning in scene and preview manifests.
