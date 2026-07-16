# Full-feature implementation plan

## Verified starting point

TrackPrompt Studio already has a deterministic Fast DSP pipeline, a private asynchronous job lifecycle, strict media validation, a CUDA-capable Demucs adapter, editable findings and sections, deterministic prompt composition, exports, cancellation, deletion, TTL cleanup, Docker packaging, and frontend/backend tests. This pass extends those boundaries; it does not replace the working analysis core.

The implementation keeps the governing separation explicit:

- **Analysis remains stable:** DSP, Demucs, genre similarities, transcript segments, language evidence, and confidence/ambiguity are reproducible inputs.
- **Interpretation may be creative:** only the prompt-writer layer samples wording, emphasis, and permitted transformations, using an explicit seed and bounded structured evidence.

## Selected local runtimes and reviewed models

The full GPU profile uses three independently capability-gated adapters and never downloads a model during track analysis:

1. **Genre:** Transformers CLAP with `laion/clap-htsat-unfused`, pinned to a reviewed Hugging Face revision and stored under `MODEL_CACHE_DIR/genre`. The model card identifies Apache-2.0 licensing. Inference is hierarchical over a versioned, artist-free taxonomy and bounded windows.
2. **Lyrics:** `faster-whisper` plus CTranslate2 with `Systran/faster-whisper-small`, pinned and stored under `MODEL_CACHE_DIR/lyrics`. The model card and runtime are MIT licensed. CUDA uses float16 by default and remains unavailable rather than silently falling back unless CPU fallback is explicitly enabled.
3. **Prompt writer:** Ollama with `qwen2.5:7b-instruct-q4_K_M` (`845dbda0ea48`, 4.7 GB, 32K context) in a private named volume. Qwen2.5 7B and Ollama are Apache-2.0 and MIT respectively. The service is internal-only and is provisioned explicitly before it is reported ready.

Exact revisions, licenses, resource estimates, restrictions, and limitations are maintained in `docs/model-licenses.md` and surfaced by `/api/capabilities`.

## Backend vertical slice

1. Extend the versioned Pydantic schemas with prompt-engine, genre-use, and lyrics-influence enums; genre/window evidence; private transcript models; prompt evidence/candidates; generation parameters; and independently truthful capability objects.
2. Extend validated configuration for each adapter, local model paths, device selection, bounded timeouts, model identifiers, CPU-fallback policy, and one-heavy-GPU-task scheduling.
3. Add a versioned genre taxonomy and adapter protocol. Implement a deterministic fake adapter and a real offline CLAP adapter that loads only a complete checksum-manifested local directory, samples bounded silence-trimmed windows, performs broad/subgenre/descriptor passes, and reports similarity rather than probability.
4. Add a lyrics adapter protocol, fake adapter, and real offline faster-whisper adapter. Consume the private Demucs vocal stem before cleanup, validate/filter timestamps and repeated or low-quality hallucinations, persist the raw transcript separately, and retain only bounded non-verbatim summary evidence in the ordinary analysis.
5. Add a typed prompt-writer protocol with deterministic, fake, and Ollama implementations. Build a small allowlisted `PromptEvidence`; request JSON-schema output with mode-specific sampling; validate identity, artist, lyric, chord/note, disabled-fact, lock, length, originality, and diversity constraints; allow one repair; then replace failures with deterministic candidates.
6. Serialize GPU-heavy work through a shared application semaphore. Analysis jobs hold a slot across Demucs, lyrics, and genre work; local prompt generation uses the same slot and exposes queue status. Cleanup remains mandatory on cancellation, timeout, CUDA OOM, and deletion.
7. Add dedicated private lyrics read/edit/delete/export endpoints and genre review/edit endpoints. Standard exports continue to omit raw transcript data.
8. Add safe diagnostics modules for aggregate capabilities, GPU, genre, lyrics, and prompt-writer health without printing private job content.

## Frontend vertical slice

1. Extend strict runtime decoders and API types for independent adapter readiness, genre evidence, private transcripts, prompt modes, candidates, seeds, and warnings.
2. Add upload-time Genre and Lyrics options with capability reasons, explicit lyrics consent, optional abstract themes, and explicit fallback policy.
3. Add a Genre & style workspace for evidence inspection, accept/reject/edit/custom/lock/restore/disable actions, plus strict-top/blend/user-only/disabled prompt influence.
4. Add a private Lyrics workspace with privacy and singing-limit warnings, timestamp seeking, segment editing/uncertainty/deletion/restoration, transcript deletion/export, and opt-in abstract themes.
5. Upgrade the prompt workspace with Reliable/Creative/Experimental selection, one/three candidates, creativity, optional/new/reused seed, candidate comparison/selection/copying, facts-used and validation diagnostics, and manual-edit replacement confirmation.
6. Preserve accessible native controls, keyboard tab behavior, current fact/section editing, waveform playback, copy/export/delete, and stale-prompt invalidation.

## Packaging and provisioning

1. Add a full GPU backend image with pinned Python, FFmpeg, CUDA-capable PyTorch, Transformers CLAP dependencies, Demucs, faster-whisper, CTranslate2, CUDA 12, and cuDNN 9 compatibility.
2. Add `compose.full-gpu.yaml` with GPU access for backend and Ollama, internal-only prompt-writer networking, persistent model/data volumes, loopback-only application ports, and health checks.
3. Add `setup-full-gpu.ps1` with explicit per-model terms switches, repository/Docker/NVIDIA validation, explicit model pulls/downloads, SHA-256 manifests, image build, volume ownership, startup, and capability verification. No analysis code may initiate a pull.
4. Add `verify-full-gpu.ps1` and `diagnose-full-stack.ps1` for imports, actual tiny GPU inference, service health, model readiness, selected/effective devices, and safe failure reasons.

## Test and acceptance strategy

- Keep large models mocked in the standard suite; add behavior-named backend and frontend tests for taxonomy/ranking/ambiguity, transcript privacy/lifecycle/filtering, prompt validation/diversity/fallback/seeds, GPU scheduling, and all new controls.
- Extend synthetic-only fixture generation; never add recorded music.
- Add optional real-model integration tests and the required baseline, Deep GPU, genre, lyrics, and creative-prompt E2E paths.
- Run every repository-required pytest, Ruff, mypy, Vitest, ESLint, TypeScript, build, Compose, and Playwright command.
- Build/start the full profile, call health/capabilities, run actual tiny inference for Demucs, CLAP, faster-whisper, and Ollama, exercise a permitted synthetic Deep+genre+lyrics+Creative flow, verify raw-transcript exclusion and stem cleanup, then report exact evidence. Any environment or model limitation remains explicit and is never converted into a passing claim.

