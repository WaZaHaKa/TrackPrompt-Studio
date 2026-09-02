# WZHK Spectrum renderer

This directory contains the checked-in contracts, typed visual preset, raw WebGL2
runtime, GLSL shaders, and preflight tooling for TrackPrompt Studio's local WZHK
renderer. The vendor snapshot stays immutable at
`vendor/wzhk-spectrum-visualizer/`; private assets, workspaces, captures, and final
videos stay below ignored `.trackprompt-data/wzhk-spectrum/`.

Scattered has a 192.000-second, 96-bar musical grid and an ffprobe-resolved
196.619796-second master. The remaining 4.619796 seconds are an intentional
`POST_GRID_TAIL`, not a mismatch.

Milestone 3.7 refines the canonical `generative-geometry` composition. One job-owned, loopback-only
browser uses raw WebGL2 point sprites and trusted GLSL shape functions to morph
4,096 addressable nodes over a fixed 64×64 domain. The same browser compositor
draws only the WZHK logo, artist and title over the full-canvas geometry. Traditional
bars/ribbon, BPM/meter text, section labels and runtime diagnostics are absent from
production. Two soft local readability masks replace the large left exclusion
rectangle. Larger point cores and normalized forms give geometry the visual lead;
the master-driven density/brightness/deformation envelope preserves section and
tail evolution. The Rainmeter field remains available as `static-structured`
compatibility/fallback mode; new production foreground copies are identity-only.

Preview and production are separate. Shape, A→B morph, section, simulated-audio,
and shape-lab overrides are preview-only. Production requires canonical
choreography, measured WebGL2/shader/GPU/FPS preflight, and explicit operator
confirmation. FFmpeg `gfxcapture` records video-only 1920×1080/60 CFR Matroska;
the pipeline trims the measured lead and muxes the approved master. Nothing is
uploaded.

Runtime assets:

```text
tools/wzhk-spectrum/runtime/index.html
tools/wzhk-spectrum/runtime/runtime.js
tools/wzhk-spectrum/runtime/shaders/neopixel.vert.glsl
tools/wzhk-spectrum/runtime/shaders/neopixel.frag.glsl
```

Private assets:

```text
.trackprompt-data/wzhk-spectrum/assets/logo/
.trackprompt-data/wzhk-spectrum/assets/track/
```

Verification:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass `
  -File .\tools\wzhk-spectrum\tests\Invoke-WZHK-SpectrumPreflight.Smoke.ps1

pwsh -NoProfile -ExecutionPolicy Bypass `
  -File .\tools\wzhk-spectrum\scripts\Invoke-WZHK-SpectrumPreflight.ps1 `
  -ProjectRoot "C:\Users\theon\GitHub\TrackPrompt-Studio"

Set-Location .\backend
python -m pytest tests\test_wzhk_generative_geometry.py `
  tests\test_wzhk_geometry_browser_runtime.py
python -m pytest tests\test_wzhk_spectrum_composition.py `
  tests\test_wzhk_composition_review.py tests\test_wzhk_geometry_composition_runtime.py
```

See `docs/wzhk-spectrum-integration.md` for architecture, choreography,
performance evidence, fallback behavior, capture/recovery, and operator workflow.
