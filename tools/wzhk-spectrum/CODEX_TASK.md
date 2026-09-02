# Codex task: Implement the WZHK Spectrum renderer in TrackPrompt Studio

## Objective

Add an optional, deterministic, Windows-only **WZHK Spectrum** rendering path to
TrackPrompt Studio. Use the locally vendored Rainmeter source at
`vendor/wzhk-spectrum-visualizer/` as the upstream visualizer engine, but rebrand
generated job workspaces for DJ WaZaHaKa and integrate them with TrackPrompt's
existing analysis, project, archival, and render workflows.

The first production target is:

- Artist: DJ WaZaHaKa
- Track: Scattered
- Tempo: 120 BPM
- Meter: 4/4
- Musical grid: 96 bars / 192.000 seconds
- Approved master: resolve exact duration with ffprobe (currently about 196.620 seconds)
- Post-grid tail: 192.000 seconds to master EOF; intentional, not a mismatch
- Intro: bars `[1, 33)` / `00:00-01:04`
- Main: bars `[33, 89)` / `01:04-02:56`
- Outro: bars `[89, 97)` / `02:56-03:12`
- Output target: 1920x1080, 60 fps

Use `config/scattered.wzhk-spectrum.json` as the canonical seed contract.

## Non-negotiable architecture

1. Treat `vendor/wzhk-spectrum-visualizer/` as an immutable third-party snapshot.
   Never implement product behavior by editing the vendor tree in place.
2. Materialize each job into a private workspace such as:
   `.trackprompt-data/wzhk-spectrum/jobs/<job-id>/skin/`.
3. Apply generated branding, logo, title, artist, colors, and section cues only to
   that job workspace.
4. Keep Rainmeter and Windows capture optional. TrackPrompt analysis, existing
   Blender/cinematic renderers, and the web UI must remain usable without Rainmeter.
5. Keep audio local. Do not add telemetry, uploads, silent downloads, or render-time
   network calls.
6. Never commit the user's logo, master WAV, captures, or rendered output.
7. Preserve the upstream MIT license and exact commit provenance.
8. Do not introduce a nested Git repository.
9. Do not overload the existing Blender-oriented
   `build-trackprompt-visualizer.ps1`; introduce explicit renderer identities.
10. Final video muxing must use the original master audio file, not audio re-encoded
    by screen-capture software.

## Suggested implementation shape

Backend:

```text
backend/app/visualizers/
  __init__.py
  registry.py
  schemas.py
  wzhk_spectrum/
    __init__.py
    adapter.py
    contracts.py
    workspace.py
    preflight.py
    rainmeter.py
    capture.py
    mux.py
```

Frontend:

```text
frontend/src/features/visualizers/
  VisualizerSelector.tsx
  WzhkSpectrumPanel.tsx
  wzhkSpectrumTypes.ts
```

Keep final names consistent with the repository's established conventions after
inspecting the current code.

## First milestone

Implement a safe, reviewable vertical slice:

1. Add typed backend contracts for renderer availability, asset selection,
   render settings, section timing, job state, warnings, and artifacts.
2. Register `wzhk-spectrum` as an optional renderer with an honest availability
   result: `READY`, `MISSING_RAINMETER`, `MISSING_ASSETS`, or `UNSUPPORTED_PLATFORM`.
3. Add a preflight that checks Windows, Rainmeter executable/skin paths, the vendor
   snapshot, license/provenance, logo, master audio, FFmpeg, writable runtime
   directories, and duration agreement.
4. Add deterministic workspace materialization that copies the vendor snapshot and
   applies WZHK branding without touching the vendor source.
5. Replace the Monstercat-specific cover asset and visible naming in the generated
   workspace with user-owned WZHK assets and neutral WZHK naming.
6. Persist the exact input contract, upstream commit, generated-file hashes,
   tool versions, warnings, and output paths with the job.
7. Add a frontend renderer choice and preflight panel. Do not expose a Render button
   when required gates fail.
8. Add unit tests for timing conversion, workspace immutability, path safety,
   missing dependencies, asset resolution, and deterministic generation.
9. Add documentation for a manual baseline capture before automating OBS or another
   capture provider.
10. Do not claim automated capture is complete until a real end-to-end render is
    produced and verified.

## Section behavior for Scattered

Use section transitions as deterministic cues rather than guessing from wall-clock
time:

- Intro at `0s`: restrained bars, low visual density, slow background movement.
- Main at `64s`: primary color/intensity transition and full visual energy.
- Outro at `176s`: remove layers gradually and return focus to the WZHK mark.
- Post-grid tail at `192s`: remain audio-reactive while the visual field settles.
- End at the ffprobe-resolved master EOF: deterministic final state.

The production fade starts at `max(192, master EOF - configured fade seconds)`.
Never truncate, stretch, or otherwise modify the approved master to force it onto
the musical grid.

The contract uses end-exclusive bar ranges so bars are never double-counted.

## Safety and path handling

- Resolve all paths beneath the configured TrackPrompt data directory.
- Reject traversal and symlink escapes.
- Treat filenames and metadata as untrusted display values, never as commands.
- Invoke subprocesses with argument arrays, no shell interpolation, timeouts,
  cancellation, bounded output, and cleanup.
- Never log raw audio, private absolute paths, embedded metadata, or lyrics.
- Use UUID job directories and atomic state writes.
- A failed render must remain inspectable and retryable without deleting analysis.

## Acceptance criteria

- Existing TrackPrompt workflows still pass without Rainmeter installed.
- The renderer registry reports truthful WZHK Spectrum availability.
- A Scattered render contract validates to 96 bars and 192 seconds.
- A job workspace can be generated twice with identical hashes for identical inputs.
- Vendor files remain unchanged after workspace generation and tests.
- Missing logo/audio/Rainmeter/FFmpeg states are actionable and non-destructive.
- The UI shows renderer state and blocks only the unavailable renderer.
- Tests cover all new behavior.
- Documentation explains manual and future automated capture.
- The required checks in the repository's `AGENTS.md` are run and reported honestly.

## Required repository checks

Follow the current `AGENTS.md`. At minimum, run the backend and frontend test,
lint, typecheck, build, and Docker configuration checks it requires. Never state
that a check passed unless it was actually executed.
