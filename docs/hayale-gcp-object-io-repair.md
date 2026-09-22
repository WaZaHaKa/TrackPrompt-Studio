# Hayale production observer repair

The 2026-09-22 `storage_http_404` was a Windows observer read failure, not a seed upload failure. The canonical Batch worker continued after the observer exited. Retained task logs show seed preparation continuing at 16:18:19Z and generation beginning at 16:18:47Z. The subsequent cloud manifest had all sixteen prepared seeds and accepted provider operations.

## Root cause and reproduction

`GcsClient.get_json` issued two requests: object metadata GET to learn generation N, followed by a media GET pinned to N. The worker could conditionally replace the live `state/run-manifest.json` between these requests. Object Versioning is disabled on the bucket; replaced generations are unavailable to an ordinary generation-bound GET, including when retained by soft delete. The observer treated the resulting 404 as permanent and exited while the worker continued.

A read-only reproduction against the actual canonical manifest captured generation `1790095190035404`, observed replacement by `1790095194964410`, then received the identical `storage_http_404` from `GET` of the former generation with `alt=media`. Private evidence, task logs, source receipts and exact worker IAM bindings are retained under `.trackprompt-data/hayale-gcp-final/repair-audit/`.

Latest JSON reads now reacquire metadata only after a media 404 **and proof that the live generation changed**. Five repeated replacements produce a retryable snapshot error. Explicitly pinned reads, truly missing objects and unchanged generations do not silently switch versions. No provider submission is involved in this read retry.

## REST and IAM audit

The JSON API [insert contract](https://docs.cloud.google.com/storage/docs/json_api/v1/objects/insert) explicitly permits the object name in metadata instead of the query string. The existing multipart and resumable requests include that name; production source uploads and all sixteen worker seed uploads succeeded. This was not the 404 cause, and no upload contract or IAM permission was weakened.

Metadata/media [GET paths and generation parameters](https://docs.cloud.google.com/storage/docs/json_api/v1/objects/get) match the documented API. The replacement behavior follows [Object Versioning semantics](https://docs.cloud.google.com/storage/docs/object-versioning). Conditional create/overwrite uses `ifGenerationMatch`; the new disposable metadata PATCH additionally binds metageneration. Cleanup DELETE is restricted to a smoke UUID child prefix and exact object generation.

Errors now preserve a fixed operation category, HTTP method/status, hashed object identity and numeric generation. Upload initiation and session PUT are distinct categories; tokens, raw session URLs, source names and provider error bodies are excluded from public diagnostics.

## Real object-I/O qualification

`-Smoke` now runs the actual Batch identity and production `GcsClient` inside `hayale-final/<canonical-run>/smoke-object-io/<image-id>/`. It tests bucket metadata, resumable creation, object metadata, pinned byte download, SHA-256, exact-prefix list, generation-bound access, multipart JSON creation, conditional JSON replacement, readback, metadata PATCH and exact-generation cleanup. Its retained proof binds the Batch UID, service account, image-specific prefix and individual operation results. It never constructs a Veo client or uploads private source media.

Normal invocation automatically checks this image-bound smoke proof before starting or recovering production. It records the actual running image separately from a newer available recovery image and waits for a fresh recovery worker to claim an older blocked snapshot. A healthy existing worker is preserved. Permanent worker exits fail the task rather than repeatedly encoding the same failing candidate; transient exits retain bounded retries.

The first upgraded real smoke passed under the existing worker identity and unchanged IAM, including create/get/list/update/delete permissions. It also observed an unavailable replaced JSON generation, and removed only its two disposable live object names. The proof is retained in the canonical run prefix.

## Operator command

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\FINISH-HAYALE-GCP.ps1
```

The run remains `hayale-gcp16-fbbaa46568b1a30d0e760e76`. Already bound originals, seeds and verified scenes are reused. Separate finishing verification diagnostics were added after the real worker reported a later failure; no native resolution, frame, audio or source-integrity requirement was relaxed.

## Later finishing defect discovered during recovery

The actual cloud verifier subsequently reported video at exactly 1920×1080, 24 fps, 1536 frames and 64 seconds, but AAC audio at **61.994 seconds** (2,975,712 samples at 48 kHz). The arranged WAV had already passed its exact 64-second check. The combined frame-limited video/AAC command cut audio before consuming the entire arranged track.

Finishing recipe `hayale-gcp-finish/2-video-first-full-audio` now encodes and verifies the frame-limited video without audio, then stream-copies that video while encoding the complete arranged soundtrack. The mux command has no frame limit, time limit, shortest-stream cutoff, silent padding or stretching. The verified video is cached for a mux retry, and the new recipe identity prevents adoption of the old short-audio candidate. The original music arrangement and all final acceptance thresholds are unchanged.

A separate provisioning delay was traced to an actual `ZONE_RESOURCE_POOL_EXHAUSTED` error for `us-central1-f`, with project CPU quota unused. New jobs remain in the approved region and use zones `us-central1-a`, `us-central1-b` or `us-central1-c`; existing jobs, build/image identities and production evidence were preserved. The smoke identity also includes this placement policy, so a prior success cannot be misrepresented as qualification of a different execution setup.

## Final acceptance evidence

- Regression/scoped suite: **99 passed**, with the separately enabled real synthetic 1080p integration also passing. Ruff and mypy passed; the clean publication candidate passed the same 99 tests.
- Final immutable worker image: `sha256:596c2ef73cbe7c2155887e3783690a184eb8a077a275f288eafeed5294238f3b`.
- Final image's real Batch object-I/O qualification: `hayale-smoke-89b598a88e8ed70becd0628b-smoke`, all required categories passed and both disposable objects removed. No IAM changes were necessary.
- Same-run recovery worker: `hayale-gcp16-fbbaa46568b1a30d0e760e76-worker-3`.
- Reached `cloud_verified`, downloaded the three delivery files, matched the cloud SHA-256 and opened the MP4 at **2026-09-22 17:48:42 UTC**.
- Cloud verification passed 1920×1080, constant 24 fps, exactly 1536 frames / 64 seconds, H.264/AAC, audio at zero, every timestamp and complete audio/video decode.
- Final MP4 SHA-256: `6b3fc0c2104aa4fef6a77c928f1f7a3bb43c56ebbb30cb529404af97949511fe`.
- All sixteen scene/attempt records and every original source receipt exactly match the pre-recovery snapshot: **16 accepted operations total**, no duplicate provider work and no source re-upload.
- Actual repeat of the same one-command launcher reopened the final file successfully at **17:53:26 UTC**. The canonical cloud manifest was unchanged and no fourth production worker was created.
- The previous master remains in the local `history` directory with its original SHA-256 `ade2c6618b98768e2eeef645e1b88a79e57f0467400515f5efb40aff8004b858`.

Detailed cloud, transport, repeat-run and preservation receipts are retained privately under `.trackprompt-data/hayale-gcp-final/repair-audit/`. Windows performed transfer/hashing/opening only; production preparation, generation, audio work, encoding and authoritative verification ran in GCP.

Changed production modules: `cloud.py` (snapshot read recovery and safe transport diagnostics), `worker.py` (real object smoke and precise failure reporting), `launcher.py` (qualification and same-run recovery), `infrastructure.py` (smoke arguments, regional placement and permanent-error handling), and `media.py` (verified video-first/full-audio finishing). Four scoped test files and the operator/incident documentation accompany the changes. The PowerShell and CMD entrypoints are unchanged.
