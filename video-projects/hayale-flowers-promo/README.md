# Hayale Flowers — original-media V1

**Current preparation: [60-second Veo V2](v2/README.md).** Run
`RUN-HAYALE-PROMO-V2.cmd` yourself when ready. V2 includes the supplied song/logo,
a storefront reveal, two interior tours, three floral scenes and a reused logo
bookend. The V1 material below is a retained historical snapshot.

This is a standalone promo inside TrackPrompt. It has no connection to the
Hayale store/CMS, product catalogue, import contract or product-image pipeline.

## Edit

The authored `edit-decisions.json` binds selected asset IDs and SHA-256 hashes
to the immutable inspection inventory. It is the selects list and edit plan.

- **Master:** 30 seconds, 1080×1920, 30 fps, silent. One 3-second original still
  introduces the shop; 27 seconds of recorded video show orchids, roses, potted
  flowers, a mixed arrangement, floral detail, white flowers and a real ribbon
  label. The portrait delivery is an upscale of 480/576-pixel-wide phone footage;
  it cannot recover absent detail.
- **Website hero:** 15 seconds, 1920×1080, 30 fps, silent. Five original landscape
  photographs show the shop and floral details with centered crops and straight
  cuts. These are static holds, not AI animation or a seamless loop. Photographs
  preserve more useful landscape framing than heavily cropped portrait video.
- Original audio is omitted. The approved-audio folder contains only a placeholder.
- No enhanced stills, AI/provider calls, invented logo, customer portrait, or
  fabricated flower-arranging shot is used. No clear arranging action was selected.

All six video contact sheets and all 183 JPEG thumbnails were visually screened.
The 174 NEF files are retained RAW companions; RAW decoding and full-resolution
photographic focus assessment have not been performed. `shot-catalogue.md` records
selects and limitations; the private JSON catalogue covers every source asset.

## Existing infrastructure

`build_v1.py` is a project adapter to the existing resolved-timeline contract and
`app.video_generation.exporter.export_davinci_package`. The shared FCPXML 1.11,
FCP7 XML, EDL, CSV, relink and FFmpeg assembly code creates both packages.
The shared exporter now accepts `audioPath: null`, bounded still inputs and
opt-in centered source framing. Existing audio and framing defaults are retained.

The inspected `schemas/video-project.schema.json` describes Veo/ComfyUI generation
jobs and requires provider profiles, audio clocks and generation authorization.
This recorded-media edit deliberately uses the existing resolved-timeline layer;
it is not represented as a fake provider job and does not change that dirty schema.

## Run it yourself

Double-click **`RUN-HAYALE-PROMO-V1.cmd`** in the repository root. It runs in a
visible foreground window and creates a fresh `v1-<timestamp>-<id>` output folder.
No Codex monitoring, scheduled task, background worker, or provider is needed.
Keep the window open until the command completes; Ctrl+C interrupts the run.
Prior runs, including the initial unverified `v1/` attempt, are preserved.

For a check that does **not** create video, run:

```powershell
./run-hayale-promo-v1.ps1
```

The explicit PowerShell equivalent of the double-click launcher is:

```powershell
./run-hayale-promo-v1.ps1 -Render
```

To verify an existing run, use `-VerifyRun 'v1-<timestamp>-<id>'`.
If FFmpeg is absent from PATH, pass `-FfmpegPath` and `-FfprobePath` with installed
executable paths. No Administrator privileges are required by the launcher.

## Local files and advanced commands

Immutable input: `D:/HayaleFlowers/promo-2026-09-15/`.
All generated files live below
`.trackprompt-data/video-generation/hayale-flowers-promo/` inside this repository.
They are private, ignored runtime artifacts; never stage them.

Inspection requires an existing Python with Pillow. Build/verification uses the
repository backend venv. Supply installed FFmpeg/ffprobe paths; no tool, model
or dependency is downloaded.

```powershell
# One-time inspection. Refuses to overwrite an existing inspection baseline.
& '<Python-with-Pillow>' video-projects/hayale-flowers-promo/inspect_media.py `
  --ffmpeg '<installed-ffmpeg.exe>' --ffprobe '<installed-ffprobe.exe>'

# Plan only by default. Source selections are checked; no media is generated.
& backend/.venv/Scripts/python.exe video-projects/hayale-flowers-promo/build_v1.py `
  --run-id v1-manual

# Explicit render. Choose a new run ID; existing directories are never overwritten.
& backend/.venv/Scripts/python.exe video-projects/hayale-flowers-promo/build_v1.py `
  --render --run-id v1-manual --ffmpeg '<installed-ffmpeg.exe>' --ffprobe '<installed-ffprobe.exe>'

# Revalidate existing packages and rehash the complete source set.
& backend/.venv/Scripts/python.exe video-projects/hayale-flowers-promo/build_v1.py `
  --verify-only --run-id v1-manual --ffmpeg '<installed-ffmpeg.exe>' --ffprobe '<installed-ffprobe.exe>'
```

Each run's `master/` contains `hayale-promo-v1.fcpxml`, `.xml`, `.edl` and the reference
MP4. Its `hero/` contains the equivalent `hayale-hero-v1` files. Both also contain
`edit-plan.json`, `edit-sheet.csv`, `davinci-markers.csv`, `relink-map.csv`,
`derived-media/`, assembly instructions, coverage and verification reports.
`grade-plan.json` describes manual color work; FCPXML does not carry a final grade.
The initial `v1/` reference attempt failed decoded-frame verification because
JPEG full-range and video limited-range encodings differed. It is **not a delivery**.
The runnable builder now normalizes SDR preview encoding consistently; this repair
is covered by synthetic mixed-media tests. No subsequent Hayale render was started.

## Resolve review

1. Import the preferred FCPXML. Try FCP7 XML if the installed Resolve build rejects it;
   use EDL as the last fallback. Actual Resolve import has not been verified.
2. Keep each package in place. Relink derived event clips from its `derived-media/`;
   use `relink-map.csv` to return to the unchanged D: originals and source ranges.
3. Match the imported edit against the reference preview and markers.
4. Check the photographs' input color interpretation before grading. Selected
   JPEGs report EXIF ColorSpace 65535 with no embedded ICC; the input profile is
   unresolved. Phone videos explicitly report Rec.709 SDR. No HDR transform is applied.
5. Follow `grade-plan.json`, check handheld motion and crops in playback, confirm
   brand-label readability and approve the ending. Add only approved title/logo/music.

Technical verification covers exact frame counts, coverage, XML syntax/references,
decoded-frame correspondence between preview and edit events, and the complete
source file set/size/mtime/SHA-256. It does not establish final visual quality,
color accuracy, seamless looping, or Resolve import acceptance. Stop at V1 review;
V2/V3 require a separate decision.
