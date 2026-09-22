# Hayale 16 x 4 implementation checklist

The operator approved implementation, synthetic/non-generative checks and publication
to a feature branch. The supplied `TrackPrompt_GCP_16x4_WNP_Codex_Prompt(1).md`
is the brief; the originally named `.codex/handoffs` file was absent.
No paid render, private upload, cloud infrastructure mutation or local GPU fallback
is authorized by this implementation session.

## Architecture and reuse

- `VideoGenerationController` and `MissionControlStore`: single execution authority,
  existing prepared-plan registration, authorization, attempts, SSE and public UI.
- `gcp_veo.py`: managed Vertex/Veo CLI-token adapter; extend its existing safe
  diagnostics, private operation storage and download/verify stages.
- `gcp_auth.py` and `tools/reauth-gcp.ps1`: shared explicit isolated account/project
  binding, bounded native execution, non-generative checks and requested login.
- Shared WNP projection: reuse bundled frozen master/adapter, durable outbox and
  writer locks; retain the old import path and historical Hayale observer.
- New versioned Hayale scenario: use prepared jobs, local photo/logo composition,
  first-frame provider requests, explicit source trims and local music arrangement.
- Existing V2.1 recovery/receipts and completed run `344ab3d2444c`: preserved.
  New work does not reinterpret seven-shot/129-frame historical contracts.

## Work and verification

1. Preserve dirty state, inspect providers, operator scripts, WNP and recovery.
   Branch from current HEAD; stage only reviewed task files/dependencies.
2. Add exact scene contract, selected asset/consent hashes, four profiles, explicit
   music edits and deterministic compile. Test counts, trims, costs, missing inputs,
   unsupported modes and preview isolation before implementation.
3. Strengthen existing GCP attempts and shared credential binding. Test uncertain
   submission, crash/restart, expired spending approval, safe reads and cost caps.
4. Generalize WNP projection and real lifecycle hooks; prove schema, replay,
   privacy, stale-source behavior, writer isolation and unavailable monitor safety.
5. Connect operator prepare/authorize/start/resume/status/stop/verify/export to
   the existing controller and detached lifecycle, with worker acceptance proof.
6. Run real synthetic 64-second FFmpeg/ffprobe export. Keep technical acceptance
   separate from unperformed customer creative approval and cloud rendering.
7. Run AGENTS.md backend/frontend/build/Compose/E2E checks. Record environmental
   and pre-existing failures separately. Review candidate without unrelated files.
8. Commit reviewed files, push verified remote feature branch, compare remote SHA;
   create a reviewable PR when tooling permits. Never merge or force push.

## Review focus

- Unknown POST outcome cannot become an automatic reroll through generic FAILED.
- Exact operation identity must survive private persistence and public redaction.
- Preview completion never schedules an automatic final generation.
- Local logo/customer scenes must never enter provider upload/submit paths.
- Four-second trims cannot silently pad a bad clip or shorten the 64-second edit.
- Observer reads must not manufacture worker/provider heartbeat freshness.
- No private filenames/assets/credentials or unrelated dirty work in publication.

## Working-state record

Started at `7d45c17a45cbc296a88694639468a35aa9333f12`. Pre-existing diff, untracked
file list and protected delivery hashes are retained in ignored local runtime
storage. Task branch: `codex/hayale-gcp-16x4-wnp`. Native Windows backend virtual
environment is the execution runtime; no model or dependency downloads are planned.
