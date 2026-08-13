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
- [ ] Commit only source files for this task, push feature branch, and open PR when available.
- [x] Pause only at the one exact plan-level live spending phrase.

## Verified on 2026-08-13

- Windows verifier: 19 focused backend tests, 19 JSON documents, all three price-bound plans, the focused React authorization tests, and strict TypeScript passed.
- Complete backend suite: 462 tests passed with Python 3.12.13 after removing the incompatible PowerShell 7 module directory from the child-only Windows PowerShell 5.1 module path.
- Complete frontend suite: 12 files and 78 tests passed; lint, typecheck, and production build passed.
- Browser E2E: 2 Chromium flows passed. The deployed fast-lane screen rendered successfully, and its HAR contained only `127.0.0.1` requests.
- Local deployment: healthy and loopback-only at `http://127.0.0.1:8765/?section=video`.
- GCP capability check: stopped safely because `gcloud` was unavailable; `networkContacted=false` and `generationSubmitted=false`.
