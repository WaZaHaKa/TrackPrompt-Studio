# Blender visual cue-sheet contract

`TrackPromptVisualCueSheet` is a public, minimized interchange format for local
visual animation. It is deliberately separate from `AnalysisResult` and is
currently version `1.1.0`.

Versioning follows semantic-version rules:

- patch: semantic corrections that do not change the JSON structure;
- minor: additive, backward-compatible fields or curves; and
- major: removed, renamed, or otherwise breaking fields.

The cue sheet contains source schema/version identifiers and a job UUID, a
deterministic timeline, the measured pulse grid, beat and onset events,
sections, neutral transitions, and normalized continuous curves. It never
contains the source filename or path, server paths, media tags, raw lyrics or
transcripts, full waveform data, chord sequences, prompts, model output, or
model-cache information. The user selects the original audio separately in
Blender.

## Timeline and events

Supported frame rates are 24, 25, 30, 50, and 60 FPS; 30 is the default.
Frames use this exact half-up convention, not Python's `round`:

```text
frameStart = 1
frameEnd = frameStart + ceil(durationSeconds * fps) - 1
eventFrame = clamp(frameStart + floor(timeSeconds * fps + 0.5), frameStart, frameEnd)
sectionEndFrame = max(sectionStartFrame, eventFrame(endSeconds) - 1)
```

The final section ends at `frameEnd`. At 30 FPS, 434.286 seconds ends at frame
13029 and an event at 228.8 seconds maps to frame 6865. Beats and onsets remain
separate. They are not labeled as downbeats and the exporter does not invent a
meter, bar structure, event strength, or frequency band.

Transitions are factual section-boundary records. `direction` is based only on
the adjacent section-energy delta: above `0.03` is `rising`, below `-0.03` is
`falling`, otherwise `stable`; missing energy is `unknown`. The compiler does
not infer drops, builds, breakdowns, or narrative events.

## Continuous curves

Analysis writes the private, versioned `visual-features.json` artifact at a
bounded 20 Hz cadence. Fast mode provides:

- `masterEnergy`: short-window RMS;
- `lowBandEnergy`: FFT energy from 20 to 150 Hz;
- `midBandEnergy`: FFT energy from 150 to 4000 Hz;
- `highBandEnergy`: FFT energy from 4000 Hz to Nyquist;
- `brightness`: spectral centroid; and
- `transientActivity`: positive spectral flux.

Successful Deep mode adds RMS curves for `drumEnergy`, `bassEnergy`,
`vocalEnergy`, and `otherEnergy` before temporary Demucs stems are deleted. No
audio samples or stem samples are stored in the artifact.

Each full-mix measure is normalized with its 5th and 95th percentiles, clamped
to finite values in `[0, 1]`. Stem curves share one percentile normalization
group so a nearly silent stem cannot be independently expanded to full visual
activity. Silent or effectively constant inputs resolve safely without division
by zero.

Smoothing is asymmetric exponential smoothing at 20 Hz:

| Curve family | Attack | Release |
| --- | ---: | ---: |
| Energy and frequency bands | 0.08 s | 0.35 s |
| Brightness | 0.15 s | 0.30 s |
| Transient activity | 0.025 s | 0.10 s |
| Vocal stem | 0.10 s | 0.45 s |

Values are visual controls, not probabilities or physical loudness units.

The compiler converts dense samples to frame/value pairs and applies
deterministic vertical-error Ramer-Douglas-Peucker simplification. It preserves
the first and last point, section ends and starts, transition frames, extrema,
and strong transient peaks. The export records source/exported point counts,
effective tolerance, point cap, and measured maximum linear-reconstruction
error.

| Detail | Initial tolerance | Per-curve point cap |
| --- | ---: | ---: |
| compact | 0.0200 | 600 |
| balanced | 0.0080 | 1600 |
| detailed | 0.0035 | 3500 |

If a cap requires a larger effective tolerance, that exact tolerance is
reported. Every curve retains at least two ordered points.

## API

```text
POST /api/analyses/{job_id}/visual-cues
GET  /api/analyses/{job_id}/visual-cues/export
```

The POST body is a typed preference object: `fps`, `includeBeats`,
`includeOnsets`, `includeStemEvidence`, `includeCurves`, and `curveDetail`.
The GET route accepts the same values as query parameters and downloads
`trackprompt-<job-id>-visual-cues.json`; it never derives the filename from the
source media. Its response is `application/json`, `Content-Disposition` is an
attachment with that UUID-derived filename, and the API-wide `Cache-Control:
no-store` policy applies to both visual-cue routes.

`GET /api/capabilities` and `GET /api/health` publish the same visualizer
contract without probing or opening a track:

```text
visualCueExportAvailable: true
visualCueSheetSchemaVersion: 1.1.0
visualFeatureArtifactSchemaVersion: 1.0.0
blenderVisualizerPreset: abstract-geometry
blenderVisualizerDefaultPreset: abstract-geometry
blenderVisualizerPresets: [abstract-geometry, space-journey]
blenderVisualizerConfigSchemaVersion: 1.0.0
```

The original `blenderVisualizerPreset` field is retained as a legacy default.
The additive fields enumerate the typed preset capability. They do not claim
that a particular job is complete or has a compatible private feature artifact;
the visual-cue routes still validate those conditions per job.

Preset selection and visual parameters use the separate
`POST /api/visualizer/config/resolve` contract documented in
[space-journey-visualizer.md](space-journey-visualizer.md). Configuration is not
embedded in `TrackPromptVisualCueSheet`, so changing a palette, camera distance,
or response multiplier cannot weaken or version-churn the minimized analysis
interchange format.

Old completed jobs may export timing and structure with `includeCurves=false`.
When curves are requested but `visual-features.json` does not exist, the API
returns `visual_features_unavailable` and asks for reanalysis. It never silently
reruns Demucs. Fast exports declare that Blender will use these fallbacks:

```text
drumEnergy  -> transientActivity
bassEnergy  -> lowBandEnergy
vocalEnergy -> constant zero
otherEnergy -> masterEnergy
```

`masterEnergy` has no fallback and is required by the Blender cue loader.

The repository's synthetic Blender smoke compiler writes this same public
contract and immediately reloads it through the Blender cue validator. Its
documented fixture, `arrangement_intro_a_b_a_outro.wav`, is generated by
`tools/generate_test_audio.py`; it is not committed audio and contains no
recorded music.

## Private artifact lifecycle

`visual-features.json` lives only inside the UUID job directory. It is schema
validated and size bounded, contains no path or source identity, and is retained
as a versioned private analysis artifact until explicit deletion. Cancellation
or failure before completion removes it along with partial decoded media and
other private intermediates.
