# Cloud render recovery

The provider-neutral scheduler binds each chunk to job, scene, profile, and
package hashes; start/end frame; worker; lease token and expiry; attempt count;
output manifest; per-frame hashes; timing; and cost metadata.

The scheduler and mock worker exercise lease renewal, heartbeat, expiry,
retryable work, bounded publication, and conflict quarantine offline. If a
worker disappears, an expired lease can become retryable. Speculative duplicate
rendering is disabled by default.

This does not yet constitute a production recovery service: a bounded Blender
runtime exists and is fake-runner tested, but it has not been environment-tested
on Brev; there is no active Brev fleet monitor, and the local production
renderer does not claim the shared SQLite queue.

## Returned-frame recovery

The offline `import-return` command copies returned data into quarantine before
validation. It checks identities, assigned frame range/count, image contract,
canonical filenames, hashes, gaps, and unexpected data. An existing valid local
frame is never silently overwritten. Identical hashes are deduplicated;
differing valid local/cloud outputs remain quarantined with local preference
until the operator decides.

The exact `scheduler-status`, `mock-worker`, and `import-return` commands are in
[`cloud-rendering.md`](cloud-rendering.md). Scheduler status is an offline
snapshot; opening SQLite can create or update journal/WAL metadata. Mock work
and import are explicitly labelled offline local mutations.

## Cancellation and termination

Scheduler cancellation and budget state can prevent new leases in the tested
primitives. Do not claim that the current UI continuously polls cost, thermal
state, or worker health. Do not assume all current uploads will finish after a
cancellation; rely only on validated, durably published chunks.

`FleetController` can decide to stop completed, idle, or budget-blocked
instances, but no active process currently calls it. Automatic worker
termination is therefore not operational. Manual `brev-teardown` with exact
stop/delete confirmation strings is required for an already known live
instance; see [`nvidia-brev-rendering.md`](nvidia-brev-rendering.md).

Keep cloud frames until the video-only master or returned sequence is locally
verified, private audio is muxed locally, ffprobe checks pass, and visual QA is
approved. Instance deletion and object-storage deletion are separate actions;
each requires explicit resource-specific confirmation.
