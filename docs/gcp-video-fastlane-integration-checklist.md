# Codex integration checklist

- [x] Read `AGENTS.md` and current TrackPrompt architecture/runbooks.
- [x] Preserve dirty unrelated work, active renders, mutexes, and `.inflight-*` state.
- [x] Keep `backend/app/video_generation/` side-effect-free at import time.
- [x] Reuse existing visual cues, StoryPlan, and ShotPlan; do not duplicate analysis.
- [x] Move provider attempt/task state into existing Mission Control lifecycle/store.
- [x] Add transactional/idempotent persistence changes only through current migration mechanism.
- [x] Add strict Pydantic API models and matching strict TypeScript decoders.
- [x] Add plan/cost/request-review/one-authorization workflow to existing Mission Control UI.
- [x] Add per-shot progress, filter/failure/retry, accepted/rejected review, and artifact previews.
- [x] Persist provider operation names and resume polling after process restart.
- [x] Enforce exact plan digest and maximum-spend cap before every submit/retry.
- [x] Keep original audio local and force `generateAudio=false`.
- [x] Verify every downloaded MP4 before timeline use.
- [x] Resolve full-song timeline at 24 FPS and snap to existing ShotPlan boundaries.
- [x] Emit FCPXML 1.11, FCP7 XML, EDL, edit sheet, marker CSV, and FFmpeg preview package.
- [x] Test with fake provider and generated synthetic media; no network in ordinary tests.
- [x] Run focused and complete backend/frontend/launcher/Compose validation.
- [x] Update architecture, privacy, Mission Control, and operator runbooks to match implementation.
- [x] Confirm `.gitignore` covers provider responses, authorization, GCS downloads, media, and timelines with local paths.
- [x] Commit only source files for this task, push feature branch, and open PR when available.
- [x] Pause only at the one exact plan-level live spending phrase.

## Verified on 2026-08-13

- Windows verifier: 19 focused backend tests, 19 JSON documents, all three price-bound plans, the focused React authorization tests, and strict TypeScript passed.
- Complete backend suite: 462 tests passed with Python 3.12.13 after removing the incompatible PowerShell 7 module directory from the child-only Windows PowerShell 5.1 module path.
- Complete frontend suite: 12 files and 78 tests passed; lint, typecheck, and production build passed.
- Browser E2E: 2 Chromium flows passed. The deployed fast-lane screen rendered successfully, and its HAR contained only `127.0.0.1` requests.
- Local deployment: healthy and loopback-only at `http://127.0.0.1:8765/?section=video`.
- GCP capability check: stopped safely because `gcloud` was unavailable; `networkContacted=false` and `generationSubmitted=false`.

## Veo 400 and continuity follow-up verified on 2026-08-13

- Historical evidence preserved truthfully: HTTP 400 and the rejected request keys were retained, but the old adapter had discarded the response body, so no exact provider message was reconstructed.
- Windows verifier: 24 focused backend tests, 21 JSON documents, both 1080p profiles, the expected GA 4K fail-closed check, focused React tests, and strict TypeScript passed without a generation request.
- Complete backend suite: 467 tests passed with Python 3.12.13; Ruff and mypy passed across 115 application source files.
- Complete frontend suite: 12 files and 79 tests passed; lint, typecheck, and production build passed.
- GCP capability doctor: all seven read-only checks passed, including the configured project, Vertex AI API, bucket, and region; `generationSubmitted=false`.
- Local deployment refresh: blocked because Windows denied stopping the existing elevated Mission Control PID. The instance was confirmed idle, preserved, and not duplicated. Browser E2E was therefore not rerun against the new backend.
- Paid generation: not attempted. A fresh digest-specific authorization is required for a new 1080p plan.
