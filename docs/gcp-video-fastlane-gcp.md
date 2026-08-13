# GCP and Veo implementation notes

## Reviewed model profiles — snapshot 2026-08-13

The starter targets the generally available Vertex AI model IDs:

```text
veo-3.1-fast-generate-001
veo-3.1-generate-001
```

The reviewed GA model specifications support 4-, 6-, or 8-second generations, 16:9 or 9:16 output, 24 FPS, and `us-central1`. Google's current REST contract lists 720p and 1080p for both GA model IDs. Its first/last-frame guide labels 4K as preview-model-only, while the preview endpoints were retired on 2026-04-02. Mission Control therefore treats 1080p as the supported final-delivery target and leaves the 4K profile visible but unavailable until a currently supported 4K model contract is explicitly configured and reviewed.

The 2026-08-13 video-only pricing snapshot is `$0.10/output-second` for Fast 1080p and `$0.20/output-second` for standard 1080p. Sixteen eight-second, single-sample clips therefore have base estimates of `$12.80` and `$25.60`. The dormant 4K profile retains its `$0.40/output-second` planning snapshot but cannot be compiled against the current GA model IDs. The UI shows the dated snapshot and a separately configured hard maximum before authorization. Billing is authoritative in the operator's GCP account, so the snapshot must be reviewed again when provider pricing changes.

The current GA model page lists fixed-quota consumption and does not promise pay-as-you-go access. The free doctor can prove CLI/account/project/API/bucket readiness but deliberately reports model access as unknown. Only the operator-authorized smoke request can prove live model entitlement or quota.

Official references used for this package:

```text
https://cloud.google.com/vertex-ai/generative-ai/docs/models/veo/3-1-generate
https://cloud.google.com/vertex-ai/generative-ai/docs/model-reference/veo-video-generation
https://cloud.google.com/vertex-ai/generative-ai/pricing
https://cloud.google.com/vertex-ai/generative-ai/docs/video/video-gen-prompt-guide
https://docs.cloud.google.com/vertex-ai/generative-ai/docs/video/generate-videos-from-first-and-last-frames
https://docs.cloud.google.com/vertex-ai/generative-ai/docs/release-notes
```

## Asynchronous REST workflow

Submit:

```text
POST https://us-central1-aiplatform.googleapis.com/v1/projects/{project}/locations/us-central1/publishers/google/models/{model}:predictLongRunning
```

Poll:

```text
POST https://us-central1-aiplatform.googleapis.com/v1/projects/{project}/locations/us-central1/publishers/google/models/{model}:fetchPredictOperation
```

The submit response returns a durable operation name. Completed video results expose GCS URIs. Mission Control persists an operation receipt containing the plan/shot/attempt identity and operation name, then resumes by that name. Raw provider bodies and bearer tokens are not exposed to the browser.

Every HTTP failure now writes a unique redacted diagnostic under `.trackprompt-data/video-generation/provider-errors/`. The receipt includes phase/job/shot/attempt identity when available; URL without query data; request field names and prompt/storage hashes rather than request content; HTTP status; safe response headers; provider status/code/message; and the exact JSON or text body after recursive credential redaction. The public job error exposes only bounded safe fields and the diagnostic ID, never the local path or raw body.

The historical 2026-08-13 HTTP 400 occurred before this capture path existed. Its exact response body was read and discarded by the old adapter and is not present in Mission Control logs, SQLite, or job receipts, so it cannot be reconstructed honestly. The retained exact request proves two invalid request choices: `parameters.task` was not in Google's Veo request schema, and the batch requested `4k` from `veo-3.1-generate-001` even though the current GA contract permits only 720p/1080p. Both are fixed: `task` is omitted and GA 4K is rejected locally before authorization.

## Request boundary

The starter sends parameters equivalent to:

```json
{
  "instances": [{"prompt": "sanitized visual prompt"}],
  "parameters": {
    "storageUri": "gs://bucket/prefix/project/shot/",
    "sampleCount": 1,
    "durationSeconds": 8,
    "seed": 18031001,
    "aspectRatio": "16:9",
    "resolution": "1080p",
    "personGeneration": "allow_adult",
    "negativePrompt": "sanitized negative prompt",
    "enhancePrompt": true,
    "generateAudio": false,
    "compressionQuality": "optimized"
  }
}
```

The exact request contract is digest-bound as `vertex-veo-predict-long-running-v2`. Older saved plans do not have that version, so they cannot be authorized, resumed, or retried. The operator must compile a fresh 1080p plan and enter its new digest-specific confirmation phrase.

## Visual continuity and supported conditioning

`continuity-profile.json` is provider-neutral and contains the locked Quantum Siren identity, wardrobe/face/hair/age description, world/camera/lighting/texture/palette anchors, named continuity groups, master seed, and `sha256-v1` derivation contract. A shot seed is deterministically derived from:

```text
masterSeed + projectId + ordered continuityGroupIds + shotId + variationIndex
```

"Retry same setup" preserves the seed, prompt, reference hash, and request. "Generate new variation" increments `variationIndex`, derives a new seed, changes the plan digest, archives the old plan/authorization/request previews, and requires a new digest-specific authorization before any submit.

The current Veo 3.1 GA endpoints support first-frame and first/last-frame conditioning but do not support `referenceImages` character-reference mode. An operator-selected private JPEG/PNG is therefore compiled as a hash-bound first-frame `instances[0].image`, uploaded to its exact GCS URI only after authorization, and never sent through the unsupported `referenceImages` field. An accepted verified clip can also supply its extracted final frame to its declared next shot. That action changes the reference hash and plan digest, preserves prior attempts, and requires fresh authorization before regeneration. Independent model output still cannot guarantee a perfect character lock.

## Credentials

Use the active `gcloud` account and short-lived access tokens. Do not create a service-account key merely to make the first run work. Do not write bearer tokens to logs, manifests, operation records, or Git.

## External blockers

Code cannot manufacture provider model access or quota. The integration must distinguish:

- authentication failure;
- project permission failure;
- API disabled;
- bucket permission failure;
- model not available to the project;
- quota/rate exhaustion;
- provider safety filtering;
- transient provider/network failure.

A missing live quota blocks only the paid smoke/batch, not offline implementation, tests, exports, mock operations, or a complete source release.
