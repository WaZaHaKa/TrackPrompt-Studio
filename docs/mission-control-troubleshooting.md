# Mission Control troubleshooting

Mission Control keeps errors in the browser with a plain-language action and an
expandable Technical details section. Use this guide when that action is not
enough.

## The launcher does not open

Run the noninteractive validation from the repository root:

```powershell
.\WZHK-Media-Launcher.cmd -ValidateOnly
```

It skips unusable Python shims until one can import the Mission Control server
and print its CLI help, checks that the React build fingerprint is current or
npm is available for normal startup to rebuild it, and checks the server
module, loopback policy, PowerShell parsing, and legacy fallback. Validation
does not build React, open a browser, start the backend listener, or start
Blender. Backend logs and the ignored instance descriptor live below
`.trackprompt-data\mission-control\`.

The launcher prefers port 8765 and automatically advances when it is occupied.
Do not edit a source file to change ports. A healthy descriptor reopens the
same process. If an existing recorded Python process is alive but temporarily
unresponsive, the launcher leaves it untouched instead of creating a duplicate.

## The page says Reconnecting or Offline

Keep the page open briefly; the client replays missed events from its last
sequence. Launching Mission Control again is also safe and should reopen the
same instance. Do not start Blender manually. If the backend genuinely ended,
restart the launcher; persisted jobs return as resumable, failed, or orphaned
according to their stored state and exact output identity.

## Authorization required

This is expected for a valid new or changed saved profile. Choose **Authorize
now** inside the render wizard and complete both confirmations. If it fails:

- confirm the saved profile still exists;
- confirm the approved scene exists and matches its recorded SHA-256;
- do not edit the profile while the dialog is open;
- expand Technical details for the exact mismatch, then retry.

Mission Control does not update the profile JSON to authorize it. It writes a
separate exact sibling record atomically. Any later profile or scene change
invalidates that record.

## Output folder cannot be used

Read the exact conflicting entries shown in the wizard. Hidden/system entries
also make a supposedly empty folder nonempty. Choose **Create a new render
folder here** to generate a meaningful unique child without deleting or moving
the existing entries. A previous render can resume only when its scene,
saved-file profile hash, frame contract, and output manifest all match.

## Preflight fails

- **Blender not found:** select the local Blender 5.2 executable in Settings or
  repair the documented installation.
- **Scene/profile mismatch:** reselect the approved scene/profile; never replace
  the frozen file in place.
- **Storage not ready:** choose a suitable drive or free space outside managed
  render output. Mission Control does not delete frames automatically.
- **Conflicting render:** open the recorded job or wait for its safe stop.

Dry-run and preflight never initialize a production sequence or start a full
timeline.

## Rendering appears still

Check current-frame elapsed time, last renderer output, process state, watcher
state, and the heartbeat. A complex single frame can take longer without being
stuck. Use **Request stop after current chunk**, not Task Manager, for the
primary recovery path. If the process failed before publication, inspect logs;
the active chunk may need to render again while previously safe chunks remain.

## Preview is old

The caption includes its frame and timestamp. Mission Control intentionally
keeps the last structurally valid completed PNG while another file is being
written. The versioned URL updates after a valid new preview is available.

## Calibration plan missing

Return to Calibration and reselect the retained evidence or create a new
bounded plan. Mission Control reports missing evidence as a recoverable error;
it does not close the app or infer a replacement plan. Candidate execution and
review persistence are not connected in the React backend yet; use the legacy
interface if either action is required.

## Performance mode did not restore

Open Settings and choose **Restore now**. Confirm the displayed previous and
current Windows power plans. Mission Control never requests Realtime priority.
If Windows was forcibly restarted during a render, use the recorded ignored
restore-state path and the legacy performance documentation for manual review.

## Cloud action is disabled

Package creation/validation and live Brev provisioning, fleet control,
teardown, remote encode, and download remain unconnected or unverified.
Disabled controls are intentional. Readiness inspection does not prove that a
live cloud workflow exists. See
`docs/nvidia-brev-rendering.md` before any separately authorized provider work.

## Encode action is disabled

Mission Control can identify a complete published sequence and report FFmpeg
readiness, but this React backend does not yet start the reviewed encoder or
private-audio mux. Open the frame output from the UI and use
`WZHK-Media-Launcher-Legacy.cmd` for the existing production encode workflow.
No encode is reported as complete unless the underlying tooling verifies it.

## Legacy fallback

`WZHK-Media-Launcher-Legacy.cmd` opens the preserved PowerShell interface. Use
it only while resolving a React/backend problem; it operates the same exact
production engine and authorization records.
