# Veo HTTP 400 and continuity integration report

Date: 2026-08-13

## Historical failure evidence

- Job: `65d50742-49f1-4ef7-8a2a-671f80c94288`
- Plan digest: `963ab490e8444925523b6bebf177b31059992b092daca9670e4225ebfc190f6e`
- Model/region: `veo-3.1-generate-001`, `us-central1`
- HTTP status retained by the old client: `400`
- Exact retained parameter keys included both `task` and `resolution: 4k`.
- The previous adapter read and discarded the provider body. No copy existed in Mission Control logs, SQLite, job artifacts, or a bounded Vertex AI audit-log query. The local historical receipt records `bodyFormat: unavailable` rather than inventing an error message.

Google's current official Veo REST request schema does not include `parameters.task`, and its current GA model contract lists 720p/1080p rather than 4K. These are independent request-contract defects. The corrected adapter omits `task`, fails GA 4K compilation locally, and digest-binds `requestContractVersion: vertex-veo-predict-long-running-v2`.

Because the request contract changed, the old authorization cannot be reused. Legacy plans fail closed with `video_plan_request_contract_changed`. A new 1080p plan generates a new digest-specific phrase.

## Durable diagnostics

Every provider HTTP failure now creates a unique JSON receipt under:

```text
.trackprompt-data/video-generation/provider-errors/
```

Receipts include safe request field names/hashes, phase/job/shot/attempt identity, URL without query data, HTTP status, selected safe response headers, provider status/code/message, and the exact JSON or text response body after credential redaction. The UI receives only bounded safe fields and the diagnostic ID. Provider operation failures and filters receive the same durable treatment.

## Continuity contract

- `continuity-profile.json` defines the Quantum Siren character identity, wardrobe, face/hair/age presentation, global visual anchors, and named shot groups.
- Stable seeds use `sha256-v1(masterSeed, projectId, orderedGroupIds, shotId, variationIndex)`.
- Same-setup retry preserves the exact seed, prompt, reference, and request.
- New-variation retry increments the variation, changes the seed/digest/phrase, archives prior plan/request/authorization artifacts, and submits nothing until reauthorized.
- Operator-selected JPEG/PNG references are byte-validated, size-bounded, hashed, and represented through Veo's supported first-frame `image` field. They are uploaded only after authorization.
- An accepted verified shot can provide a locally extracted final PNG to its declared next shot. The new hash changes the plan digest and requires fresh authorization.
- `referenceImages` is omitted because the current GA models do not support that character-reference mode.

## Verification

- Focused video-generation backend: 23 passed with real FFmpeg/ffprobe.
- Authoritative Windows verifier: 24 passed; 21 JSON files parsed; Fast/Quality 1080p plans compiled; GA 4K failed closed; focused React and TypeScript passed; no cloud generation request.
- Complete backend: 467 passed on Python 3.12.13.
- Complete frontend: 12 files, 79 tests passed; ESLint, TypeScript, and production build passed.
- Ruff: passed.
- mypy: 115 source files passed.
- Docker Compose config: parsed successfully (with a non-fatal local Docker config ACL warning).
- Read-only GCP doctor: all seven checks passed for CLI, active account, token, project, Vertex AI API, bucket, and `us-central1`; `networkContacted=true`, generation submission impossible in this check.
- Paid generation: not attempted.

## Deployment status

The existing Mission Control instance was confirmed idle for both video generation and rendering. Windows denied stopping its elevated backend PID, so a second scheduler was not started against the same SQLite state. Close the elevated instance, then run:

```powershell
.\tools\RUN-GCP-VIDEO-FASTLANE.ps1 -NoBrowser
```

The next operator action is to compile and review a fresh **Fast 1080p** plan, including the displayed continuity seed/reference and maximum spend, then enter its new digest-specific phrase. Only that later explicit start action may submit the smoke shot.
