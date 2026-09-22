# Implementation verification — 2026-09-22

The maintained operator entrypoints are `FINISH-HAYALE-GCP.ps1` and `FINISH-HAYALE-GCP.cmd`. Supporting code lives in `backend/app/video_generation/hayale_gcp/`; the allowlisted cloud image definition is in `cloud/hayale/`. The final source map and visual selection rationale are in `video-projects/hayale-flowers-promo/v4-gcp-only/`. Operator behavior is documented in `docs/hayale-gcp-only.md`.

## Results

| Boundary | Evidence-backed result |
| --- | --- |
| Source selection | Fifteen visually reviewed, distinct original images; required role counts and scene order validated. Approved original audio SHA-256 retained. |
| Fresh Windows launch | Actual PowerShell 7 and CMD tests passed from an unrelated directory with a space-containing repository path and cleared prior task environment. |
| Scoped workflow/recovery tests | 106 passed; one normal-gated synthetic integration skipped in this command and run separately successfully. Includes 27 existing legacy-reconciliation cases. |
| Synthetic media integration | Actual 1920×1080 H.264/AAC, 24 fps, 1536 frames, 64 seconds, zero audio offset, complete decode and every video timestamp verified. Receipt explicitly says `production: false`. |
| Backend static checks | Ruff passed; mypy passed for 214 source files. |
| Frontend | 147 tests passed; lint, typecheck and production build passed. |
| Browser | Both E2E tests passed on isolated ports with synthetic state. |
| Compose | Configuration validation exited successfully. |
| Existing GCP authentication | The actual launcher read the active, exact project through the existing isolated binding. No browser reauthentication or ADC switch occurred. |
| Cloud setup | Dedicated Hayale identities, repository, scoped grants and Batch API successfully created/enabled/reused. Existing unrelated policy bindings retained. |
| Cloud worker | Allowlisted code-only images built successfully; non-generative Batch runtime smoke verified attached metadata identity, Pillow/FFmpeg availability and bucket metadata access. |
| Actual new production | Not started. Independently checked new run prefix: zero objects and no production run manifest. No new source-media upload or Veo request. |
| Historical delivery | Original MP4 still has SHA-256 `ade2c6618b98768e2eeef645e1b88a79e57f0467400515f5efb40aff8004b858`. |
| WNP/Companion | Reuses existing observation integration; no new node or production selection created. Live Companion display remains unverified and cannot block cloud execution. |
| Git | Original dirty checkout preserved. Only this workflow's source, synthetic tests and documentation are selected for `codex/hayale-gcp-only-final`; no main merge or force-push. |

## Broader backend limitations

The required full backend test command completed with **2236 passed, 36 failed and eight skipped**. A focused rerun of those 36 cases produced **two passes and 34 retained failures**. The new delivery fixture passed after aligning it with the strict cloud-verification contract; the older detached-heartbeat test passed on rerun.

The remaining failures are outside this new workflow: 22 Andromeda release/story/authorization tests encounter existing immutable-file/hash binding mismatches; nine older Hayale worker-binding tests expect superseded helper/schema APIs; three V2 tests still request disabled Veo prompt enhancement. Historical artifacts were not rewritten and provider validation was not weakened to make these tests pass. The full raw logs and recheck are retained privately in `.trackprompt-data/hayale-gcp-full-pytest.log` and `.trackprompt-data/hayale-gcp-failure-recheck.log`.

## Bound identities

- New canonical run: `hayale-gcp16-fbbaa46568b1a30d0e760e76`.
- Final worker source digest: `197e874c63f4149140d0926c6a463f76955565b8e786f880fcb851bb0e1bb296`.
- Final built image digest: `sha256:dda7c45a6961ebe1342635785450af83d55f514ffb2a5b19dfdc15894af8c87f`.
- Final image build: `5b598625-5650-452b-bc05-e11976b9b729`.
- Final image also passed its own non-generative Batch smoke job, `hayale-smoke-dda7c45a6961ebe134263578-smoke`.
- Synthetic integration MP4 SHA-256: `ecaa467baa52fcd7d5fb0c5f5340d18afa50824e8939d49d7c478b9659ae52f3` (test evidence only).

Private check receipts live under `.trackprompt-data/hayale-gcp-final/checks/`; the controller retains the canonical plan binding, last cloud manifest, build identity and transport receipt there. No production completion claim is made. Live Veo acceptance, creative fidelity, production cloud finishing and final download/open will be exercised by the operator's single command.

An initial automatic approval rejection interpreted the old infrastructure prohibition as current. The latest explicit user clauses authorizing minimal workflow-specific infrastructure and safe cloud setup verification were checked; retry of the identical setup command was approved. No denied action was bypassed.

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\FINISH-HAYALE-GCP.ps1
```
