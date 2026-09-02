## What changed

- adds `static-into-signal` as the second data-driven GCP Video Fast Lane content package, with the exact 16-shot Signal Bearer bank, eight normalized chapters, continuity groups, deterministic master seed, Fast/Quality/optional-4K profiles, and project-owned editorial blueprint
- makes timeline resolution and Resolve export track-agnostic by loading package-owned treatment, coverage, sequencing, output names, and handoff metadata instead of branching on a song
- fixes the local master-audio selection contract so `selected` and `verified` are real booleans, persists verified retained/Browse bindings, and includes the bound master in local finishing identity without altering the provider plan
- updates Mission Control catalog/UI behavior so the project, Fast 1080p default, package seed, saved plan, costs, prompts, audio state, and one-time exact-batch authorization gate survive refresh/restart
- extends the Windows verifier and operator documentation for both content packages

## Why

The first Fast Lane implementation still embedded project-specific editorial assumptions and the native picker could overload selection state, preventing a second song from using the same safe workflow. This change moves all creative/editorial policy into versioned project data and keeps the existing Mission Control scheduler, store, SSE protocol, media validation, FFmpeg assembly, and authorization boundary.

## Operator impact

The operator can analyze the real 3:38 master, review the exact 16-shot Fast 1080p plan and $24 maximum, then authorize the whole immutable batch once. The authorized workflow will submit the same-plan smoke shot first and continue only after local verification. No paid generation was submitted by this change.

## Validation

- `tools/VERIFY-GCP-VIDEO-FASTLANE.ps1`: 40 passed; both projects compiled; unsupported 4K failed closed; no cloud generation
- staged-only backend Fast Lane scope: 39 passed
- staged-only Ruff and mypy: passed
- frontend unit suite: 80 passed; staged-only suite: 55 passed
- frontend lint, typecheck, and production build: passed
- browser E2E: 2 passed
- Mission Control browser suite: 3 passed, 1 unrelated existing copy-expectation failure
- Docker Compose config and Windows launcher validation: passed
- local browser QA: Fast 1080p default, seed `314159265`, exact $12.80 / $19.20 / $24.00 costs, 16-shot saved plan, verified 218.320-second audio master
- bounded GCP doctor: contacted GCP, submitted no generation, and correctly reported unavailable application credentials/project/API/bucket access

The broad dirty-worktree backend run reached 481/483 passing; the two remaining failures are unrelated Windows authorization/filesystem tests outside this diff. The task-owned staged tree is independently green.
