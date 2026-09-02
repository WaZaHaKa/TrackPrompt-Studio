# Render calibration

Calibration measures the exact approved Blender scene through the production
profile-application path. It never authorizes or starts a full render.

## Safety gate

Before a candidate render, Mission Control inspects Blender and renderer
processes, the production mutex, managed final-output `.inflight-*`
checkpoints, and their scene/profile identities. Planning remains available
when rendering is blocked. Never remove an in-flight checkpoint or mutex to
make calibration proceed.

The required representative frames are `1`, `2085`, `3432`, `6972`, `8106`,
and `13029`. The required sustained ranges are `7065-7094` and `8091-8120`.
Isolated stills screen candidates; sustained ranges supply the production ETA
and storage evidence.

Candidate reports record cold start, warm mean/median/p90/worst timing,
process and image-write overhead, frame sizes, hashes, and exact scene/profile
identity. The calibration record also binds the machine fingerprint, Blender
build, GPU/driver, output drive, filesystem, and free space. Stage timings and
GPU/CPU/RAM/disk telemetry are recorded only where the local tools expose a
meaningful measurement; a null compositor or dependency-graph timing is not a
zero-duration result. Mission Control does not continuously sample utilization,
temperature, clocks, power, RAM, or disk behavior during every candidate.

Human review must mark a candidate `PASS`, `PASS WITH DOCUMENTED CAVEAT`, or
`FAIL` after inspecting both stills and consecutive ranges. Generated timing
evidence does not substitute for that review.

## Exact operator workflow

From the repository root, run:

```powershell
.\WZHK-Media-Launcher.cmd
```

Then select the exact menu path:

```text
CALIBRATE THIS PC
-> CALIBRATE THIS PC (create plan)
-> RUN CALIBRATION CANDIDATE
-> REVIEW CALIBRATION CANDIDATE
-> repeat for finalists
-> GENERATE RECOMMENDED PROFILE
-> REVIEW
-> SAVE
-> AUTHORIZE
-> LOCAL RENDER
-> FINAL RENDER PREFLIGHT
-> DRY-RUN / RESUME PLAN
-> START / RESUME RENDER
```

Calibration evidence stays under `test-output/render-calibration/` and is
generated local state, not source material. Editing a calibrated profile
changes its saved-file hash and invalidates old authorization and resume
identity.
