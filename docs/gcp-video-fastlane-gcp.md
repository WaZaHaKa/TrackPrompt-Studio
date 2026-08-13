# GCP and Veo implementation notes

## Reviewed model profiles — snapshot 2026-08-13

The starter targets the generally available Vertex AI model IDs:

```text
veo-3.1-fast-generate-001
veo-3.1-generate-001
```

The reviewed model specifications support 4-, 6-, or 8-second generations, 16:9 or 9:16 output, 24 FPS, and `us-central1`. The standard model supports 720p, 1080p, and 4K. The reviewed Fast GA model specification lists 720p and 1080p.

The 2026-08-13 video-only pricing snapshot is `$0.10/output-second` for Fast 1080p, `$0.20/output-second` for standard 1080p, and `$0.40/output-second` for standard 4K. Sixteen eight-second, single-sample clips therefore have base estimates of `$12.80`, `$25.60`, and `$51.20`. The UI shows this dated snapshot and a separately configured hard maximum before authorization. Billing is authoritative in the operator's GCP account, so the snapshot must be reviewed again when provider pricing changes.

The current GA model page lists fixed-quota consumption and does not promise pay-as-you-go access. The free doctor can prove CLI/account/project/API/bucket readiness but deliberately reports model access as unknown. Only the operator-authorized smoke request can prove live model entitlement or quota.

Official references used for this package:

```text
https://cloud.google.com/vertex-ai/generative-ai/docs/models/veo/3-1-generate
https://cloud.google.com/vertex-ai/generative-ai/docs/model-reference/veo-video-generation
https://cloud.google.com/vertex-ai/generative-ai/pricing
https://cloud.google.com/vertex-ai/generative-ai/docs/video/video-gen-prompt-guide
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
    "compressionQuality": "optimized",
    "task": "textToVideo"
  }
}
```

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
