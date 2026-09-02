# Long-form set segmentation

`MAX_LONGFORM_DURATION_SECONDS=43200` permits source ingestion and segmentation through 12 hours. It does not authorize a 12-hour ordinary analysis, STFT, Demucs call, CLAP tensor, or transcription request. Ordinary and child analysis remain bounded by `MAX_SINGLE_TRACK_ANALYSIS_SECONDS` (1,200 seconds by default).

The coarse scan asks FFmpeg for sequential bounded PCM chunks at 8 kHz stereo. It retains low-rate observations, not a full-resolution waveform or source-wide STFT. Default cadence is one observation per second and the default decode chunk is 300 seconds. Each observation contains RMS, low/mid/high ratios, centroid, flatness, a chroma summary, onset density, and stereo width.

Candidate boundaries combine energy dips, sustained timbral distance, harmonic/chroma distance, onset change, stereo-field change, and post-boundary persistence. No boundary is based solely on silence, BPM, a transient, or a vocal entry. Local suppression removes duplicate neighbors and a deterministic global optimizer applies soft expected-length penalties; strong evidence may still retain short interludes or long tracks. Each globally selected coarse candidate is then decoded again in a bounded 32-second window at 0.25-second cadence. This refinement chooses a local anchor, transition extent, type, confidence, and evidence without retaining the local PCM after the candidate is processed.

At the defaults, the coarse decode buffer is at most 300 seconds × 8,000 samples/second × 2 channels × 4 bytes = 19.2 MB (18.31 MiB). A refinement buffer is at most 2.048 MB. The retained observation list grows at the low-rate cadence, not at audio sample rate.

Transition labels are:

```text
silence_gap
hard_cut
fade
crossfade
gradual_transition
uncertain
```

Crossfades contain both tracks. TrackPrompt records transition bands and stable cores but does not claim to source-separate overlapping masters. When no defensible boundary exists, one unresolved item is returned with an explicit warning.

Interactive scans use durable `segmentation_jobs` records. `POST /api/assets/{asset_id}/segmentation-jobs` queues a scan, `GET /api/segmentation-jobs/{job_id}` exposes its safe stage/progress/counts, and `DELETE` requests cancellation. A graceful shutdown cancels the bounded FFmpeg operation and leaves the scan queued; an unclean restart converts a persisted `running` scan back to `queued`. Recovery reruns the deterministic scan from the source rather than trusting a partial boundary map. Completed segments are committed atomically only after the entire scan succeeds.

Virtual segments can be replaced or edited through add, move, delete, merge, split, rename, accept, reject, and restore operations. Every map is an immutable revision with a SHA-256 and audit event. CUE, CSV, JSON, and explicitly timed M3U input can seed imported boundaries.

Accepted child tracks are decoded from the source only for their bounded stable-core range when it is sufficiently long. Temporary child media and stems are deleted after analysis; the archived source remains stored once. Results record the original source/segment offsets.
