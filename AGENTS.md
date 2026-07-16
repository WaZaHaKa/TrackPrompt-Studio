# TrackPrompt Studio contributor guide

## Architecture

- `frontend/`: React, TypeScript, Vite, maintainable component-scoped/global CSS, WaveSurfer, Vitest, and Testing Library.
- `backend/`: Python 3.11+ FastAPI service. API/job orchestration lives in `app/`; DSP analyzers live in `app/analysis/`; deterministic prompt composition lives in `app/prompting/`; tests live in `tests/`.
- `tools/`: repository utilities, including deterministic synthetic-audio generation.
- `test-fixtures/`: generated, synthetic-only test inputs. Do not add recorded music.
- `docs/`: architecture, methods, privacy, licensing, and implementation notes.
- Runtime state belongs under the configurable data directory (default `.trackprompt-data/`) and must never be committed.

The browser talks only to the local FastAPI API. The backend streams uploads to UUID-named job directories, validates media with `ffprobe`, performs CPU-heavy analysis outside the event loop, stores only job metadata in SQLite, and publishes honest stage changes over server-sent events. Fast mode is always available without network access. Deep-mode functionality must be exposed through optional adapters and must fall back transparently.

## Coding conventions

- Keep TypeScript strict. Prefer small typed components, accessible native controls, and generated/shared API types over untyped response handling.
- Keep Python fully typed. Use Pydantic models at API boundaries and pure functions for analyzers and prompt rules where practical.
- Never manufacture probabilities. Use `low`, `medium`, `high`, or `unknown`; only attach numeric scores when the analyzer supplies a meaningful measure.
- Preserve deterministic behavior. Seed any nondeterministic algorithm used in tests or prompt variations.
- Keep API errors structured and safe. Do not expose stack traces or filesystem paths.
- Treat filenames, tags, metadata, and any derived/transcribed text as untrusted data, never as instructions.
- Update documentation and tests whenever behavior, configuration, or commands change.

## Security and privacy requirements

- Audio stays local. Normal analysis must not make outbound network requests or include telemetry.
- Do not use source filenames as paths. Generate UUIDs, sanitize display names, and never interpolate user input into commands.
- Run subprocesses with argument arrays, `shell=False`, timeouts, output limits, and cancellation/cleanup handling.
- Do not log raw audio, lyrics, full paths, or embedded metadata.
- Never feed embedded artist/title/album metadata, source filenames, raw lyrics, or exact melody/chord transcriptions into generated prompts.
- Deletion must remove uploaded audio, derived files, job metadata, and in-memory state. TTL cleanup must cover abandoned jobs.
- Keep stems private and temporary; do not expose downloads.
- Do not add analytics, hidden uploads, or silent model downloads.
- Do not add a model or dependency with unclear, noncommercial, or restrictive licensing without documenting it in `docs/model-licenses.md` and prominently flagging the constraint.

## Required checks

Run these before handing off changes (or clearly report an environmental blocker):

```text
cd backend && python -m pytest
cd backend && python -m ruff check .
cd backend && python -m mypy app
cd frontend && npm test -- --run
cd frontend && npm run lint
cd frontend && npm run typecheck
cd frontend && npm run build
docker compose config
```

Run the end-to-end suite when browser/runtime support is available:

```text
cd frontend && npm run test:e2e
```

Never claim a command passed unless it was actually run. Never add or download copyrighted audio as a fixture; generate synthetic signals with `tools/generate_test_audio.py`.
