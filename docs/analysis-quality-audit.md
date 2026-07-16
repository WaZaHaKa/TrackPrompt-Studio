# Analysis quality audit

## Scope and traced path

This audit covers analysis/schema version `0.2.0` / `1.1.0`. The reviewed path
is upload streaming, bounded ffprobe validation, FFmpeg `pcm_f32le` decode,
SoundFile loading, shared spectral preprocessing, Fast analyzers, optional
Demucs separation, result consistency validation, serialization, frontend
decoding/display, deterministic prompt composition, export, and deletion.

## Defects found and implemented corrections

| Area | Defect found | Correction in 0.2.0 |
| --- | --- | --- |
| Edge activity | The 20th-percentile rule had no upper bound, so later mastered material could make a quiet but musical intro look silent. Mono averaging could also cancel anti-phase or one-sided activity. | Activity now uses the loudest-channel 50 ms RMS, an adaptive threshold bounded to -70 through -50 dBFS, start/hold hysteresis, repeated-sparse-activity handling, and contiguous edge searches. The actual threshold and decoded range are exported. |
| Sample peak | Positive normalized sample peaks could pass through without explaining an invalid decode range. | The decoded min/max range is validated. Valid normalized sample peak stays at or below 0 dBFS. An over-range float decode withholds peak/crest/compression fields, flags clipping/range warnings, and never masquerades as a valid positive dBFS measurement. True peak remains explicitly unmeasured. |
| Rhythm | `beatTimestamps` were spectral-flux peaks, so subdivisions and percussion transients were mislabeled as beats. | Onsets are now stored separately. Beats are a phase-fitted constant pulse grid at the selected BPM. Autocorrelation, transient estimator agreement, grid alignment, window consistency, and duration inform confidence. A serialization invariant rejects grids whose median interval contradicts BPM. |
| Tonality | A respectable absolute template fit could receive medium confidence despite a 0.001 winning margin. | Tonal confidence now gates on absolute fit, runner-up margin, tonal concentration, usable duration, and four-second-window consistency. Margins below 0.025 are low/ambiguous. Alternatives and a direct ambiguity warning are preserved; ambiguous key color is omitted from prompt facts. |
| Deep sections | Demucs evidence updated global vocals/instrumentation only; sections retained the stale “no enabled vocal separator” text. | Global and section-aligned relative RMS are derived before stem deletion. Every section receives coarse vocals/drums/bass/other activity, ratios, method, and confidence. Global candidates record active section IDs. Failed Deep runs preserve Fast fields without section fabrication. |
| Deep execution | Capability reporting conflated package/build/runtime/device state and always invoked CPU. | Capabilities and result diagnostics separately report PyTorch installation/version, CUDA build support, runtime availability, device name, selected device, and fallback reason. `auto`, `cpu`, and `cuda` selection are supported; a failed CUDA execution retries once on CPU. Standard Compose remains GPU-independent. |
| Segmentation | Two-feature, short-distance novelty could turn fills or sparse periodic material into 3–7 second sections and did not suppress recurring loop changes. | The segmenter now combines energy, centroid, chroma, and eight spectral summaries, applies a long-window stationarity guard, longer smoothing, a minimum-duration/strong-novelty policy, and a recurrence-similarity suppression rule. Neutral repetition grouping remains conservative. Boundary confidence is exposed. |
| Prompting | The no-tagger fallback said “genre-fluid,” and ambiguous tonal color could leak through related harmony facts. Prompt lengths could collapse to identical text. | The fallback names measured groove/timbre/arrangement and warns that genre tagging is unavailable. Low/ambiguous tonal facts are omitted unless explicitly accepted/edited. Deep section changes can shape arrangement wording. Compact and balanced variants cap phrase counts while all variants retain whole-phrase budgets and deterministic order. |
| Serialization | Cross-analyzer contradictions were not checked at one final boundary. | A sanity layer validates edge-silence totals, section bounds/order, beat finiteness/grid agreement, normalized peak, key-margin confidence, Deep-section consistency, and finite feature values. Unsafe fields are omitted or downgraded with metadata-safe invariant warnings. |
| Diagnostics | There was no one-command local evidence report. | `python scripts/diagnose_analysis.py <audio-file>` runs the real local path and reports signal range, activity threshold, rhythm/key candidates, sections, Deep readiness/evidence, invariant warnings, prompt, and omission reasons. `--json` emits structured output; temporary decoded/stem data is removed. |

## Regression coverage

Deterministic fixtures now include edge silence, quiet-to-loud material, fades,
mastered electronic content, sparse ambience, isolated clicks, one-sided stereo,
120/133/174 BPM rhythms, halftime, 3/4, 6/8, syncopation, dense subdivisions,
tempo change, rubato/noise, arrangement repetition, build/drop, and a continuous
loop. Backend tests also synthesize normalized peak references, an invalid
over-range float decode, Deep stems with section-specific activity, and
contradictory result objects for the sanity layer.

## Remaining limitations

- Beat tracking is a deterministic constant-grid estimator, not a trained
  downbeat model. Expressive tempo changes, compound meter, rubato, and extreme
  syncopation can remain unknown or choose a secondary pulse.
- The key estimator is still a global/windowed profile matcher. Modal, chromatic,
  drone-based, detuned, and genuinely modulating music is handled conservatively
  rather than classified semantically.
- Section semantics remain neutral unless simple intro/outro evidence is strong.
  Builds, drops, fills, and transitions are not forced from novelty alone.
- Demucs supplies four coarse categories only. The `other` stem never becomes a
  specific-instrument claim. No additional tagger is enabled because no model
  with fully reviewed code, weight, training-data, and commercial-use terms was
  selected for this pass.
- Sample peak is a decoded-sample measurement. True peak remains unavailable
  until an actual oversampled meter is implemented and validated.
