# GCP video fast-lane privacy and safety boundary

## Local-only material

The following must not be added to provider requests or committed:

- original audio and stems;
- raw lyrics and transcription artifacts;
- source filenames and physical paths;
- unrelated analysis JSON;
- model caches and weights;
- GCP tokens, cookies, service-account JSON, private keys, or environment files;
- downloaded videos and thumbnails;
- provider response logs that contain unsafe details;
- runtime authorization and operation records.

## Provider-disclosed material

Only the following may leave the local machine:

- project-approved visual identity;
- one sanitized shot prompt and negative prompt;
- generation parameters;
- optional visual reference images that the operator owns or is authorized to use;
- a generated-output GCS prefix.

The first content pack explicitly describes an invented adult protagonist and prohibits celebrity or real-person impersonation.

## Audio handling

`generateAudio` is always false. The provider never needs the song. The original master remains local and becomes the sole timeline clock during Resolve export and FFmpeg mux.

## Logs and telemetry

- Never log `Authorization` headers or access tokens.
- Bound provider error bodies before persistence/UI display.
- Store only operation names and safe GCS output URIs.
- Redact unsafe local paths from browser-visible API payloads.
- Runtime paths may exist in ignored local artifacts needed by the Resolve handoff.

The browser-facing video job omits the GCP project, bucket, audio path, local clip paths, private reference-image paths, operation names, provider-diagnostic paths, and raw provider responses. Request previews intentionally show the exact sanitized prompt, approved reference GCS identity, and authorized GCS output prefix to the local operator before spending. Redacted provider diagnostics remain local under `.trackprompt-data/video-generation/provider-errors/`; only their bounded status and diagnostic ID reach the UI.

## Deletion

Generated clips should remain until the local preview, Resolve import, and final delivery are verified. Local deletion and GCS deletion are separate explicit actions. Do not auto-delete cloud output before local media verification.

Cancellation preserves attempts, receipts, and verified media for diagnosis and safe resume decisions. It is not deletion. A future explicit delete operation must remove the video job row, event rows, private runtime directory, downloaded clips, generated exports, and any separately selected GCS objects without broad path expansion.
