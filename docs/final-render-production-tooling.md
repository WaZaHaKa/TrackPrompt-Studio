# Final-render production tooling

TrackPrompt's final-render workflow is deliberately separate from the bounded preview runner. These tools prepare, resume, encode, and verify a previously frozen Blender candidate; they do not rebuild a scene, regenerate cues, rerun analysis, delete prior frames, or overwrite media.

## Safety boundary

`render-trackprompt-final.ps1` has three modes:

- `-DryRun` validates the scene/profile hashes, scans any managed frame directory, and prints the missing contiguous chunks. It does not create the output directory or start Blender.
- `-Preflight` performs the same read-only plan plus Blender-version and output-drive checks. It does not render.
- With neither switch, the script requires the exact scene-specific token, acquires the one-GPU mutex, initializes or resumes a managed output directory, and runs one background Blender process per missing chunk.

The exact token is derived from both frozen scene and render-profile hashes and has this form:

```text
AUTHORIZE FULL RENDER: TRIP TO ANDROMEDA | SPACE-JOURNEY | <PROFILE-ID> | SCENE <SCENE-SHA12> | PROFILE <PROFILE-SHA12>
```

The match is exact, including spaces, punctuation, case, and both 12-character SHA-256 prefixes. Changing any profile byte changes the token and prevents reuse of an accepted manifest. Dry-run and preflight do not require a token. Supplying a token to either mode validates it without rendering.

No wrapper or helper accepts `-Force`, `-Yes`, a wildcard token, or a generic authorization file. The Blender chunk entrypoint also requires a managed render manifest whose scene/profile hashes agree and whose authorization state records acceptance by the outer script.

## Render-profile contract

The low-level renderer continues to accept the frozen schema `1.0.0` contract for backward compatibility. WZHK Mission Control's saved-profile builder emits schema `1.1.0`; it adds a stable profile ID, display metadata, nested source/timeline identity, pixel aspect, a configurable one-segment published-frame directory, dashboard preferences, per-output encoding enablement, and exact saved-file authorization binding. Required legacy production fields are illustrated below. Values are examples, not a profile recommendation.

```json
{
  "schemaVersion": "1.0.0",
  "project": "trip-to-andromeda",
  "preset": "space-journey",
  "profileId": "1440P-30-SDR",
  "blenderVersion": "5.2.0 LTS",
  "frameStart": 1,
  "frameEnd": 13029,
  "fps": 30,
  "resolution": {
    "width": 2560,
    "height": 1440,
    "percentage": 100
  },
  "imageSequence": {
    "format": "PNG",
    "extension": "png",
    "bitDepth": 16,
    "colorMode": "RGB",
    "compression": 15,
    "filenamePattern": "frame_######.png",
    "colorManagement": {
      "displayTransformBaked": true
    }
  },
  "approvedSceneSha256": "<64 HEX CHARACTERS>",
  "chunking": {
    "framesPerChunk": 300,
    "rationale": "Measured startup and checkpoint tradeoff."
  },
  "storage": {
    "plannedFrameSequenceGiB": 71.281,
    "projectedMasterGiB": 15.677,
    "projectedDeliveryGiB": 0.487,
    "supportReserveGiB": 2.0,
    "contingencyMultiplier": 1.5,
    "minimumLaunchFreeGiB": 140.0
  },
  "render": {
    "engine": "BLENDER_EEVEE",
    "samples": 64,
    "shadowPoolSize": "512",
    "motionBlur": false,
    "useCompositing": true,
    "filmTransparent": false,
    "ditherIntensity": 1.0
  },
  "colorManagement": {
    "displayDevice": "sRGB",
    "viewTransform": "AgX",
    "look": "AgX - Medium High Contrast",
    "exposure": 0.0,
    "gamma": 1.0,
    "sequencerColorSpace": "sRGB"
  },
  "audio": {
    "sha256": "<64 HEX CHARACTERS>",
    "sampleRate": 48000,
    "channels": 2
  },
  "encoding": {
    "master": {
      "container": "mov",
      "fileExtension": ".mov",
      "videoCodec": "prores_ks",
      "expectedVideoCodec": "prores",
      "profile": "3",
      "displayToDeliveryFilter": "colorspace=ispace=gbr:iprimaries=bt709:itrc=iec61966-2-1:irange=pc:space=bt709:primaries=bt709:trc=bt709:range=tv:format=yuv422p10,format=yuv422p10le",
      "pixelFormat": "yuv422p10le",
      "audioCodec": "pcm_s24le",
      "color": {
        "primaries": "bt709",
        "transfer": "bt709",
        "space": "bt709",
        "range": "tv"
      }
    },
    "delivery": {
      "container": "mp4",
      "fileExtension": ".mp4",
      "videoCodec": "libx264",
      "expectedVideoCodec": "h264",
      "profile": "high",
      "displayToDeliveryFilter": "colorspace=ispace=gbr:iprimaries=bt709:itrc=iec61966-2-1:irange=pc:space=bt709:primaries=bt709:trc=bt709:range=tv:format=yuv420p",
      "preset": "slow",
      "crf": 16,
      "pixelFormat": "yuv420p",
      "audioCodec": "aac",
      "audioBitrate": "320k",
      "color": {
        "primaries": "bt709",
        "transfer": "bt709",
        "space": "bt709",
        "range": "tv"
      }
    }
  }
}
```

Do not create schema `1.1.0` profiles by hand. Start from one of the Mission Control templates and use its 13-stage builder so the resulting profile is normalized, validated by both PowerShell and the authoritative Python renderer, written atomically, and accompanied by a human-readable summary. A saved profile remains unauthorized until its exact file SHA-256 and approved-scene SHA-256 pass both explicit production confirmations. Any later edit, rename, or duplicate invalidates that authorization identity.

For schema `1.1.0`, `resolution.pixelAspectX`, `resolution.pixelAspectY`, and `output.framesSubdirectory` are part of the render-manifest frame contract. `production.resumeEnabled`, `production.verifyExistingFrames`, `production.atomicChunkCommit`, and `production.stopOnValidationFailure` must remain true, while `production.overwriteValidFrames` must remain false. `output.directoryPattern` accepts only safe literals and `{project}`, `{preset}`, `{resolution}`, `{profile}`, and `{timestamp}` tokens; `{timestamp}` is mandatory and the expansion must remain one safe directory name. Encoding is disabled unless an exact approved audio SHA-256 and matching full-timeline clock are bound; OpenEXR profiles may render with both encode outputs disabled.

`frame_%06d.png` is accepted as the equivalent Python/FFmpeg pattern. Chunk size must be backed by a written rationale; an override may shrink but never enlarge the reviewed profile chunk size. PNG output may be 8- or 16-bit at the tooling layer so tiny fixtures remain testable; the approved production profile decides the actual bit depth. Every PNG output profile must provide a bounded `displayToDeliveryFilter`. The selected filters perform the measured sRGB/full-range RGB to Rec.709/limited-range YUV conversion for the exact output pixel format; retagging alone is not accepted. OpenEXR must be RGB half-float with ZIP or PIZ compression, and its encoding profiles must document an explicit `linearToDeliveryFilter`. Reviewed libx264 output requires `profile: high`; `color.range: tv` emits `-color_range tv` and is verified from ffprobe.

The storage policy is profile-hash-bound. A new production output requires at least the profile's `minimumLaunchFreeGiB` before initialization. Immediately before each chunk—and before an inflight directory or Blender process is created—the wrapper refreshes `IO.DriveInfo.AvailableFreeSpace` and requires the remaining projected frame sequence plus master, delivery, and support reserve under the reviewed contingency multiplier. Lookup failures and insufficient space fail closed; a resume uses the smaller remaining-frame requirement.

## Resume and atomic publication

The managed production directory contains the profile's safe `output.framesSubdirectory` (default `frames`) plus `logs`, `checkpoints`, `manifests`, `master`, `delivery`, and `qa`. A non-empty directory without the matching managed manifest is rejected.

Each Blender process writes only into a new `.inflight-*` directory under `checkpoints`. The Python core validates every output name, PNG CRC or OpenEXR structure, dimensions, bit depth, and SHA-256. It then creates each destination frame as a same-volume hard link with create-new semantics and unlinks the temporary name. Existing destination frames are never replaced. An interrupted publication leaves only valid published frames plus bounded inflight residue; the next dry-run rescans actual files and replans only the missing contiguous ranges.

Future renderer processes also honor an identity-bound stop marker under the managed output's `control` directory. The marker is checked only before a new chunk and after successful validation/publication. A clean operator stop records `stopped-after-current-chunk-by-operator`; it never deletes or replaces a frame. Renderer processes that were already running when this support was installed do not dynamically reload the script and therefore cannot observe the new marker.

Valid published frames are never replaced. When `production.overwriteInvalidFrames` is false, corrupt, zero-byte, wrong-dimension, or out-of-range canonical frames block rendering. When it is true, an authorized production run first rechecks storage, scene/profile identity, the exact authorization token, the matching authorized render manifest, and managed-directory containment; it then atomically moves only invalid canonical files into a recoverable `checkpoints/quarantine-invalid-*` directory, writes a hash-bound quarantine manifest/history entry, and replans those frames as missing. Duplicate, noncanonical, symlinked, or escaped files always fail closed before any move. Dry-run and preflight never invoke quarantine.

The canonical resumable sequence is deliberately opaque RGB with six-digit `frame_%06d` names and 100% resolved output. Transparent-film/RGBA output is not advertised as supported: schema 1.1 validation fixes `render.filmTransparent` false so the saved setting cannot become a no-op. Blender 5.2 high-quality normals are likewise fixed false because the reviewed API exposes no matching setting.

## Separate encoding and verification

`encode-trackprompt-final.ps1` refuses to start FFmpeg until:

- all expected frames validate and the count is exact;
- frame 1 and frame 13029 are present through the complete range contract;
- the frame-set digest agrees with the complete render manifest;
- scene, profile, and audio SHA-256 values agree;
- audio duration is within one video frame of the sequence clock;
- the destination is new and contained in the selected `master` or `delivery` directory.

The FFmpeg command maps video and audio explicitly, fixes the exact video frame count, omits `-shortest`, never normalizes audio, uses `-n`, and preserves the frame sequence. Output is written to a new `.partial-*` name. The Python core revalidates the approved audio after FFmpeg, counts decoded video frames, probes the temporary file, checks the reviewed codec/profile/resolution/FPS/duration/audio/range/color contract, hashes it, atomically renames it, and writes an encode manifest. That manifest binds the render frame-set digest, approved-audio hash/size/duration, exact video-frame count, and `shortestAllowed: false` clock policy.

`verify-trackprompt-final.ps1` requires the original `-AudioPath`, independently re-hashes and probes both approved audio and completed media, counts decoded video frames, checks nontruncation plus render/encode-manifest agreement and temporary residue, and writes a new QA report. Its extraction-frame plan is marked `pending-human-review`; structural verification never claims that final visual QA has happened.

## Timeline health scan

`blender/timeline_health_scan.py` evaluates the dependency graph without calling Blender's render or save operators. It samples every 30 frames, first/final frames, section and transition boundaries, the six representative stills, preview edges/center, and spaced high-energy peaks. Reports are written atomically as JSON and Markdown. Any blocking dependency, driver, finite-value, camera, foreground-blocking, compositor, audio-bus, frame-range, or FPS issue produces `NOT READY` and a nonzero exit.

Always launch it with Blender's `--python-exit-code 1` so an unexpected Python exception cannot be mistaken for a successful scan.
