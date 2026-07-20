# Render profiles

Saved profile JSON is the production source of truth. A valid profile resolves
actual Blender, image-sequence, color, compositor, storage, encoding, chunking,
and recovery values; labels such as `FAST` or `ULTRA` are not sufficient.

Mission Control stores profiles beneath `render-profiles/<project>/`.
`recommended-profile.json` is a hash-bound pointer, not a render profile.
Profile discovery excludes it and resolves it only when its target remains in
the same project directory and the target profile, scene hash, profile ID, and
saved-file SHA-256 all match.

Current profile roles are:

- native 720p Hyper Optimized;
- calibrated machine recommendation;
- calibrated 1080p release option;
- calibrated 1440p balanced option;
- native 4K balanced optimized option;
- existing 4K Ultra comparison, which must not be overwritten.

The word `calibrated` is valid only when the profile contains measured evidence
for the exact machine and frozen scene. A generated template or an imported
provider estimate is not calibrated evidence.

Profile changes always require fresh authorization. Output created by another
profile hash cannot be resumed. Profile summaries are convenience views; the
exact JSON file and its SHA-256 remain authoritative.

## Exact native 720p workflow

From the repository root, run:

```powershell
.\WZHK-Media-Launcher.cmd
```

```text
CALIBRATE THIS PC
-> run and visually review native 1280x720 candidates
-> GENERATE 720P HYPER PROFILE
-> CREATE / EDIT PROFILE -> REVIEW
-> AUTHORIZE exact scene/profile hashes
-> LOCAL RENDER -> FINAL RENDER PREFLIGHT
-> LOCAL RENDER -> DRY-RUN / RESUME PLAN
-> LOCAL RENDER -> START / RESUME RENDER
```

The 720p profile remains 1280x720, square pixel, 30 fps, frames 1-13029,
EEVEE, and a resumable image sequence. It is never a renamed upscale.

Read-only profile inspection is also available from PowerShell:

```powershell
.\WZHK-Media-Launcher.cmd -ListProfiles
.\WZHK-Media-Launcher.cmd -ValidateProfile `
  -ProfilePath ".\render-profiles\trip-to-andromeda\trip-to-andromeda-720p-hyper-optimized.json"
```

The interactive `-RenderProfile` entry point still requires exact saved-profile
authorization and both render confirmations. Merely validating or listing a
profile never starts Blender.
