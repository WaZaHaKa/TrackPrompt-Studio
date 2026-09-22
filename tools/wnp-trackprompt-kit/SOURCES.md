# Source and evidence ledger — 18 September 2026

## Project-supplied context (not fresh machine verification)
- User incident run: 344ab3d2444c; Windows TrackPrompt path; two 129-frame Shot001
  directories; raw-verified marker; Shot002 submission files; historical idle GPU
  sample. User reports recurrence. Marker contents and current process state remain
  unverified until local collection. Root cause is NOT established by the snapshot.
- Render Companion post-remediation audit supplied previously, dated 17 Sept2026:
  258 tests reported; Python wzhk_render_companion package; vanilla JS/SQLite/UI;
  SSH monitoring and specific remediations. This is historical evidence, not a
  claim that 258 tests were rerun for this package.
- Existing Companion working path from prior setup: ~/projects/opencode-experiments.
  GitHub lookup did not locate its repository. Confirm the local tree before editing.
- TrackPrompt GitHub main inspected via connected GitHub: commit
  e9b63b8c996ec718f6a6f80a6bb3eb59339f8fa2. Available branches did not expose the
  local Hayale V2.1 code. Do not overwrite local work with main.
- Contributor instructions inspected:
  https://github.com/WaZaHaKa/TrackPrompt-Studio/blob/e9b63b8c996ec718f6a6f80a6bb3eb59339f8fa2/AGENTS.md
  (blob d2eeb90bb62b0b376ff3f3987d1437efd9de2b30).

## Official external references consulted
- Ollama OpenCode integration: https://docs.ollama.com/integrations/opencode
- Ollama cloud authentication/model access: https://docs.ollama.com/cloud
- Requested builder: https://ollama.com/library/deepseek-v4.1-flash:cloud
- Requested reviewer: https://ollama.com/library/kimi-k3
- OpenCode current agents: https://opencode.ai/v2/docs/agents
- OpenCode current config: https://opencode.ai/v2/docs/config
- Legacy OpenCode agents: https://dev.opencode.ai/docs/agents/
- Legacy config: https://dev.opencode.ai/docs/config/
- Providers: https://opencode.ai/docs/providers
- ComfyUI queue/history/prompt routes:
  https://docs.comfy.org/development/comfyui-server/comms_routes
- OpenSSH configuration: https://man.openbsd.org/ssh_config
- SQLite transaction rationale: https://sqlite.org/atomiccommit.html

Installed versions, provider IDs, account/model access, current endpoint port and
credentials MUST be checked locally. The package discovers model IDs rather than
silently substituting a different model. Cloud inference can consume account
credits; no package script buys credits or enables a paid fallback.

## Design provenance
WNP is the custom protocol designed in this conversation, not an existing external
standard. SPEC_CLARIFICATIONS.md explicitly closes earlier ambiguities. The code in
these kits is a new reference implementation, not a patch claimed to have been
applied to the inaccessible local runner or Companion.
