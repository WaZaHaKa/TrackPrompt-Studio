# Explicit clarifications to the agreed design candidate

These are implementation clarifications, not hidden changes to your goals. The
wire major remains `wnp/1`; implementation release is `1.0.0`. No earlier deployed
WNP installation is assumed.

1. **Replay needs a reducer.** An event that changes the durable snapshot carries
   `snapshot`: the COMPLETE post-event durable state payload, excluding protocol,
   stream, sequence, timestamp and observations. `state.changed` requires it.
   Informational events without it leave the payload unchanged. The reference
   writer includes it on every event. Event `task` context alone never selects a
   current task or completes an entire run.
2. **Metrics have their own clock.** `observations.sampled_at` and optional source
   observation timestamps are separate from durable `timestamp`/`sequence`.
   `.wnp/observations.json` is a private implementation file, overlaid on GET status.
   No GPU sample increments the durable cursor. Missed historical metric samples
   cannot be reconstructed; durable events can.
3. **Sync uses a fixed watermark.** The first page pins `head`; subsequent requests
   supply `until=head`. The last page includes the snapshot AT that same head.
   `next_after` is the last actual delivered sequence, not an unearned jump.
4. **Genesis is explicit.** The reference host persists `.wnp/genesis.json` for
   deterministic recovery of sequence zero. It is an implementation extension,
   not a new public endpoint or replacement for the four public documents.
5. **Evidence is not omniscience.** Hashes establish byte identity, not authenticity,
   semantic correctness or legal authority. Contradictory provider observations,
   claimed success and verification evidence cause reconciliation/integrity errors.
   There is no unconditional rule that a log entry overrides physical evidence.
6. **Missing history is ambiguous.** A provider can accept a job and lose history.
   `sending/accepted + absent from queue/history` never authorizes automatic retry.
   The recovery library does not promise exactly-once provider execution.
7. **Raw output is not the final movie.** Verified 129-frame raw footage is reusable
   work, not a completed 60-second 1080p24 deliverable. Completion is stage-specific.
8. **Limits are explicit.** Sequence uses JSON-safe integers through 2^53−1. This
   release caps an event at 64 KiB, a sync response at 1 MiB, and fetched evidence at
   8 MiB. Large media remains in application storage and is represented by a small
   approved manifest. Unknown optional fields remain forward-compatible.
9. **Restore is not a normal restart.** Ordinary restarts retain the stream. A
   destructive rollback/reset must produce a new stream with explicit archival
   handling. A same-stream cursor rollback is an error, never a silent reset.
10. **Observation and control remain separate.** WNP can show an ambiguous run; it
    cannot restart it. The TrackPrompt repair belongs to its local execution layer.
11. **Secure transport profile.** The implemented service binds numeric loopback
    only, with a reader token. Remote access uses an SSH tunnel; optional TLS can
    also be enabled. No unauthenticated LAN API, shell execution endpoint or VPN
    bypass is introduced.
12. **Capabilities are descriptive.** They are not authorization grants. This
    release implements project/node/status/sync/evidence/health. It does not claim
    new runs/tasks collection endpoints are implemented merely because examples
    in earlier discussion listed those capabilities.
