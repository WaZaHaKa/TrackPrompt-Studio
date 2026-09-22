# GCP video recovery and admission

Mission Control's existing SQLite store owns prepared plans, shot attempts, cost reservations, accepted operation names and verification state. The legacy `submit-batch` command refuses new paid work; use `tools/hayale-16x4.py` / `run-hayale-16x4.ps1`. Existing legacy operation polling, downloads and export remain available for retained historical work.

Every new reservation binds the plan digest, job, shot, attempt, exact request digest, selected input hashes, authorization digest and immutable output prefix. The prefix contains the run, shot and attempt identities. The `idempotency_key` field is retained for compatibility and identifies the local request only; it is **not** a Google idempotency guarantee. Google submission is never automatically repeated. Immediately before POST, SQLite records `submissionIntentAt`; the returned qualified operation is retained privately in SQLite and a private operation receipt. Public job views omit it and WNP uses its allowlisted projection.

The short admission lock serializes reservation read/write across processes. No database transaction spans network work. All outstanding and uncertain attempts occupy the configured concurrency limit, including stopped jobs, and retain their conservative estimated cost reservations. Defaults for historical generic profiles remain two maximum attempts and one concurrent generation; the new Hayale plan explicitly binds one attempt and one concurrent generation. These are application admission limits, not billing settlement or a billing-alert enforcement claim. No quota, IAM, API, bucket or billing configuration is changed by this controller.

| Observed state | Resume behavior |
| --- | --- |
| Reserved, no durable operation identity | Stop for reconciliation; never infer non-submission from missing history. |
| Accepted operation, authentication/read failure | Resume that exact operation; expired authorization for new spending does not prevent this recovery. |
| Completed operation, transfer failed | Download the retained output without polling or generating again. |
| Downloaded candidate, verifier/tool failure | Retry local verification, preserving the candidate. |
| Corrupt decoded frames | Retain candidate and rejection receipt; a later resume downloads the cloud output again without generation. |
| File promoted immediately before a crash | Fully reverify the retained canonical file before repairing SQLite state. |
| Verified clip | Reuse it; local finishing/export failures never request generation. |

Polling uses at most five transient retries per continuous failure sequence, transfer at most two retries per invocation, with bounded jittered backoff and bounded Retry-After. Tokens are obtained through the shared isolated gcloud binding. Workers never open login browsers. After reauthentication, resume the same job. A changed identity/configuration digest or project requires explicit reconciliation. A persistent permission denial is not treated as an expired login.

Cloud downloads first inspect object metadata and copy the exact GCS generation (`gs://...#generation`). They check nonempty expected size and retain an object identity/local SHA-256 receipt. Downloads use unique temporary names. Clip verification checks dimensions, frame rate, duration, audio absence and a complete strict FFmpeg decode with the exact source frame count before atomic publication. Source duration is separate from the edited timeline duration. Partial/corrupt files and cloud outputs are not automatically deleted.

Cancellation stops local scheduling. It does not confirm Google cancellation, and an in-flight POST may still be accepted. Its uncertain reservation remains. A new submission remains blocked until reconciliation. Controller leases bind PID, process creation time when available, job, plan digest and a random token. `recover_stopped_worker(job_id, expected_token=...)` archives and releases only the exact proven stopped/reused-PID owner; an old timestamp is never grounds to steal ownership. Historical leases lacking creation time remain blocked while their PID is alive or uncertain.

On backend restart, an existing worker lease leaves that job untouched while the service continues starting other jobs. This applies to ordinary Mission Control video jobs as well as prepared Hayale jobs. Read the exact job's private `foreground-worker-lease.json` under its runtime directory, then recover a confirmed stopped owner with its retained token:

```powershell
.\run-hayale-16x4.ps1 -Action RecoverWorker -JobId '<saved-video-job-id>' -LeaseToken '<exact-retained-token>'
```

Only `RecoverWorker` accepts non-Hayale video jobs through this launcher. It archives and releases the stopped lease without changing job state, authorizing spending, or starting work. Resume an ordinary job through its existing Mission Control flow afterwards; resume a Hayale job through its dedicated launcher. Live or uncertain owners remain protected, and every Start or Authorize claims ownership before changing saved state or authorization receipts.

The supported operator launches use the repository's detached worker with explicit acceptance evidence. Closing Mission Control's browser does not cancel accepted work. A stopped backend can later recover accepted cloud operations from SQLite. Observation errors cannot veto authoritative state writes or rendering; failed SQLite intent persistence blocks new paid submission. Worker and provider-observation timestamps are separate and never refreshed by an observer poll.

Local composited shots use `continuationMode: local-composition`, zero estimated generation cost and no cloud image reference. `register_local_clip` imports only an explicitly selected, hash-bound clip, verifies it and creates a zero-cost VERIFIED attempt in the same ledger. No provider fallback is used.

Official capability and storage references checked 2026-09-22: [Veo 3.1 model specifications](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/models/veo/3-1-generate), [GCS object generation addressing](https://docs.cloud.google.com/storage/docs/using-versioned-objects). This adapter keeps its tested 720p/1080p path; model documentation now lists broader quality-model capabilities, which do not automatically expand existing authorized plans. Model access, paid generation and final creative acceptance require their own evidence; mock contract tests prove none of those live boundaries.
