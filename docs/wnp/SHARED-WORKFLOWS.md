# Shared TrackPrompt WNP projection

`app.wnp` owns the project mapping. `app.local_video.wnp_projection` remains a
compatibility import for the historical Hayale reader. The bundled master and
TrackPrompt adapter are unchanged, including their schemas, locks and transport.
There is one implementation of the durable observation outbox per configured
node. The native analysis, local-video and Mission Control stores remain the
execution authorities. A WNP failure never authorizes a retry or changes work.

The shared mapping adds `extensions.trackprompt.pipeline`. Standard WNP `state`,
`health`, `detail`, and `progress` remain useful without extension support.
`detail` identifies `trackprompt.preparation`, `analysis`, `planning`, `generation`,
`transfer`, `verification`, `finishing`, `music-mux`, `export`, or `recovery`.
No WNP enum or endpoint was added. The frozen adapter still performs legacy
phase/completion validation; the wrapper supplies the selected lifecycle detail.

## Lifecycle coverage and evidence

| Supported entry point | Actual commit hook | What is proven | Regression evidence |
| --- | --- | --- | --- |
| Upload/preparation and fast/deep audio analysis | `JobStore.create_job/update_job` after native commit and archive lifecycle update | Native phase/status only; completed analysis is idle, not a verified film | Real JobStore create/update with monitor lock contention in `test_shared_wnp.py` |
| Resumable local-video generation/post/edit/QC through local actions | `orchestrator._atomic_json`, exact resumable-run kind | Hash-bound completed source units; final QC separately binds final manifest | Actual synthetic 16-unit `ResumableLocalVideoRun` execution |
| Mission Control planning/authorization/local scene registration/GCP generation | `VideoGenerationController._save` | Ordered source clip attempts, accepted-operation opaque reference, 16 scene slots when present | Shared mapper and Mission Control controller regression tests |
| GCP transfer and source verification | Same `_save`, attempt `succeeded/downloaded/verified` | Transfer/verification stage; verified count requires clip hash and probe evidence | Native-record mapper tests with 16 tasks |
| Mission Control Blender render and inline encode/mux | `MissionControlStore.put_job` after transaction commit | Actual `JobPhase` including master/delivery encoding, audio mux and final verify; frame counters are transient and COMPLETE alone is unverified | Actual store writes and separate-connection postcommit test in `test_render_wnp.py` |
| Mission Control V2 media/render variants | `MissionControlStore.put_media_render_job` after commit, retaining the generic job's explicit phase when present | Final verification requires all enabled variants' validated frames, complete encode tasks, matching artifact identities, latest passing hash-bound QA and complete final-QA stage; evidence digest binds the exact persisted JSON bytes | Actual V2 store writes, absent/later failed QA tests and mux-phase preservation |
| Standalone Mission Control encode route | Typed `encode_job:` setting in `MissionControlStore.put_setting` after commit | Existing encoder progress/stage; its COMPLETE/path fields lack bound final QA, so delivery remains unverified | Actual typed setting writes with unrelated settings excluded |
| Hayale finishing/music mux/export | `hayale_finishing.finish_edit` before arrangement/mux/verification/export and after each verified scene; `hayale_operator.export` after final receipt | Technical completion only with explicit verified manifest; creative acceptance remains separate | Real finishing callback order test, manifest-required tests and scenario's synthetic media tests |
| Historical Hayale V2.1 observer | Existing read-only `HayaleObservationReader` through compatibility import | Existing provenance and 60-second delivery contract unchanged | `test_wnp_projection.py` and historical reader tests |

The unrelated experimental morph/passage implementation remains preserved local
work and is not adapted on the clean candidate branch. Its complete lifecycle
coverage is unavailable; the table does not claim full adaptation of those paths.
GCP/local source verification progress is
`source-clips`, separate from edited timeline `scenes`. The extension's
`provider_attempts` includes reserved/accepted attempts and is not a paid invoice.
`reserved_cost_usd` is the native admission reservation. Unknown GPU/VRAM, sampling,
fractional generation progress, ETA and transfer bytes are omitted. No local GPU
sample is described as a managed Veo GPU.

Blender/encode records do not supply a bound worker heartbeat or provider polling
clock; both remain unknown. Neither native `updated_at`, PID presence nor an
output-path string is substituted for those facts. Native logs, project/scene
names, paths, preview URLs, GPU samples and QA summaries remain private. V2 QA
hashes assert what the committed native verifier reported; WNP performs no media
decode or filesystem verification itself.

The accepted worker uses `observe_worker` after writing its native heartbeat. This
updates only the transient worker observation for the selected run, preserves the
provider's own observation timestamp and leaves tasks/progress/history unchanged.
Authentication required maps to `reauthenticate`; identity changes and uncertain
submission outcomes map to `reconcile`. Safe action/remedy codes are exported;
native error summaries, resource names and remediation command strings are not.

Technical verification is distinct from creative approval. A native COMPLETE
state, preview filename or directory does not by itself set `technical_verified`.
The final verifier must call `emit_lifecycle(technical_verified=True,
final_manifest_sha256=...)` after its media/hash checks. A separate creative review
remains `pending` until an actual caller provides an accepted review fact.

## Selection and startup

Monitoring is opt-in and local. Normal analysis makes no outbound requests even
when the hook is configured. First use `tools/trackprompt-wnp.py check` with the
repository's `backend/.venv/Scripts/python.exe`. It reports the existing identity,
cursor, selection, pending outbox count and source freshness without writing.

For an existing initialized node, explicitly select the job to monitor:

```powershell
& backend/.venv/Scripts/python.exe tools/trackprompt-wnp.py select --root . --run $jobId
$env:TRACKPROMPT_WNP_ROOT = (Get-Location).Path
$env:TRACKPROMPT_WNP_RUN = $jobId
```

Start/resume the normal controller from that environment. Selection is stored in
the existing outbox database. Both environment and durable selection must match;
unselected jobs cannot overwrite the node snapshot. An explicit later selection
retains the node, stream, history and cursor. Pending transitions must drain before
changing selection. A selection alone leaves the old snapshot visible until the
selected job supplies a native observation; `check` displays both IDs.

Do not run the historical long-lived Hayale adapter and the shared producer on
the same node: their exclusive adapter lock rejects competing owners. A read-only
master HTTP host is safe alongside the shared producer. Reuse the existing host
and SSH tunnel after checking identity. For simultaneous jobs, configure separate
initialized node stores and distinct node IDs; never copy `.wnp` to clone a writer.
No helper here initializes nodes, creates tokens, starts services/tunnels, changes
ports, kills processes, or changes existing configuration implicitly.

## Companion check and recovery

```powershell
& backend/.venv/Scripts/python.exe tools/trackprompt-wnp.py check
& backend/.venv/Scripts/python.exe tools/trackprompt-wnp.py probe --origin http://127.0.0.1:9761 --project trackprompt-studio --node render-rtx3060
& backend/.venv/Scripts/python.exe tools/trackprompt-wnp.py drain
```

On the Companion machine, reuse its existing authenticated SSH-forwarded origin
`http://127.0.0.1:19761`, private reader credential, project `trackprompt-studio`,
and enrolled node `render-rtx3060` (alias `render-desktop`). Keep UI port 8765 and
host port 9761 on loopback. These are discovery hints, never permission to replace
an occupied port or enroll a different identity. Provision credentials through
the existing private procedure, never a URL, Git, console output or screenshot.

`probe` requires the existing local `.wnp/auth/read.token`, uses bounded GETs,
disables proxies and redirects, checks exact project/node/stream, and validates
the returned state with the frozen master validator. It reports authenticated
transport separately from worker/provider freshness. ONLINE is never rendered as
work progress by this check. It does **not** certify that the external UI renders
extensions; `companion_ui` remains `unverified`.

| Safe check result | Operator action |
| --- | --- |
| `reader_or_store_permission_denied` | Run under the existing authorized user/service identity; inspect the exact reader-file ACL without weakening unrelated ACLs |
| `reader_or_store_missing` | Select the existing initialized root and credential; do not reset history |
| `reader_unauthorized` | Reconcile the enrolled endpoint and privately provisioned reader credential |
| `wnp_transport_unreachable` | Inspect the existing loopback listener/SSH forward and reuse its owner; do not kill arbitrary processes |
| `job_selection_required` | Explicitly select the intended native job on this node |
| `pending_selection_conflict` | Repair WNP storage/host and drain pending rows before switching jobs |
| `source_identity_conflict`, `outbox_stream_conflict`, `outbox_cursor_ahead` | Preserve the journal/outbox and reconcile; never clear a cache or reset a stream |

The outbox persists sanitized transitions before publication, replays oldest
first, and acknowledges only after the host accepted the event. A lost ACK retries
the same producer key. Worker/provider/progress samples stay transient and never
refresh old source timestamps. A failure before the outbox commit cannot be made
durable: the native authoritative state remains available for the next observation.
No snapshot means no WNP claim; execution proceeds independently.

## Verified boundary on 2026-09-22

Local read-only inspection found project `trackprompt-studio`, node
`render-rtx3060`, stream `6c75240b-8fdd-4abe-be6b-ef3beb17d004`, sequence 15,
historical run `344ab3d2444c`, durable state `waiting/render.finalizing`, and stale
worker evidence. This observation does not override historical verified delivery
artifacts. The sandbox initially denied reader credential/SSH access; an approved
read-only check outside the sandbox could use the credential but still reported
`wnp_transport_unreachable`. Bounded socket probes outside the sandbox on 8765,
9761 and 19761 also timed out. No service,
port, process, configuration or historical run was changed.

The actual `wzhk_render_companion` source was unavailable at the local GitHub and
projects discovery hints. The supplied/downloaded TrackPrompt bundle contains the
host and adapter, but no Companion client. Tests use the actual frozen host,
internal protocol validation, ordered sync and authenticated HTTP; these are not
a substitute for the unavailable real Companion client/UI. Live Companion display
and old Companion client acceptance remain explicitly unverified. To accept the
UI, inspect the enrolled run's standard progress, source freshness, provider state,
history and technical/creative fields in the existing Companion; retain its
source/version and test evidence before claiming end-to-end acceptance.

The existing Windows detached-child regression passed outside the sandbox using
its stable installed Python; the sandbox-only attempt failed executable access.
The backend environment and installed Python 3.12 both lack optional `jsonschema`;
that optional test is skipped. Frozen host validation and byte-for-byte master
manifest validation are exercised independently and passed. No dependency was
installed to replace this explicit boundary.
