# Andromeda V2 production foundation

This directory promotes the immutable R13.1 proof into a new, owner-attested
creative baseline without modifying or replacing any historical proof file.
The acceptance is creative scope only. It does not authorize a final render.
Codex records the owner-supplied attestation; Codex is not identified as the
human approver. The acceptance explicitly preserves technical QA, fresh scene
and profile checks, cloud-provisioning controls, and the final production gate.

The locked look profile is aspect-neutral. Horizontal and vertical outputs use
independently authored composition profiles; vertical is optional and disabled
by default. A crop of the horizontal output is never an acceptable vertical
composition. Every authored shot declares its camera rig and lens, three depth
layers, dominant shape, secondary action, lighting identity, cut intent,
bounded smoothed audio response, render complexity, and separate horizontal
and vertical framing overrides.

The R13.1 profile locks Blender 5.2 Eevee, 64 temporal and 32 volumetric
samples, temporal antialiasing/reprojection, AgX Medium High Contrast, DITHERED
transparency, motion blur off, at most two transparent layers, one localized
gate membrane, and no compositor denoising. It also locks independent
protagonist motion, authored camera lag/foreground parallax, and the prohibition
on raw-audio-driven major travel.

`production-authorization.json` is deliberately blocked. Production may start
only after the full source-audio animatic, representative human visual QA, V2
calibration, deterministic-effect and disk checks, the exact enabled-matrix
24-hour SLA, and a separate operator authorization all pass.

`package-manifest.json` hash-binds this directory to the versioned StoryPlan and
ShotPlan templates. Private source audio and cue files remain local and are
bound by digest rather than committed.

## Final V2 operator path

The horizontal production profile is
`render-profiles/trip-to-andromeda/andromeda-v2-horizontal-1080p-final.json`.
The wrapper below reuses the canonical resumable renderer and fails closed
unless the local scene/profile hashes, horizontal-only matrix, technical
readiness, and explicit operator start gate all agree:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  .\production\andromeda-v2\invoke-production.ps1 -Action Inspect

powershell -NoProfile -ExecutionPolicy Bypass -File `
  .\production\andromeda-v2\invoke-production.ps1 -Action Preflight

# Run only after the exact operator-start gate has been authorized.
powershell -NoProfile -ExecutionPolicy Bypass -File `
  .\production\andromeda-v2\invoke-production.ps1 `
  -Action StartOrResume `
  -AuthorizationToken "<exact scene/profile token>"
```

`StartOrResume` validates and skips already published frames, so the same
command safely resumes an interrupted job. `-EnableVertical` deliberately
fails until the optional authored vertical variant has its own calibration,
aggregate 24-hour forecast, enabled matrix, and exact operator authorization.
