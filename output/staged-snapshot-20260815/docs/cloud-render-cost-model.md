# Cloud render cost model

GPU selection must be based on measured validated output, not VRAM, Tensor Core
count, marketing tier, hourly price alone, or remembered benchmarks.

```text
cost per validated frame = hourly price * measured seconds per frame / 3600
cost per 1000 frames     = hourly price * measured seconds per frame * 1000 / 3600
total GPU-hours          = measured seconds per frame * remaining frames / 3600
expected wall time       = total GPU-hours / effective workers + transfer and startup overhead
```

Tournament measurements should include boot and upload time, cold frame, warm
median, p90, worst frame, frames/hour, hourly price, cost/frame, cost/1000
frames, utilization, VRAM, frame size, upload throughput, Blender warnings,
stability, and visual validity. Software rendering, compositor differences,
unstable workers, or failed visual gates make an offer ineligible regardless of
price.

## Current status

There are no live Brev measurements or selected winning GPU. The tournament
code ranks measurements supplied by the operator; it does not discover offers,
run Blender, poll provider prices, or prove the data is current.

Run offline ranking from the repository root — **READ-ONLY**:

```powershell
$CloudPython = ".\backend\.venv\Scripts\python.exe"
$TournamentInput = Read-Host "Tournament benchmark JSON"
& $CloudPython -m cloud_render.cli tournament-rank `
  --input "$TournamentInput"
```

The input `benchmarks` array must contain actual measured offer, price, timing,
validated-frame, visual-gate, technical-gate, software-rendering, and stability
fields. Ranking fabricated or remembered results does not authorize a GPU.

## Fleet and budget boundary

A future production fleet review must show optimistic, expected, and
conservative time; compute, storage, and transfer cost; safety reserve;
current/projected spend; budget warning thresholds; and confidence. The
intended hard controls are maximum hourly price per worker, maximum worker
count, total budget, optional deadline, no unapproved scale-up, and stopping
completed/idle workers.

Those live telemetry claims are not implemented by the current one-shot job
status snapshot. Its scheduler source currently supplies job ID, cancellation,
chunk-state counts, published-frame count, unresolved conflicts, and completion.
Provider, GPU, worker counts, frames/hour, ETA, current/projected spend, budget,
storage, transfer, QA frame, and worker log display as `not supplied` unless a
separate real telemetry source is explicitly provided.

`FleetController` is a tested stop-decision primitive, not an active reconciler.
Manual teardown is required for a known live instance. The starting production
choice may eventually be 1, 2, 4, 8, or custom workers, but the current Mission
Control benchmark path is preparation-only and exactly one worker is the
maximum live benchmark target. Scaling above one remains forbidden until that
single-worker benchmark passes and its worker is confirmed stopped.

More workers can reduce wall time; they do not make total GPU-hours free.
