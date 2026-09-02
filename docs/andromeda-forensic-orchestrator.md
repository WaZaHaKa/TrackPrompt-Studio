# Andromeda V2 forensic orchestrator

This package answers one question before any expensive work begins:

> Which exact local release, scene, profile, evidence set, private source files, and authorization state are actually present—and what precisely still blocks rendering?

It is designed for the situation where the repository appears ready but the production helper fails closed.

## What the audit does

The audit is read-only. It:

- ignores `.pytest*`, `pytest-*`, synthetic fixture, cache, virtual-environment, and `node_modules` trees;
- discovers every real `package-manifest-v2.json` candidate;
- validates the required sibling calibration, technical authorization, and release report;
- recursively checks every discoverable `path` + `sha256` (+ optional `sizeBytes`) reference;
- checks the tracked release hold against candidate identities;
- reports human visual-QA approval and human closure bindings separately;
- extracts the enabled output matrix and aggregate P90 forecast;
- discovers the newest real Andromeda `.blend` scenes, profiles, and proof roots;
- searches bounded known locations for the exact private audio and `visual-cues.json` hashes;
- records Git state, executables, relevant processes, and free disk;
- introspects available Pydantic release/human-approval model schemas from the local backend;
- writes JSON and Markdown reports;
- creates a small `andromeda-forensic-upload.zip` containing the report and relevant JSON evidence only.

The upload ZIP intentionally excludes audio, `.blend` scenes, frames, previews, model weights, and final media.

## Install

Extract the package into the root of:

```text
C:\Users\theon\GitHub\TrackPrompt-Studio
```

The resulting paths are:

```text
tools\andromeda_forensic_audit.py
tools\Invoke-AndromedaForensicOrchestrator.ps1
RUN-ANDROMEDA-FORENSIC-AUDIT.cmd
RUN-ANDROMEDA-FORENSIC-PREFLIGHT.cmd
RUN-ANDROMEDA-FORENSIC-START.cmd
```

## First action

Double-click:

```text
RUN-ANDROMEDA-FORENSIC-AUDIT.cmd
```

Or run:

```powershell
Set-Location "C:\Users\theon\GitHub\TrackPrompt-Studio"

powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\tools\Invoke-AndromedaForensicOrchestrator.ps1 `
  -Mode Audit `
  -OpenReportFolder
```

The output directory is:

```text
test-output\andromeda-forensic-YYYYMMDD-HHMMSS\
```

Upload:

```text
andromeda-forensic-upload.zip
```

That bundle should contain enough exact local evidence to determine whether the remaining work is only:

- a human approval/closure record;
- an aggregate forecast refresh;
- a stale package identity;
- a missing scene/profile binding;
- private source-file discovery;
- or an actual incomplete final release.

## Safe existing preflight

Double-click:

```text
RUN-ANDROMEDA-FORENSIC-PREFLIGHT.cmd
```

This runs the audit, then invokes the existing guarded preflight against the exact selected package and incorporates the result into the forensic report. It does not start Blender rendering or FFmpeg encoding.

## Start mode

`RUN-ANDROMEDA-FORENSIC-START.cmd` remains fail-closed. It starts only when all of the following are true:

- a real coherent package is selected;
- all bound path/hash references validate;
- `technicalReady` is true;
- the exact aggregate total P90 exists and is at most 86,400 seconds;
- required horizontal output is enabled;
- the tracked release hold does not bind the release;
- human visual-QA approval and later human review closure are both valid;
- exact source audio and cue hashes match local files;
- the operator types the exact package-bound start phrase.

It then opens Mission Control and delegates to the existing `Invoke-AndromedaLatestProduction.ps1` helper, preserving the real-frame dashboard, resumable rendering, encoding progress, and final QA lifecycle.

## Optional source paths

Explicit paths reduce search time:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\tools\Invoke-AndromedaForensicOrchestrator.ps1 `
  -Mode Audit `
  -SourceAudioPath "C:\Users\theon\OneDrive\Desktop\Gratis Project\DJ WaZaHaKa - Trip to Andromeda.wav" `
  -SourceCuePath "C:\path\to\final\visual-cues.json" `
  -OpenReportFolder
```

Additional bounded search roots can be supplied with repeated `-SourceSearchRoot` values.

## Important boundary

The tool does not delete or edit `release-hold.json`, fabricate human approval, rewrite immutable manifests, copy old tokens, lower quality, or invoke low-level render workers directly. A script that did those things could start the wrong scene or create a non-resumable output that Mission Control cannot trust.
