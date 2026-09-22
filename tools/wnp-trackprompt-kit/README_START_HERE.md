# WNP_TrackPrompt_1.0.0

Read trackprompt/HANDOFF.md and trackprompt/prompts/START_CODEX.txt. Preserve incident run344ab3d2444c. Never blindly rerun Shot001.

Start with IMPLEMENTATION_GUIDE.md for exact Windows/WSL steps.

1. Run `python tools/verify_package.py`.
2. Run `python tools/install_package.py --target YOUR_EXISTING_REPO` for a dry run.
3. Add `--apply` only after the destination is correct. This stages files under
   `tools/wnp-trackprompt-kit/`; it does not modify existing app code or start jobs.
4. From the actual repo, follow the relevant agent handoff and acceptance gates.

The master/ directory is byte-identical across all three archives. VERSION=1.0.0.
Read BUILDINFO.json and VERIFICATION_REPORT.md for what was and was not tested.
No prompt eliminates error. Live application integration remains explicit work.
