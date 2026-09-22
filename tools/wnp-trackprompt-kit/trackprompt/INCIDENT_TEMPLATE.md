# Local incident report — fill from actual evidence, not assumptions

Known historical incident: run344ab3d2444c, Shot001129framefiles+verification marker,
Shot002submissionfiles, repeated "stale" outcome. Treat these as observations from
17Sept2026, not the current machine state.

Record: collectionUTC; localHEAD+dirtydiff; launcherentrypoint; runnerPID/starttime/
parent; ComfyUIendpoint+PID/starttime; runID; shot/attempt; workflowSHA; inputSHA;
promptID; queue/history observation timestamp; lastheartbeat; lastmaterialprogress;
rawframe integrity/provenance; first failed boundary; causal code reference.

For every hypothesis write supporting evidence, disconfirming test and outcome.
Record secrets only in private local sources—not in this sanitized report.

Completion labels:
- REFERENCE_TESTED: supplied components passed synthetic tests.
- INTEGRATED_LOCAL: real repo wiring and integration tests passed.
- RECOVERED_LIVE: existing incident run reconciled with verified no duplicate submit.
- FINAL_VERIFIED: full final artifact validated and reviewed.

No label implies the next one. A root-cause field can explicitly be UNDETERMINED.
