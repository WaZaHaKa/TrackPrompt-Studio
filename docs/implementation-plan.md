# Implementation plan

## Outcome

Deliver one local-first application in which a permitted audio upload becomes a transparent, editable analysis, an editable and playable section timeline, deterministic Suno-ready prompt variants, and JSON/Markdown exports. Fast mode is the production baseline; Deep mode advertises only installed adapters and falls back visibly.

## Architecture decisions

1. **Single local API:** React calls FastAPI under `/api`. Uploaded bytes, the decoded WAV, and versioned result/prompt JSON stay in a UUID job directory. SQLite stores job/lifecycle metadata, including a sanitized display name and request flags, never audio or analysis payloads.
2. **Honest asynchronous jobs:** A bounded upload is accepted only after a bounded `ffprobe` preflight. Accepted media receives a job with HTTP 202; a worker semaphore then bounds isolated, killable Python analysis subprocesses while the API remains responsive. State transitions are persisted and emitted as server-sent events. Cancel/delete/expiry operations are serialized per job and checked between stages.
3. **One reusable decode:** The visible **Validating** stage performs a second, fresh, cancellable `ffprobe` of the actual stream; `ffmpeg` then creates one 16,000 Hz float analysis WAV while preserving mono or stereo for mix analysis. Commands use argument arrays, no shell, bounded output, cancellation, and explicit subprocess/analysis timeouts.
4. **Defensible Fast DSP:** NumPy/SciPy/librosa/soundfile/pyloudnorm derive rhythm, chroma/key/chords, recurrence/energy-based sections, timbre, loudness, stereo, and signal quality. Heuristic descriptors retain their methods and warnings and do not masquerade as model probabilities.
5. **Optional Deep adapters:** capability adapters expose availability, licensing metadata, and disk impact. The baseline adapter performs enhanced coarse stem-aware analysis only when an explicitly installed separator is present; absent models produce a warning and no invented output.
6. **Deterministic prompts:** a typed, tested phrase library ranks salient medium/high-confidence facts, removes duplicates and contradictions, applies whole-phrase character budgets, records fact-to-phrase rationale, and supports reproducible seeded variation.
7. **Accessible UI:** upload permission is explicit; progress reflects backend stages; the result workspace exposes evidence, fact editing/disabling/restoring, guarded timeline label/bound editing, waveform section seeking, prompt controls, copy feedback, exports, cancellation, and deletion.

## Delivery sequence

1. Establish schemas, configuration, error format, capability reporting, and job states.
2. Implement streaming upload, bounded preflight plus visible-stage ffprobe validation, persistent lifecycle, SSE, cancellation, deletion, and TTL cleanup.
3. Build synthetic fixtures and the Fast analysis pipeline with partial-analyzer isolation.
4. Build and test the deterministic prompt composer and exports.
5. Implement upload, progress, overview, editable timeline/waveform, editable facts, prompt preferences, copying, and privacy controls.
6. Add optional-model adapters, Docker packaging, direct-development commands, and full documentation.
7. Run backend/frontend checks, production build, E2E where available, Compose validation, and a final privacy/security review; fix discovered failures.

## Scope guardrails

- No Suno integration, account, API, or browser automation.
- No raw-audio network transfer or mandatory external model/API.
- No singer identification, sensitive-trait inference, exact transcription, or named-artist imitation.
- Deep features are truthful and optional; completing Fast mode and the primary path takes priority.
