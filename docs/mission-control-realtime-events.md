# Mission Control real-time events

The Mission Control backend owns durable render state. The React client uses a
same-origin event stream for updates and bounded HTTP recovery when a stream is
temporarily unavailable. Rendering never depends on a browser connection.

## Transport and replay

`GET /api/mission-control/events` is a Server-Sent Events stream. A client sends
its last observed sequence through `Last-Event-ID` or the supported replay
query. Every persisted event has a monotonically increasing sequence number.
On reconnect the backend replays retained events after that sequence, then
continues with live updates.

The UI retains its last authoritative snapshot while it shows **Reconnecting**.
After refresh it loads persisted jobs first, reconnects to the stream, and does
not issue a second start request. Start requests are idempotent for an already
active exact scene/profile/output identity.

## Event envelope

The typed `RenderEvent` envelope contains `schemaVersion`, `sequence`,
`timestamp`, `eventType`, `jobId`, and `projectId`. State-bearing events may
also contain:

- state and phase;
- scene/profile IDs and SHA-256 identities;
- frame range, current frame, rendered, in-flight, validated, and published
  counts;
- active chunk range/progress and completed/total chunks;
- current, rolling median, rolling mean, and p90 seconds per frame;
- ETA, expected completion, and qualitative confidence;
- used, projected, and free storage;
- GPU, VRAM, temperature, CPU, and RAM observations when locally available;
- preview reference, latest safe frame, activity line, warning, structured
  error, process state, and safe-stop state.

Unavailable telemetry is `null`/unknown; Mission Control never invents a
numeric measurement.

## Persisted state machine

Jobs use explicit states rather than inferring state from `blender.exe`:

```text
VALIDATING -> AUTHORIZATION_REQUIRED -> READY -> STARTING -> RUNNING
RUNNING -> STOP_REQUESTED -> FINISHING_CURRENT_CHUNK -> PAUSED_SAFELY
PAUSED_SAFELY -> RESUMABLE -> STARTING
RUNNING -> COMPLETE
any active state -> FAILED or CANCELLED
```

Relevant phases include scene load, frame render/write, frame and chunk
validation, chunk publication, and storage wait. The shared schema reserves
master/delivery encode, local audio mux, and final-verification phases for the
future encode adapter; this implementation does not emit those phases as if an
encode were running. Every actual transition and event is committed to the
local Mission Control state store before it is presented as authoritative.

## Heartbeats and long frames

While a renderer is active, the backend publishes a heartbeat at least every
two seconds even when no frame completes. Heartbeats distinguish:

- elapsed time on the current frame;
- last completed frame and event;
- backend/event-stream connection;
- renderer process and watcher state;
- age of the last renderer output.

The UI therefore says **Rendering is still active** during a long frame rather
than implying that progress stopped. When the production wrapper reports only
chunk boundaries, Mission Control shows the active chunk and authoritative safe
count; it does not relabel the last published frame as the exact frame currently
inside Blender.

## Safe versus in-flight

`inFlightFrames` are written inside the active chunk but are not yet the
recoverable production sequence. `publishedFrames` passed validation and the
atomic chunk publication contract. The UI labels these as **In progress** and
**Safe** respectively and bases resume progress on the safe count.

## Logs and previews

Structured activity events power the concise feed. A bounded log endpoint
supports search/copy diagnostics, while raw output remains under Technical
details. Secrets and private media metadata are not logged.

Preview responses serve only an integrity-checked completed image. The preview
URL includes the frame/version, uses no-cache headers, and remains labelled with
its frame and timestamp. A partially written PNG is never returned.
