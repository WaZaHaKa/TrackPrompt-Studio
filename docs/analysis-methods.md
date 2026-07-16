# Analysis methods and limitations

This document describes analysis version `0.2.0` and result schema `1.1.0`.
Exports preserve both versions and the runtime analyzer-version map.

## How to read a result

Every uncertain output is a `FeatureValue` with a value, qualitative confidence,
method, optional alternatives/warning, and `userEdited` flag. Numeric `score` is
included only when its scale is meaningful for that calculation. Scores such as
template correlation or autocorrelation strength are **not calibrated
probabilities**.

Confidence has this meaning:

- `high`: a direct measurement, or strong estimator evidence under the stated
  assumptions;
- `medium`: useful signal evidence with known ambiguity;
- `low`: a heuristic descriptor or weak estimator that needs review; and
- `unknown`: not measured, not applicable, or deliberately withheld.

The prompt composer uses medium/high facts by default. Low/unknown results remain
visible for transparency but are omitted unless a user explicitly supplies or
enables an override.

## Shared preprocessing

FFprobe inspects the actual stream and accepts a supported codec/container pair,
not an extension or browser MIME claim. The allowlists cover PCM WAV, FLAC,
MP3, AAC/ALAC in M4A/MP4-style containers, and Vorbis/Opus in OGG-style
containers. The installed FFmpeg build must be able to decode the stream.

FFmpeg removes metadata, video, subtitles, and data streams and creates a
16,000 Hz, 32-bit float PCM analysis WAV. Mono stays mono; multichannel input is
decoded to at most two channels so stereo evidence remains available. Most
spectral work uses the channel mean. SoundFile reads the result as `float32`;
non-finite decoded samples are replaced with zero before measurement.

Most frequency-domain features share a Hann-window SciPy STFT with an up-to-2048
sample window, 50% overlap, and no boundary padding. Downstream analyzers retain
one `float32` magnitude matrix; power values are derived from it as needed rather
than stored in a second full matrix. At 16,000 Hz, a 2048-sample window is 128 ms
with a 64 ms hop. Very short audio is zero-padded to at least 64 samples and uses
a smaller valid power-of-two window.

Because a maximum-duration stereo analysis can still use several hundred MiB,
`ANALYSIS_WORKERS` defaults to one. `MAX_PENDING_JOBS` defaults to two with the
default worker count and bounds the total admitted running plus waiting jobs;
additional upload attempts receive a capacity error before their file is stored.
Increasing either value requires a host-memory review.

Each DSP worker is a separate killable Python process and has a hard
`ANALYSIS_TIMEOUT_SECONDS` bound (600 seconds by default) covering core analysis,
optional Deep work, and result serialization. Upload/ffprobe validation, the
separate FFmpeg decode, and parent-side prompt composition are outside that
worker timer.

## File, waveform, and signal quality

### File and stream inspection

FFprobe provides duration, sample rate, channel count, codec, container, bit rate
when available, and size. Artist/title/album tags are not requested, displayed,
persisted, classified, or used in a prompt in this release. The display filename
is normalized to a bounded printable basename; the source path is a UUID-based
internal name.

Known failures include damaged headers, an audio codec inside an unsupported
container, missing duration/sample-rate fields, more than 32 channels, or a codec
not compiled into FFmpeg. These produce a structured rejection rather than a
partial musical result.

### Waveform peaks

The mono signal is divided into at most 1,200 equal buckets. Each point is the
maximum absolute sample in a bucket, normalized by the track's largest bucket.
This is a display envelope, not a loudness curve and not an export of raw audio.

### Silence and signal sufficiency

Signal activity uses 50 ms RMS frames with 50% overlap and takes the loudest
channel RMS rather than a mono average. This prevents anti-phase or one-sided
stereo activity from disappearing. The candidate threshold is 8 dB above the
10th-percentile frame level and is bounded to `-70` through `-50` dBFS. A later
loud section therefore cannot raise the threshold above -50 dBFS. Start activity
must either persist through a 200 ms hysteresis window or recur across at least
250 ms inside a 1.25 second sparse-activity window; a single isolated click does
not end edge silence. Leading and trailing silence use independent contiguous
edge searches and never include a quiet middle breakdown. The threshold actually
used is returned as `activityThresholdDbfs`.

The Fast analyzer considers signal sufficient only when sustained or repeated
edge activity exists, whole-track RMS exceeds -70 dBFS, more than 1% of frames
cross the bounded threshold, and sample standard deviation exceeds `1e-5`.
Otherwise it emits an insufficient-signal warning and musical claims are
unavailable.

Other direct/proxy measures are:

| Feature | Method | Confidence / caveat |
| --- | --- | --- |
| Clipping | Fraction of decoded samples at or above absolute `0.999`; flagged above `0.0005` of samples. | High for decoded sample clipping; lossy encoding can change peaks. |
| DC offset | Mean mono sample amplitude. | High measurement of the decoded signal. |
| Noise floor | 10th percentile of short-window RMS in dBFS. | Medium; this is a quiet-frame proxy, not isolated background noise. |
| Effective level | Whole-track RMS in dBFS. | High measurement, not perceptual loudness. |
| Phase correlation | Pearson correlation between decoded left/right channels. | High for stereo; unknown/not applicable for mono. |

## Rhythm

### Tempo and alternatives

Positive spectral flux produces an onset envelope. The analyzer median-filters
the envelope and autocorrelates it across lags representing 40–220 BPM. A small
display bias favors a conventional 75–165 BPM pulse while retaining credible
half-time and double-time alternatives from 35–260 BPM.

The score is normalized autocorrelation strength. It is not a probability.
Confidence combines that strength with short-RMS estimator agreement,
beat-grid alignment, consistency across track windows, duration, and whether an
octave normalization was needed without estimator agreement. High requires at
least eight seconds, autocorrelation strength `0.22`, grid alignment `0.62`,
window consistency `0.6`, and estimator agreement.

Half/double-time ambiguity is intrinsic: a kick on beats 1 and 3 can support both
60 and 120 BPM, while dense subdivisions can support 120 and 240 BPM. The UI and
export retain those alternatives; the primary number must not hide them. Rubato,
ambient pads, free percussion, clipped transients, or syncopated material can
produce an unstable or musically secondary pulse.

### Onsets, beats, regularity, and groove

Spectral-flux peaks are retained separately as `onsetTimestamps`; they are
transient evidence and are never labeled as beats. For a selected BPM, the
analyzer searches onset phases and emits a constant musical-pulse grid whose
interval is exactly `60 / BPM`. `beatGridAlignment` combines grid coverage and
robust onset timing fit on a 0-to-1 evidence scale (not a probability). Coarse
track-window coverage supplies temporal consistency and therefore tempo
stability. Beat timestamps are capped at 2,000 points; onsets at 4,000.

Percussiveness and `driving`/`laid-back`/`steady` plus
`busy`/`moderate`/`sparse` descriptors use documented tempo, density, and flux
thresholds. They are heuristic medium-confidence descriptions, not instrument or
genre classifications.

Fast mode does not run a trained downbeat/meter model. It exposes 4/4 only as a
low-confidence periodic-accent approximation, marks downbeats unknown, and does
not establish swing or syncopation without clearer subdivision evidence. Do not
read the 4/4 label as proof, especially for 3/4, compound, changing, or additive
meters. A lag-three accent cycle accompanied by roughly two onsets per selected
pulse is withheld as ambiguous between simple triple and compound 6/8.

## Harmony

### Key and mode

STFT bins from 55–5,000 Hz are mapped to nearest equal-tempered pitch classes and
weighted to reduce high-frequency dominance. Mean chroma is correlated with all
12 rotations of the Krumhansl-Schmuckler major and minor profiles.

The reported score is the best template correlation. The next three candidates
and their fits are retained.

This is a global polyphonic estimate. Modal music, key changes, drones, power
chords, atonality, detuning, strong percussion, short excerpts, and recordings
whose melody conflicts with accompaniment can flatten or mislead the profiles.
The score is not the probability that a key is correct. Confidence additionally
uses runner-up margin, tonal concentration, usable harmonic duration, and
consistency across four-second windows. A margin below `0.025` is always low or
ambiguous even when the winning absolute fit is respectable. High confidence
requires fit at least `0.72`, margin at least `0.08`, and temporal consistency at
least `0.72`; medium requires fit at least `0.52`, margin at least `0.045`, and
consistency at least `0.5`. Plausible alternatives and ambiguity wording remain
in the result. Ambiguous key/mode facts are not prompt-eligible without explicit
user edit or acceptance.

### Chords and harmonic rhythm

About one second of chroma at a time is compared by cosine fit with 24 major/minor
triad templates. A block becomes unknown below fit `0.5` or with too little chroma
energy; fits at or above `0.72` are medium, and weaker accepted fits are low.
A three-block majority rule removes isolated flips, adjacent equal labels are
merged, and segments shorter than 150 ms are discarded.

The complete chord feature remains low confidence even when individual segments
are accepted. It recognizes only root-position major/minor pitch-class templates;
sevenths, suspensions, inversions, slash chords, extensions, pedal tones,
non-tertian harmony, and rapid changes can be simplified or marked unknown.
Harmonic rhythm is a low-confidence merged-change-rate descriptor. Major/minor
balance and tonal stability are similarly derived heuristics.

Approximate chord segments belong in the report/export. The deterministic primary
prompt describes harmonic character and does not paste the complete sequence.

## Melody

Fast mode deliberately does not estimate predominant pitch contour, note range,
register, phrasing, repetition, ornamentation, call-and-response, or hook
prominence from a dense polyphonic mix. Those fields are `unknown` with an
explicit warning. No note-for-note transcription is produced or sent to the
prompt composer.

An eventual Deep melody adapter would need a documented model/license, confidence
gate, and failure behavior. Even then, source separation leakage, octave errors,
polyphony, vocal effects, and overlapping leads would limit reliability.

## Structure and arrangement

The Fast segmenter combines log frame energy, log spectral centroid, chroma, and
eight broad spectral summaries. Standardized trajectories are smoothed over 1.5
seconds and compared across a 350 ms context. Candidate peaks are separated by
at least 2.5 seconds, but weak candidates require an eight-second minimum section;
only strong novelty can override that policy down to three seconds. A high
before/after spectral recurrence match with little energy change suppresses
periodic loop events. Before novelty selection, a phase-robust stationarity
guard compares four longer spectral summaries and their level spread; this
prevents a constant sparse pulse from splitting merely because short windows
contain different integer numbers of impulses. Results remain limited to ten
sections and expose boundary confidence.

Sections receive neutral A/B/C labels. `intro` is used cautiously for a short
first section (no more than about 15 seconds or 20% of the track); `outro` is used
only for a quieter terminal section. Other semantic verse/chorus/drop labels and
repetition groups are not asserted. Section energy is normalized from RMS dBFS;
the energy arc compares the first, last, minimum, and maximum section levels.

Conservative duration-constrained chroma recurrence assigns neutral repetition
groups, but does not force semantic verse/chorus labels. Gradual crossfades, sustained
ambient changes, brief fills, mastering-level jumps, and tracks shorter than a
few seconds can defeat novelty segmentation. Boundaries and labels are evidence
for review, not ground truth.

The timeline lets a reviewer correct the displayed inferred label and numeric
start/end bounds. Labels must match a neutral arrangement vocabulary such as
intro, verse, pre-chorus, chorus, refrain, bridge, breakdown, build, drop,
interlude, outro, transition, or neutral section/part identifiers. Bounds must be
finite and non-negative; every section must end after it starts, remain ordered
and non-overlapping with adjacent sections, and stay within the track. The
corrected inferred label can be excluded from the arrangement blueprint; that
removes semantic-label influence while retaining the timing under the generic
label `section`. Label/start/end can be restored from the preserved detected
analysis. These PATCH operations invalidate the stored prompt snapshot like
other analysis changes.

## Timbre and texture

| Feature | Method |
| --- | --- |
| Spectral centroid | Median magnitude-weighted frequency. |
| Spectral bandwidth | Median magnitude-weighted spread around centroid. |
| Spectral rolloff | Median frequency below 85% of frame power. |
| Spectral flatness | Mean geometric/arithmetic power ratio. |
| Zero-crossing rate | Fraction of adjacent samples whose signs differ. |
| Log-band summary | Eight coefficients from a compact Fourier/DCT-like transform of 20 log-power bands. This is labelled MFCC-like; it is not a standards-compliant MFCC classifier input. |
| Transient sharpness | Upper-percentile spectral-flux rule. |

User-facing descriptors use fixed thresholds: median centroid above 3,500 Hz is
`bright/airy`, below 1,400 Hz is `warm/dark`, and otherwise `balanced`; flatness
above `0.3` adds `noisy or textured`; zero-crossing and bandwidth thresholds
produce `percussive/sustained` and `dense/focused` texture.

The low-level measurements are high confidence for the decoded signal. The words
are medium-confidence heuristics and depend on sample rate, arrangement, level,
and mastering. Bright cymbals can make a warm song look bright; distortion/noise
can mimic airiness or percussiveness. Harmonic/percussive balance is a low-
confidence proxy, not source separation.

## Production and mix

### Loudness and dynamics

- Integrated loudness uses pyloudnorm's ITU-R BS.1770 meter when the material is
  valid for the meter. This receives high measurement confidence.
- If pyloudnorm is unavailable or cannot meter the input, the analyzer reports an
  explicitly named `ungated RMS-derived loudness proxy`, roughly RMS dBFS minus
  0.7 dB, with low confidence. It is not LUFS.
- Loudness range is the 95th-to-20th percentile of 400 ms RMS windows. It is a
  medium-confidence proxy, not EBU LRA.
- Sample peak is measured directly from decoded normalized samples and cannot
  exceed 0 dBFS for a valid normalized decode. The decoded min/max range is
  exported. If either bound exceeds `1.0001`, peak, crest factor, and compression
  tendency are withheld with a normalization warning rather than clamped
  silently. True peak is not measured because no oversampled meter runs; it
  remains unknown.
- Crest factor is sample peak minus whole-track RMS. Below 7 dB maps to `strong`,
  below 13 dB to `moderate`, and otherwise `light` compression tendency.
- Macro dynamics use the 95th-to-10th percentile of windowed RMS.

Compression labels do not distinguish mastering limiting from naturally dense
arrangements or sustained instrumentation. Short/quiet tracks can be invalid for
gated loudness and will use the named proxy.

### Stereo and frequency balance

Stereo width is side RMS divided by mid RMS. Inter-channel Pearson correlation
below -0.1 triggers a phase-risk warning; below 0.2 asks for a mono check. Mono
inputs report width/compatibility as not applicable rather than pretending to be
narrow stereo.

Relative STFT power is preserved across fixed bands: 0–120, 120–500, 500–2,000,
2,000–6,000, and 6,000 Hz–Nyquist. Low-end, midrange, and brightness labels use
ratios across those bands. The raw ratios remain in JSON so users can distinguish
measurements from descriptor rules.

Spaciousness is only a low-confidence mid/side proxy and cannot distinguish
reverb from panning or stereo arrangement. Sidechain pumping is not asserted.
Transient emphasis comes from spectral flux. Mix density is the mean fraction of
spectral bins above 8% of each frame's peak, mapped to sparse below `0.055`, dense
above `0.18`, and moderate in between. This remains a low-confidence occupancy
proxy. None of these is a plugin-grade mix diagnosis.

## Instrumentation, vocals, style, and mood

Fast instrumentation stays coarse. Pronounced/moderate onset evidence can yield
`percussive elements`; warm/dark spectra can yield `low-frequency tonal material`.
It does not rename those categories as drums, bass guitar, or synth bass.

Vocal presence, register, delivery, phrasing, layering, processing, and placement
remain unknown without an enabled separator/tagger. The analyzer never identifies
a singer or infers ethnicity, nationality, health, age, gender identity, or other
sensitive traits.

No Fast genre or era model is installed, so broad genre, subgenre, and recording
date are not fabricated. Mood, energy, intensity, danceability tendency, and
organic/synthetic character are low/medium heuristic translations of tempo,
onset stability, and timbre. `production-era resemblance` remains unknown and
would never claim an actual recording date.

## Deterministic prompt composition

The base composer is a typed rule engine, not an external language model. It
receives the versioned analysis plus `PromptPreferences` and produces a primary,
compact, and detailed paragraph; separate exclusions; an arrangement blueprint;
phrase-to-fact rationale; facts used/omitted; and warnings.

### Evidence and safety gates

- Only medium/high analyzer facts are eligible by default. A persisted
  user-edited or explicitly accepted fact can pass the confidence gate; a
  disabled path cannot. Acceptance is a user review decision, not a change to
  the analyzer's value or confidence.
- Style/target, rhythm, mood/energy, instrumentation, harmony, vocals,
  arrangement, production, and intent are assembled in a stable musical order.
- Strings are bounded, normalized to printable whitespace, and screened for
  explicit imitation language such as `in the style of`, `sounds like`, or
  `copy`. User-entered targets, vocals, exclusions, and edited analysis facts also
  pass a conservative proper-name guard based on an offline musical-term
  vocabulary. The curated vocabulary includes the documented common
  instrumentation, vocal-performance, timbre, mix, genre, mood, and arrangement
  terms, but remains closed to unfamiliar words. Exact duplicate phrases and
  duplicate exclusive groups are removed.
- Known opposing descriptor pairs—bright/dark, sparse/dense, polished/raw, and
  major-key/minor-key—are resolved within and across phrase groups; the higher-
  priority fact wins and the losing fact receives an omission reason.
- The sanitized source display name and any private-tag values are treated as
  forbidden identity substrings during final phrase selection. Private tags are
  empty in the current analysis pipeline because they are not extracted.
- There is no composer input path for raw lyrics, exact melody, or the complete
  chord list. The composer uses only broad harmonic character.

The proper-name guard intentionally favors omission and can reject an uncommon
capitalized musical term. Conversely, no finite local vocabulary can recognize
every possible artist name. Users remain responsible for entering musical
attributes rather than artist identities; the application does not silently add
a network lookup.

### Budgets, variation, and traceability

Compact, balanced, and detailed variants use 450, 1,000, and 1,800 character
budgets. Compact and balanced additionally select at most five and nine phrases,
so they remain meaningfully distinct even when every phrase would fit the raw
character budget. A custom budget is 200–4,000 characters. Selection considers phrases in
descending priority, then renders accepted phrases in canonical musical order.
It adds only complete phrases and always reserves room for the originality
clause, so a prompt is never cut in the middle of a phrase. Exclusions are
deduplicated and kept separate; the UI can copy them alone or append them
explicitly.

The same analysis/preferences yield the same output. Creativity deterministically
selects wording and adds controlled/bolder-direction phrases at the low/high ends;
target duration adds a bounded duration phrase. A variation seed selects from
fixed synonym sets (for example, approximate-tempo and arrangement verbs), so
variation is reproducible rather than random. Phrase rationale lists exactly
which fact paths were used, and rejected or budgeted-out evidence is returned in
`factsOmitted` with a reason.

Edit, restore, disable/use, and accept/unaccept operations are persisted in the
editable analysis. Editing or restoring clears acceptance unless that API update
explicitly re-accepts the fact, while disabling a path always overrides either an
edit or acceptance for prompt selection. Each analysis PATCH invalidates the
previously stored prompt package; the next prompt must be generated from the new
analysis snapshot. Manual text in the browser's prompt editor is deliberately
not persisted by this API.

Section label/bound edits are plain guarded section fields rather than
`FeatureValue` objects, so they do not use acceptance or alter analyzer
confidence. Label and bounds support restore; the corrected label has its own
prompt-inclusion control. All of those PATCH operations follow the same prompt-
snapshot invalidation rule.

The compulsory final sentence asks for an original melody, arrangement, and any
lyrics instead of reproduction. Non-English output currently returns an English
prompt plus a warning because no external translator is called.

Contradiction handling is deliberately a finite token rule, not general semantic
reasoning. It will not discover every conceptual conflict across arbitrary user
phrasing; the UI keeps the result editable for that reason.

## Fast versus Deep

Fast is the complete offline CPU baseline described above. Deep capability is
queried before upload. The optional Demucs adapter is disabled by default and is
ready only when `ENABLE_DEMUCS=true`, its Python package is installed, and every
regular file in `MODEL_CACHE_DIR` other than the root manifest is listed for
`DEMUCS_MODEL_NAME` and matches its SHA-256 entry in `demucs-models.json`. An
extra unmanifested repository or configuration file also disables Deep. No
weights are bundled, and the application never starts a model download.

For a Deep request with sufficient signal and a ready adapter, the backend
selects `cpu` or `cuda` according to `DEMUCS_DEVICE`, the PyTorch build, and
runtime availability, then creates private vocals, drums, bass, and other
stems inside the UUID job directory. It compares each stem's RMS with the source
RMS. A ratio of at least `0.025` marks that broad category present; `0.45` marks
it prominent. A vocal ratio of at least `0.08` yields `present`; otherwise it is
`weak or absent`. These are medium-confidence coarse descriptors, not source-
separation probabilities or proof of a specific instrument. The resulting score
is a relative RMS measure.

Before deletion, each structural section is mapped to the same time bounds in
each stem. Section-relative RMS, coarse `inactive`/`present`/`prominent` state,
vocal activity, method, and confidence are retained. Activity requires both a
relative ratio and an absolute floor relative to whole-track RMS, preventing a
silent stem in a quiet source section from appearing active. Only those derived
measurements survive. Temporary stems are deleted immediately after descriptors
are derived, including after adapter failure. If any readiness condition is missing,
separation fails, or the input has insufficient signal, the job retains Fast
analysis, exposes a warning, and reports Fast as its effective mode. Stem and
enhanced vocal/instrument evidence is never invented. See
`docs/model-licenses.md` before enabling or replacing the adapter.

Capability and result diagnostics report PyTorch installation/version, CUDA
build support, CUDA runtime availability, GPU name, selected device, and the CPU
fallback reason separately. A CUDA execution failure retries once on CPU. The
standard Compose profile never requires or claims a GPU. Demucs uses one worker
and 7-second segments inside the already single-worker analysis boundary to
limit peak host/device memory.

## Failure isolation and reproducibility

Analyzers run through an isolation boundary. If one analyzer raises, the job may
complete with that group represented by unknown fallback fields and a safe
warning while independent groups remain usable. Non-finite decoded samples are
zeroed before calculation; serialized scores and measurements must remain finite.

Fast calculations and prompt composition are deterministic for the same decoded
signal, analysis version, and preferences. Prompt variation uses an explicit
seed. Algorithm thresholds are versioned behavior: changing one requires a test,
an `analysisVersion` update when results materially change, and an update here.

## Final consistency validation

Before serialization, a dedicated sanity pass verifies finite values, edge
silence totals, ordered/in-range section bounds, bounded beat timestamps, BPM to
beat-grid interval agreement, normalized sample-peak consistency, key-margin to
confidence agreement, and Deep-mode section evidence. An affected field is
omitted or downgraded rather than serialized misleadingly. Safe warnings name
the analyzer invariant; logs contain only analyzer/invariant identifiers.

The local command `python scripts/diagnose_analysis.py <audio-file>` exposes the
main measurements and omission reasons without printing source metadata or
retaining decoded audio/stems. Add `--json` for structured output and `--mode
deep` to request the configured local adapter.
