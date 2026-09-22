# TrackPrompt-Studio — repeated stale worker repair and WNP integration

**Windows repo:** C:\Users\theon\GitHub\TrackPrompt-Studio
**Kit:** tools\wnp-trackprompt-kit
**Incident run:** .trackprompt-data\hayale-v21\runs\344ab3d2444c
**ComfyUI root:** D:\TrackPrompt-ComfyUI (discover actual port/instance)
**Original media:** D:\HayaleFlowers\promo-2026-09-15 (never modify)

## Goal and scope

Stop the repeated expensive rerun loop. Find and fix the REAL local stale-worker
cause, preserve verified work, reconcile provider attempts without duplicate
submission, then publish honest state through the supplied WNP host. Do not make
WNP responsible for execution. Do not rebuild the product/image pipeline, store
size settings, promo creative, ACE-Step or Companion.

Approved production target remains seven Wan scenes plus one reused logo, final
60s at 1920x1080/24fps. Preserve current source identities, seeds, raw quality,
settings and soundtrack. Historical Shot001 request: 832x480,129frames,40steps.
Do not lower quality, change duration, switch providers or use paid GCP as a fix.
129 raw frames is NOT 60 seconds; final timeline must verify to 1440 frames.

## Evidence, not assumptions

The old user report shows 129 files in each Shot001 raw/provider output folder,
raw-verified.json exists, and newer Shot002 submission files. ComfyUI and another
Python process were present, with one idle GPU sample. None of this proves marker
contents, current liveness, prompt acceptance or a root cause. The user reports
recurrence. Preserve original report and current state separately with timestamps.

### T0 — preserve and diagnose without changing live work

- [ ] Read real local AGENTS/README, Git status/diff and current launcher/runner.
  Local uncommitted code is authoritative; do not checkout main/reset/stash it away.
- [ ] Prevent YOUR investigation from issuing NEW submissions; do not globally
  pause/kill a live worker or touch the provider queue merely to inspect it.
- [ ] Run the supplied private diagnostic collector if appropriate. It creates new
  reports only. Inspect full untruncated process command lines/PIDs/starttimes,
  parent process chain, receipts/timestamps, loopback listeners and two resource
  samples. Redact private captures before any cloud prompt or WNP export.
- [ ] Discover the actual ComfyUI endpoint from this run/owned process, not a
  guessed default port. Read queue/history/system stats via existing adapter;
  correlate run/shot/attempt/prompt ID/workflow digest/input digest/output prefix.
- [ ] Trace submission → accepted ID persistence → polling → output persistence →
  decode verification → next-shot handoff → worker heartbeat/stale calculation.
- [ ] Investigate host process lifetime, Codex tool timeout/sandbox parent shutdown,
  daemon child behavior, blocking stdout/stderr pipes, unawaited tasks, exceptions,
  mismatched clocks, stale locks, wrong provider instance and heartbeat starvation.
  Identify last PROVEN successful boundary and first missing one from evidence.

Write `docs/wnp/INCIDENT_ROOT_CAUSE.md` with real file/function references and an
observed causal chain. If evidence is insufficient, say so, add narrowly bounded
instrumentation, and continue safe offline work—do not repeat a seven-shot run.

### T1 — lock and durable execution intent, TDD

Reuse the current state/DB pattern wherever possible. Kit `attempt_ledger.py` is a
tested reference, not permission to create a competing second execution database.
Create a local code mapping showing integration sites and one authority.

Hold a native OS lock for the run's controller lifetime. A stale JSON timestamp
cannot steal it. Never identify a process by PID alone: bind PID+starttime+run.
Old and new controllers may not submit simultaneously. Do not run a production
controller under a disposable agent tool process that will kill it on tool timeout.
Separate long-running user-owned runner/service from ephemeral observer UI.

Persist attempt identity including run, shot, attempt, workflow hash, input hash,
seed/settings, expected frame count/size, output prefix and provider instance.
Persist `prepared`, then commit `sending` BEFORE the single external POST; only
then store the accepted prompt ID. A timeout/crash in this window is ambiguous.

Use source-correlated metadata/output prefix to rediscover accepted work. If the
provider lacks an idempotency guarantee, do not claim exactly-once. Persisted
sending/accepted with no known queue/history entry MUST hold, not retry. History
may have been cleared or the provider may have restarted. An operator may choose
a new attempt after explicit evidence review, but automatic retry cannot guess.
Definitive validation rejection is different from uncertain acceptance.

### T2 — reconcile before any action

Use `recovery_policy.py` as the tested decision reference. It never submits jobs.
Inputs MUST be proven by the local adapter; do not fill identity_match=true from
assumption or treat an unavailable provider as absent.

| Observed condition | Safe action |
|---|---|
| Exact verified raw checkpoint | Reuse; never rerun diffusion |
| Complete raw set, unverified | Decode/hash/provenance validation only |
| Queued/running matching provider job | Adopt and observe existing ID |
| Completed matching provider job | Recover/download/verify outputs |
| Definitively failed provider attempt | Record real error; separate retry policy |
| Sending/accepted, no provider/history evidence | Hold ambiguous; no resubmit |
| More than one correlated provider job | Hold duplicate; no automatic cancel |
| Partial/corrupt files | Preserve; scoped remediation, no overwrite |
| Provider inaccessible | Wait/reconnect; no inference of absence |
| Prepared intent never sent, exclusive owner | One begin_send + one POST allowed |
| No intent, owner, provider known | Prepare first; not direct submission |

A reused raw checkpoint needs more than filename count: exact continuous sequence,
full decode, dimensions, nonempty files, stable read, hashes and matching workflow,
source/seed/attempt provenance. `frame_check.py` verifies bytes/decoding, NOT that
provenance or aesthetic quality. Tie its report to actual submission records.

Do not rewrite old evidence to manufacture PASS. Preserve original receipts;
append a new recovery record with references. Download/finalize to temporary
attempt-specific destinations and atomically promote after validation. Existing
verified artifacts remain immutable. Repeated recovery must be a no-op.

### T3 — fix the proven stall and make progress observable

The WNP host does NOT fix the renderer by itself. Patch the root-cause boundary
identified in T0 and cover it with a failing test that passes after the fix.
Distinguish: lease owner alive, last heartbeat, last material progress, successful
provider observation, worker state, provider state, task stage and final status.

Examples of stages: prepared/submitted/generating/frames_complete/raw_verified/
finalizing/final_verified; waiting/reconciling/ambiguous as applicable. The message
must identify what it is actually waiting for. Do not continually print "rendering"
while waiting on a dead subprocess or an unknown queue.

Use monotonic durations for timeouts in a process and UTC for persisted display.
A long silent denoise/model-loading interval alone is not failed progress. Poll
provider with bounded requests/backoff and independent observer liveness; inspect
CPU/disk during VAE decode/finalization. Do not equate GPU0% with no work.
A heartbeat is not permission to resubmit; lease expiry initiates reconciliation.

### T4 — WNP adapter, not a new execution controller

Use bundled master unchanged. `src/wnp_trackprompt.py` accepts a NORMALIZED verified
application observation. Implement the local reader/mapping to it based on real
schema, documenting each source field. No guessed paths or marker-exists success.
Durable transitions publish via an idempotent outbox keyed to source identity;
current metrics/progress publish through independent observations.

Checkpoint stages and run completion remain distinct. Include per-shot summary
under `extensions.trackprompt.tasks`. A complete run requires final verified
artifacts + final manifest SHA; raw_verified cannot mark the run complete.

The WNP observer must run independently of an ephemeral foreground coding session;
it should be able to report an execution worker becoming stale. Keep a single
adapter owner. If monitoring is unavailable, preserve execution and the local
outbox rather than discarding output or crashing/rerunning the scene. Restart
outbox delivery is idempotent. Source timestamps must remain visible even when the
observer itself has just polled an old receipt.

Use read-only API over SSH. Do not expose ComfyUI or WNP on 0.0.0.0. Never publish
raw imagery, prompts, source audio, secrets or private absolute paths to WNP.
Only small redacted manifests and machine/run identifiers belong in the observer.

### T5 — required failure tests BEFORE another production submission

- [ ] Before submit crash: one prepared intent, one allowed submit.
- [ ] Provider accepts, local prompt ID not saved: rediscover/adopt OR hold ambiguous;
  NEVER a blind second submit.
- [ ] History cleared/provider restart: missing job is not proof never accepted.
- [ ] 129 complete raw frames before verification marker: verify existing files only.
- [ ] Shot001 verified → Shot002 submitted → controller dies: preserve Shot001,
  adopt/resolve Shot002. Exercise recovery twice and assert zero duplicate jobs.
- [ ] Second local controller cannot acquire a live owner's lock.
- [ ] Corrupt/partial/hash-mismatched frames are never promoted or overwritten.
- [ ] Observer offline, API offline, failed telemetry write: rendering isn't rerun.
- [ ] Clock jump/unchanged GPU sample/stale heartbeat do not become a render failure.
- [ ] UI/terminal/agent disconnect does not kill a separately supervised worker.
- [ ] Finalization failure retries only finalization; raw checkpoint survives.
- [ ] 60s1080p24 final verification checks 1440 final frames, audio sync/duration,
  logo/scene structure and playback; no invented visual-quality score.

Use a fake provider, synthetic tiny frame fixtures and crash/fault injection first.
No real seven-scene run as a test fixture. Capture exact command/exit-code evidence.
The supplied kit tests are not substitutes for regression tests in the real runner.

### T6 — controlled real-run recovery and completion

After the root-cause regression and integration gates pass, reconcile the EXISTING
run 344ab3d2444c under the exclusive controller. Preserve source/workflow hashes and
run ID. Reuse verifiedShot001. Inspect the actual Shot002 state. No duplicate prompt.
Do not kill/restart ComfyUI, cancel global queue, spawn replacement workers, or
perform paid/cloud generation absent a separate justified operator decision.

First report the exact proposed action, evidence and ownership. A previously
approved normal local continuation may proceed after proof, but ambiguous external
acceptance requires a deliberate operator decision—not a claimed safe retry.
After the Shot001→Shot002 boundary is verified, continue remaining work through the
existing bounded runner. Finalize/export only after complete raw verification.

Deliver a concise final report: true root cause; files/commits; baseline vs fixed
checks; currentShot001provenance/count; Shot002ID/state/action; duplication checks;
new WNPnode/stream; current real workload and monitoring freshness; finalartifact
status; remaining blockers; ONE exact tested continuation command (not invented).
