# Validation report — TrackPrompt GCP Video Fast Lane starter v0.1.0

**Validation date:** 2026-08-13  
**Target repository:** `C:\Users\theon\GitHub\TrackPrompt-Studio`  
**Package type:** additive Codex implementation starter; no credentials, audio, generated footage, or paid cloud execution.

## Result

The source tree passed its focused offline validation before archive creation.

| Check | Result |
|---|---:|
| Focused Python tests | **12 passed** |
| JSON files parsed | **39 passed** |
| Python files parsed with `ast` | **18 passed** |
| Shot bank | **16 ordered shots** |
| Plain-text shot prompts | **16 present** |
| Provider request examples | **16 present** |
| Project profiles compiled | **4 passed** |
| Synthetic FFmpeg assembly | **passed** |
| Packaged secrets/private media scan | **passed** |
| Python caches/package clutter | **absent** |

## Compiled profile evidence

| Configuration | Model/output | Shots | Base estimate | Maximum cap | Plan digest |
|---|---|---:|---:|---:|---|
| `project-config.json` | Veo 3.1 Fast / 1080p | 16 | $12.80 | $24.00 | `45eb57209615b899504e6b316ad09c3e5067d04c00c77d83676fe788756e1db9` |
| `project-config.quality-1080p.json` | Veo 3.1 standard / 1080p | 16 | $25.60 | $45.00 | `99252a975c53c01f580b3ed8b18b6e4473967f6ed20ca105697a88858de76025` |
| `project-config.4k-optional.json` | Veo 3.1 standard / 4K | 16 | $51.20 | $80.00 | `d941aadd967f812c721f5dc8f9fa10dbd1cdf46d1648f1ea7cfcdc08b78e6430` |
| `project-config.smoke.json` | Veo 3.1 Fast / 720p, one shot | 1 | $0.32 | $1.00 | `805913cf8a4dce7a9cbb7296c22d5af4a32a639bd116eff1f333f9027bc047cf` |

The ordinary one-command live workflow deliberately uses `shot-001` from the exact selected full plan as the smoke request, so the same plan digest and one batch authorization continue to cover the remaining shots.

## Synthetic autonomous assembly proof

The real `ffmpeg` executable was used to:

1. create two deterministic one-second, video-only H.264 fixtures;
2. normalize and concatenate them through the package's actual assembly command builder;
3. mux a local two-second 48 kHz WAV master;
4. probe the resulting MP4.

Verified result:

```text
commands:        4
video:           present
local audio:     present
resolution:      320x180 synthetic fixture
frame rate:      24.0 FPS
duration:        2.0 seconds
status:          passed
```

This verifies the autonomous assembly mechanism, not the artistic quality of future Veo generations.

## Exact offline commands used

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend \
  python -m pytest -q -p no:cacheprovider \
  backend/tests/video_generation/test_video_generation_fastlane.py

python tools/validate_gcp_video_fastlane_bundle.py
```

Additional packaging checks parse every JSON file, parse every Python file, verify every entry in `PACKAGE-MANIFEST.json`, scan for forbidden private-media/credential suffixes, and re-run validation from a fresh archive extraction.

## Not executed in this environment

PowerShell 7 was not available in the Linux packaging container. The `.ps1` workflows were statically reviewed but not executed here. The Codex task explicitly requires their real Windows execution and the repository's full authoritative test/build/launcher suite before the feature is called integrated or shipped.

No GCP credential was present and no Vertex AI request was submitted. Live Veo access, project quota, bucket permissions, generated-shot quality, and DaVinci import behavior remain runtime checks for Codex on the target Windows machine.
