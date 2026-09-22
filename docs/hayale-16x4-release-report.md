# Hayale 16x4 implementation acceptance — 2026-09-22

This is software and synthetic-delivery acceptance. No paid film was rendered,
no selected or unselected private media was uploaded, no cloud IAM/infrastructure
was changed, and no local GPU generation was started.

## Evidence by boundary

| Boundary | Evidence / remaining prerequisite |
| --- | --- |
| Scenario and implementation | Versioned 16 x 96-frame edit, local customer/logo composition, four explicit profiles, selected asset/consent hashes, exact-batch admission and canonical Mission Control attempts |
| GCP authentication tooling | Native PowerShell 5.1/7 parsing and fake-CLI execution passed; auth/drain suite 53 passed, 1 inapplicable batch-launcher case skipped; actual Local check reports no private TrackPrompt credential binding |
| Actual cloud access | Unverified; intended account, video project and private bucket are discovered only as unadopted local hints. Operator must use explicit Reauth and Cloud checks |
| Companion connectivity | Local node identity/stream inspected and frozen host integration tested. Expected loopback/tunnel ports were unreachable even outside the sandbox. The actual Companion source/client was unavailable; no live UI acceptance claimed |
| Provider generation | Not run. Provider behavior tested through deterministic mocks/contracts, crash injection and retained-output recovery |
| Technical delivery | Real synthetic 1080p MP4: 64.0 seconds, 1,536 frames, 24 fps, stereo 48 kHz music, full decode and A/V alignment checks passed; original synthetic soundtrack bytes preserved |
| Creative acceptance | Pending. Actual three customer photos, approved artwork/shop selections, explicit 64-second original-music arrangement and human review are still required |
| Historical preservation | Previous completed MP4, soundtrack, provenance and verification report all retain their pre-session SHA-256 values; existing run `344ab3d2444c` was not resumed or rewritten |

## Repository checks

Checks ran against a clean publication worktree, using the existing Windows test
environment and installed Gyan FFmpeg 8.1.1. Operator commands additionally ran
with the installed stable Python 3.12.10. No model/dependency download was used.

- Frontend: **99 tests passed**; lint, typecheck and production build passed.
- Required browser suite: **2 passed**, with isolated loopback ports and generated
  synthetic audio. Additional Mission Control E2E could not start because its
  private calibrated 720p regression profile is absent in a clean checkout.
- Backend Ruff passed; mypy passed for **172 source files**.
- `docker compose config -q` passed; no containers were started.
- Full backend run: **989 passed, 5 failed, 7 skipped**. Four failures exposed a
  required existing local lease dependency; that dependency was extracted from
  the unpublished experimental module without publishing the experiment. The
  affected Hayale group then passed **56 tests**, with one opt-in legacy finishing
  test skipped. The remaining unrelated failure is
  `test_cli_resolves_relative_release_inputs_from_repository_root`: the historical
  Andromeda release manifest points outside this isolated checkout. Its immutable
  manifest and assertion were not weakened or rewritten.
- Final integrated WNP/provider/scenario/operator regressions: **76 passed**, one
  optional `jsonschema` test skipped. The frozen host's own validation, ordered
  replay, competing-writer behavior and master-byte manifest checks passed.
- Independent final review found and fixed retained-worker ownership during
  ordinary backend startup. The final ownership/provider/prepared-plan regression
  run passed **58 tests** with native media tools; Ruff and mypy passed again.
- Seven full-suite skips cover the inapplicable auth launcher case, three private
  render fixtures, optional `jsonschema`, a Windows symlink capability and the
  explicitly gated legacy finishing test. The new real 64-second finishing test
  ran and passed.

The first clean-worktree run also lacked generated audio fixtures. The repository
generator produced 61 synthetic fixtures before the full rerun. Unchanged
hash-bound fixture JSON was read with its exact Git blob bytes after Windows
checkout line-ending conversion; no production artifact contract was relaxed.

## Publication scope

The original checkout contained substantial unrelated dirty work and fifteen
unpublished predecessor commits. It was preserved. The publication candidate is
based on verified remote `main` (`e9b63b8c996ec718f6a6f80a6bb3eb59339f8fa2`) and
carries the required current Hayale recovery/WNP source plus this feature.
Historical raw reports, private media, credentials, journals, runtime databases,
production outputs and unrelated experimental work are excluded from new commits.
The frozen WNP master bytes remain intact. Existing already-remote historical
artifacts are unchanged; newly selected publication files contain no media,
credential signatures or files larger than 1 MB.

Publication branch: `codex/hayale-gcp-16x4-wnp-reviewed`. The original working branch
and clean review worktree both remain available. Final local/remote commit identity
and PR URL are reported after publication; this report does not claim a future push.

## Operator handoff

Use [the runbook](hayale-16x4-runbook.md) for selection, preparation, one bounded
authorization, supervised start/resume, safe local stop and verified export.
Start with `./run-hayale-16x4.ps1 -Action Check`. Google reauthentication is a
separate explicit operator action and never resumes rendering automatically.
