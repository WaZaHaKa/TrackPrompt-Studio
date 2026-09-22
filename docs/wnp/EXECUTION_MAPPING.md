# One execution authority, separate WNP observation

This integration is limited to Hayale V2.1 and run `344ab3d2444c`. Original source media, V2, existing requests, seeds, 832×480/129-frame/40-step raw settings, soundtrack and 1440-frame final edit are preserved.

| Concern | Existing authority and integration |
| --- | --- |
| Exclusive controller | The original `.trackprompt-data/hayale-v21/production.lock` byte remains the native OS lock. `durable.ControllerLease` uses the kit's native FileLock against that same file, so old and new controllers exclude each other. No timestamp can steal it. |
| Process identity | PID + native process start time + run binding. Provider additionally requires the actual listener lineage and exact run input/output directories. Legacy provider identity is separately audited and bound to the original server-record SHA. |
| Original run/receipt | `run.json` and original `submission.json` remain preserved. `attempt.json` is the canonical current attempt record for the updated controller; an imported legacy acceptance references its original receipt hash. The compatibility receipt is not an independent retry authority. |
| Durable intent | JSON adapts the supplied `attempt_ledger.py` state/transaction rules: identity is immutable; `prepared` → `sending` is atomically committed before POST; accepted ID is committed before its compatibility view. No second execution SQLite database was added. |
| Bound request | Run, shot, attempt, complete workflow SHA, input SHA, seed, settings, expected dimensions/count, output prefix, provider PID/start/endpoint. Prepared-but-unsent restart reuses the saved input/workflow without another upload. |
| Acceptance ambiguity | `recovery.inspect_submission` correlates queue and bounded full history by client/workflow identity, including an accepted request whose ID was not saved locally. Multiple matches hold. Missing history or unavailable provider holds; it never authorizes a blind second POST. |
| Decision policy | `durable.decide_verified` calls the unchanged supplied recovery policy. Identity match is supplied only after local source/workflow verification. Observers have no execution ownership. |
| Raw checkpoint | Supplied full PNG checker plus matching input, approved prepared graph, seed/settings, accepted prompt/history, download receipts and existing manifest hashes. A count or marker alone is not accepted. Complete files without a marker are verified, not regenerated. |
| Download promotion | New retrieval attempts remain private and separate. Full decode/provenance precedes directory promotion. Existing raw/receipt conflicts hold. Receipts precede atomic raw promotion, so an interrupted marker write is recoverable by verification only. |
| Stalled output | Console messages are bounded observer work. A blocked terminal cannot stop polling/commits; a failed log sink continues draining the provider pipe. Display/telemetry failures never cause rendering to retry. |
| Heartbeat | `worker.json` comes from an independent heartbeat thread, with PID/start/run and source UTC, separate from last material progress. Timeouts use monotonic time. Heartbeats do not grant submission authority. |
| Finalization | Existing finishing and DaVinci exporters remain authoritative. Retry uses verified raw; final success requires bound preview/interchange verification, 1440 frames, 60 seconds, 1080p24, audio/scene/logo structure, and honest review status. |
| User-owned execution | `continue-hayale-v21.ps1` uses stable installed Python. The shared local broker/native-detach primitive verifies no inherited Job Object before loading the worker. The exact primitive is tested against disposable parent-job closure. It has no remote endpoint. |
| WNP | `WNPProjection` has a separate native adapter-owner lock and redacted outbox. Its SQLite is monitoring-only. Reader/cache/projection/API failures cannot alter attempts, provider queue, or rendering. Master remains byte-identical. |

## Actual continuation gate

The pinned entrypoint never creates a new run. Its check currently returns `HOLD_EXISTING_OWNER`: the original elevated controller owns the OS lock while blocked in selected-console stdout. That is an honest blocked gate, not a reason to generate another Shot 001/002.

The old loop's timeout is overdue. Releasing its console selection may allow its existing cleanup to stop its ComfyUI child. That controlled legacy handoff is a separate operator decision. Positive completed-provider history and all output bytes are preserved for recovery even after provider shutdown; absent history is never substituted for those facts.

Once the legacy owner has exited normally and the continuation command proves exclusive ownership, the updated controller can promote Shot 002's existing verified output and continue the genuinely unsubmitted scenes with the same run/settings. No automatic missing-job retry or quality fallback exists.
