# Hayale GCP-only implementation ledger

The final user specification supplied on 2026-09-22 replaces the earlier 11-cloud/5-local architecture. Work directly in the existing checkout, preserve its dirty state and historical deliveries, and do not launch production during implementation.

Architecture: the root PowerShell 7 launcher binds the existing isolated gcloud user configuration. It uploads only the fifteen hash-bound originals and approved original music. A deterministic Google Cloud Batch job runs a source-addressed container built by Cloud Build. Its attached, dedicated service account performs preparation, sixteen Veo requests, audio arrangement, finishing and authoritative verification. GCS generation preconditions fence state changes. Windows downloads the three verified delivery objects and compares hashes.

Cloud work continues without Codex or the terminal. The foreground launcher observes and downloads; closing it does not cancel Batch. Re-running the same command reconnects and delivers. No permanent VM, local media processing, replacement music, alternate model, ADC on Windows, or service-account keys.

- [x] Bind visually reviewed fifteen-source map, exact hashes, distinct cloud-only plan identity and deterministic scene order.
- [x] Implement/test cloud media preparation, native scene verification, explicit music edit, 1536-frame finishing and complete decode.
- [x] Implement/test GCS CAS state, provider write-ahead intent, operation recovery, transfer recovery and three-generation-per-scene limit.
- [x] Implement/test fresh PowerShell/CMD launch, scoped cloud setup/build/Batch submission, transport-only delivery and historical preservation.
- [x] Reuse/test native legacy reconciliation; keep WNP observational with content-free stage projection.
- [x] Run repository checks, actual safe launcher path, read-only cloud boundary checks and independent review. Report any external boundary honestly.

Review focus: two simultaneous launchers; HTTP response lost after accepted submission; shell closed during build or cloud finishing; corrupted/mismatched object generation or source hash; previous verified local master present during interrupted download/replacement.

Ruling: use the cloud manifest as the new workflow authority because the explicit final brief requires detached GCP execution; retain the existing native reconciliation API for historical local jobs. Do not rewrite those jobs into the new workflow or allow unrelated old records to become an artificial lock on a separately bounded cloud job.

Validation: 106 focused workflow/reconciliation tests passed; the separately enabled synthetic 1080p integration passed, including 1536 timestamps and complete audio/video decode. Backend Ruff and mypy (214 source files), 147 frontend tests, lint/typecheck/build, two browser E2E tests and Compose configuration passed. Full backend regression command completed: 2236 passed, 36 failed, eight skipped; targeted recheck resolved two transient/stale-fixture results and retained 34 failures in historical Andromeda bindings, the older Hayale worker-binding tests and V2 prompt-enhancement assumptions. These historical files/evidence were not rewritten.

Cloud setup, code-only image builds and bounded non-generative Batch smoke succeeded. The production prefix was independently confirmed empty. No new Hayale generation or source upload was performed. See `docs/hayale-gcp-only-verification.md` for final artifact identities and external boundaries.
