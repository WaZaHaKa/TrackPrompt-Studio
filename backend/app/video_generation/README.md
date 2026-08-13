# Video generation module

This package implements TrackPrompt Studio's deterministic GCP Veo video plan, provider adapter, media verification, Resolve interchange, and local assembly contracts. Production orchestration belongs to `VideoGenerationController`, which is created by the existing `MissionControlService` and persists through its canonical SQLite store and SSE event sequence.

Boundary rules:

- importing this package has no provider or filesystem side effects;
- planning hashes completed analysis/StoryPlan/ShotPlan inputs and contacts no provider;
- only `start` and explicit retry can submit, after exact digest/cap authorization;
- `generateAudio` is false and the original audio remains local;
- durable operation names resume polling after restart;
- request cost is reserved before submission and never silently released;
- GCS outputs must stay below the exact authorized shot prefix;
- downloaded clips are attempt-scoped, never overwritten, and verified before timeline use;
- browser views omit credentials, operation names, physical paths, raw provider bodies, and audio identity;
- 1080p Fast is the default completion profile; 4K is optional.

Run focused verification from the repository root:

```powershell
.\tools\VERIFY-GCP-VIDEO-FASTLANE.ps1
```

See `docs/gcp-video-fastlane-runbook.md` for the operator workflow and `docs/gcp-video-fastlane-api-contract.md` for routes and state semantics.
