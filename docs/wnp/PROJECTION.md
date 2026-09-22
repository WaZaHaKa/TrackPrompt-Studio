# Local WNP observation projection

`backend/app/wnp/projection.py` wraps the bundled, unchanged WNP master
and `trackprompt/src/wnp_trackprompt.py` mapping. It does not discover render files,
decode frames, query a provider, launch work, or change execution state. Its caller
must supply a verified normalized observation and the stable source transition ID.

Initialize the master node separately, once. The local observer uses:

```python
from app.local_video.wnp_projection import WNPProjection

with WNPProjection(project_root) as projection:
    result = projection.publish(source_id, observation)
    # A safe result reports monitoring success/failure, not render success/failure.
```

`load_wnp_kit()` returns `(store_module, adapter_module)` from the two bundled source
directories. No installation or changes to the master are required. Constructor
and context entry can raise on missing initialization, unavailable storage, or a
competing observer. Keep that lifecycle separate from the execution controller.
`publish`, `drain`, and `observe` return `ProjectionResult(ok, pending, error,
sequence)`; `pending` is unknown (`None`) if the local DB cannot be read. Errors
contain fixed local codes and never exception messages or private paths.

The observer holds the native OS lock `.wnp/trackprompt-adapter.lock` for its
whole context. No timestamp or PID expiry can steal that ownership. The separate
master writer lock continues to serialize individual WNP file operations.

The small SQLite outbox `.wnp/trackprompt-outbox.sqlite3` stores only sanitized
durable observations and SHA-256 hashes of source IDs. It is an observation ledger,
not a second execution authority. A source ID must identify an immutable durable
transition; reusing it with different durable fields returns
`source_identity_conflict`. Commit the local outbox row before WNP delivery. A
wrapper supplies the stable source hash as the master's producer key, while the
unchanged adapter builds the protocol state. ACK only after successful publication.
If WNP or the ACK write fails, retain the row and retry oldest pending rows through
`drain()`. An ACK failure after a committed event does not add another event. A
source already acknowledged is never replayed over newer durable state. A genuine
state revisit with a new source ID remains a new transition.

Restart preserves the node stream. The outbox binds to that stream and rejects an
ACK cursor ahead of the available history. It never resets, prunes, or repairs a
damaged WNP journal. The source reader must reconcile current authoritative state
before supplying new observations after restart. A failed initial outbox write
cannot be made durable; the caller must offer the same source again. No failure
authorizes a render retry.

Transient worker/provider/metric/progress fields are excluded from the outbox and
producer key. `publish` refreshes them even when durable delivery remains pending,
when the observation file itself remains writable. `observe(observation)` can
also refresh them independently. Neither operation emits metric-only durable
events. `worker.heartbeat_at`, `worker.lease_expires_at`, `provider.observed_at`, and
per-task `updated_at` preserve source timestamps, including null/unknown values;
the master's `observations.sampled_at` is the separate observer sample clock.

The allowlist includes run/task/attempt identifiers, known phases and states,
manifest SHA-256 values, final verification boolean, bounded per-shot summaries,
native protocol worker/provider fields, numeric metrics, and unit-based progress.
Task summaries accept `task`, `phase`, `attempt`, `frames_verified`,
`expected_frames`, `width`, `height`, `raw_manifest_sha256`,
`final_manifest_sha256`, `final_verified`, and `updated_at`. Unknown fields and
arbitrary evidence/message objects are omitted; no prompt, media, environment,
command line, URL, or absolute path is published. Unsafe opaque provider job IDs
are omitted. This deliberately narrow schema is not a general secret detector;
the reader must only supply machine identifiers in identifier fields. Supplied
hashes establish byte identity, not provenance independently verified by this module.

`raw_verified` requires its bound manifest and cannot complete the run. A final
phase requires `final_verified=true` and a final manifest SHA-256. The adapter does
not infer those facts from a marker, filename, count, or an idle GPU.

Synthetic checks in `backend/tests/test_wnp_projection.py` cover restart, ordered
replay, stable stream/cursor, duplicate suppression, real state revisits, competing
owner exclusion, telemetry outages, outbox failure, ACK failure, post-event snapshot
write failure, same-stream rollback detection, field redaction, source freshness,
and raw-versus-final completion. They do not launch an API, touch a real run, or
claim live Companion connectivity. Transport/authentication remain the unchanged
master host's responsibility.

## Hayale V2.1 reader and independent observer

`video-projects/hayale-flowers-promo/v2.1/observation.py` maps the existing run:

| Source | Projection and acceptance condition |
| --- | --- |
| `run.json`, prepared `manifest.json` | Existing twelve-character run, unchanged preparation digest, quality profile |
| `shot-NNN/submission.json`, optional `attempt.json` | Task/attempt context; receipt presence never means provider completion |
| `durable.verify_checkpoint(..., write_proof=False)` | Full frame/provenance verification is required for each `raw_verified` task and its manifest SHA |
| Recorded server endpoint, PID + process start time, current socket owner, `/system_stats` run input/output directories | All must identify the existing local provider before status/history is accepted |
| Guarded `recovery.inspect_submission` GETs | `generating`, `submitted`, `frames_complete`, or ambiguous state; unavailable access never proves absence |
| `worker.json` | Run + PID + process-start binding before active/stale classification; original heartbeat time preserved |
| `delivery.json` and every referenced file | All hashes match, bound verification/ffprobe reports verify 1440 frames, 60 seconds, 1920x1080/24, expected audio and source/logo structure |

The reader keeps durable per-task timestamps separate from provider sampling and
worker heartbeats. `.wnp/hayale-reader-cache.json` contains only compact verified
facts, original verification timestamps, and hashes of tracked file identities.
Unchanged raw bytes and provenance can reuse that verification after restart.
Any change to tracked code, source/manifest, receipt, provider input, raw sequence,
or download records invalidates it. If source code changes while the observer is
running, it stops refreshing observations until restarted with the new code.
Failure to save verified checkpoint facts merely loses this optimization. The
same file also records a small producer transition chain: unchanged polls and
restarts reuse their source identity, while a real state revisit gets a new one.
A failed transition-identity write stops new publication until the same transition
can be saved; it never changes execution. The cache is never execution authority
and contains no media, prompts, history bodies, or private source paths. Provider
request failure preserves the last successful source observation time, or unknown
when no successful source observation has been seen.

Legacy server metadata without a start timestamp needs the separately audited
`run/recovery/provider-instance.json` with `pid`, `started_at`, `run`, and
`server_record_sha256` (SHA-256 of the original server metadata bytes). The reader
checks that source binding before any provider GET, then rechecks process identity
and metadata stability after verifying the run directories. The first validated
compact instance is retained across observer restarts; a changed instance needs
reconciliation. Transport unavailability becomes `unreachable/waiting_provider`;
identity, validation, or local-source failure becomes `unknown/reconciling`.
The read-only client permits `/queue`, `/system_stats`, `/history`, and individual
`/history/<id>` GETs, using the existing bounded ComfyUI transport. No POST or
provider-control path is permitted.

A technical final verification reports `final_verified`; workload remains waiting
while the existing final report's creative review is pending. The reader does not
invent an operator approval or a visual-quality score.

`tools/hayale-wnp.py` defaults to `--check-only`, which checks local imports and
existing node/source files without contacting the provider. `--once` performs one
read/publication. `--serve-forever` holds the single adapter lock, polls the reader,
and runs the unchanged authenticated master API on `127.0.0.1:9761` in a separate
thread. Reader failure does not stop API reads or affect execution. Exceptions are
reported only as fixed safe codes; the reader token is never printed or generated.

`tools/start-hayale-wnp.ps1` also defaults to checks. `-Once` is explicit one-shot
observation; `-Start` calls the CLI's explicit `--launch` mode using the installed
Python 3.12, existing backend/Pillow libraries, and private runtime log files. The
launcher uses the existing local Windows WMI process broker to start a hidden
guardian outside the caller's Job Object. That guardian applies
`CREATE_BREAKAWAY_FROM_JOB | CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS` to the
target. Before loading the target script, its bootstrap must prove
`IsProcessInJob=False` and record PID/start identity in a nonce-bound handshake.
There is no inherited-job fallback, automatic retry of an uncertain launch, remote
WMI, UAC, new privilege, service registration, scheduler task, or SSH/firewall
change. Private single-use launch tickets and stdout/stderr files are retained.
Launch receipt means only launched;
the operator must verify the API and current source freshness before claiming
live monitoring acceptance.

The reusable local helper is `tools/hayale-wnp.py::spawn_detached_python`:
`spawn_detached_python([absolute_script_path, *arguments], stdout, stderr, cwd=path)`
returns `.pid` and `.started_at`. Its arguments are generated by local project
launchers; this capability is never exposed by the read-only WNP API.

Native Windows acceptance passed on 2026-09-18 using synthetic fixtures. A
disposable parent assigned itself to a Job Object with `KILL_ON_JOB_CLOSE` and
`BREAKAWAY_OK`, called the exact helper, then exited and closed its job. The
bounded child wrote three heartbeats after that parent exited, reported no Job
Object membership, and exited itself; `WaitForSingleObject` verified termination.
A separate injected denial test proved one native breakaway attempt without
fallback. Neither test launched the actual observer or renderer. The focused
validation command is:

```text
backend/.venv/Scripts/python.exe -m pytest tests/test_hayale_wnp_observation.py tests/test_wnp_projection.py -v --basetemp=.pytest_hayale_wnp_detachment_final
```

Run it from `backend` with the executable path adjusted to
`.venv/Scripts/python.exe`. Native fixtures need access to the existing installed
Python 3.12 and local Windows process broker. The private validation transcript
and exit receipt are retained under
`backend/.pytest_wnp_native_validation_20260918_final/`.
