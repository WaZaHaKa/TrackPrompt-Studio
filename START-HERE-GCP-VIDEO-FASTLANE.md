# Start here — TrackPrompt GCP Video Fast Lane

This archive is aligned to the root of:

```text
C:\Users\theon\GitHub\TrackPrompt-Studio
```

Extract it directly into that repository. Every path in the archive is additive. The package does not replace existing TrackPrompt analysis, Blender presets, Mission Control state, or production-render evidence.

## What this starter provides

- A provider-neutral, versioned video-plan contract.
- A raw Vertex AI Veo 3.1 long-running-operation adapter using the active `gcloud` identity.
- One exact project-level maximum-spend authorization instead of authorization per shot.
- Persistent operation receipts, polling, resume-friendly state, GCS download, and `ffprobe` verification.
- A complete 16-shot content pack for **The Glitch Is Me**.
- Three delivery profiles:
  - default: Veo 3.1 Fast, 1920×1080, 8 seconds per shot;
  - optional: standard Veo 3.1, 1920×1080;
  - optional: standard Veo 3.1, 3840×2160.
- A deterministic timeline resolver that uses the original song as the clock and snaps chapter boundaries to an existing TrackPrompt ShotPlan when available.
- FCPXML 1.11, FCP7 XML, CMX3600 EDL, edit-sheet CSV, and marker CSV exports.
- An FFmpeg-based autonomous first assembly with the original audio muxed locally.
- Focused tests and a Windows verification command.

## First action

Open Codex in the repository and paste the complete task from:

```text
CODEX-GCP-VIDEO-FASTLANE-PROMPT.md
```

Codex must inspect and adapt the starter to the actual current repository rather than blindly overwriting established services.

## Safe local validation

From PowerShell 7:

```powershell
Set-Location "C:\Users\theon\GitHub\TrackPrompt-Studio"
.\tools\VERIFY-GCP-VIDEO-FASTLANE.ps1
```

This does not contact GCP.

## GCP setup preview

```powershell
.\tools\SETUP-GCP-VIDEO-FASTLANE.ps1 `
  -GcpProjectId "YOUR_GCP_PROJECT_ID" `
  -BucketName "YOUR_GLOBALLY_UNIQUE_BUCKET"
```

It prints the bounded setup commands without applying them. Run again with `-Apply` only after reviewing the project and bucket names.

## Prepare the exact project without spending money

```powershell
.\tools\RUN-GCP-VIDEO-FASTLANE.ps1 `
  -GcpProjectId "YOUR_GCP_PROJECT_ID" `
  -GcsBucket "YOUR_GLOBALLY_UNIQUE_BUCKET" `
  -AudioPath "C:\path\to\The Glitch Is Me.wav" `
  -Profile fast-1080p
```

This compiles the full plan, calculates the current package estimate, runs the GCP doctor, and writes all sanitized provider requests. It submits nothing.

After review, run the same command with `-Generate`. The workflow asks for one exact batch-level spending phrase, generates `shot-001` first as a same-plan smoke request, verifies it, resumes the other 15 shots, downloads and verifies all clips, creates the Resolve handoff, and prepares/runs the autonomous FFmpeg assembly.

## Default output

Generated runtime files remain under:

```text
.trackprompt-data\video-generation\the-glitch-is-me\
```

The key handoff directory becomes:

```text
.trackprompt-data\video-generation\the-glitch-is-me\davinci\fast-1080p\
```

Import `trackprompt-timeline.fcpxml` into DaVinci Resolve. If Resolve rejects it, import `trackprompt-timeline.xml`. The autonomous preview remains available even if no XML format imports perfectly on the installed Resolve build.
