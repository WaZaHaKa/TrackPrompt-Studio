# DaVinci Resolve handoff and autonomous assembly

## Export priority

The generated package contains project-blueprint filenames. `Static Into Signal` uses:

1. `trackprompt-timeline.fcpxml` — preferred editable FCPXML 1.11 exchange.
2. `trackprompt-timeline.xml` — FCP7 XML fallback commonly accepted by Resolve.
3. `trackprompt-timeline.edl` — conservative CMX3600 fallback.
4. `edit-plan.json`, `edit-sheet.csv`, and `davinci-markers.csv` — exact editorial decisions and chapter timing.
5. `relink-map.csv` — derived event media to immutable provider-clip mapping.
6. `coverage-report.json`, `render-manifest.json`, and `verification-report.json` — continuity, identity, hash, and media evidence.
7. `autonomous-preview-1080p.mp4` — baked H.264/AAC reference preview.

`The Glitch Is Me` retains its established `the-glitch-is-me-rough-cut.*` names through its project-owned blueprint. Never infer artifact names from a song inside shared exporter code.

DaVinci Resolve 20 documentation includes FCPXML 1.11 support. Import behavior can vary by installed Resolve build, media paths, or XML structure, which is why the package emits multiple fallbacks and an editor-independent preview.

Official codec reference:

```text
https://documents.blackmagicdesign.com/SupportNotes/DaVinci_Resolve_20_Supported_Codec_List.pdf
```

## Timeline contract

- 24 FPS non-drop, 1920×1080 Rec.709 SDR timeline by default.
- The verified song starts at 00:00:00:00 and is one continuous audio asset and the master clock.
- Generated provider assets contain video only and remain immutable.
- Every event has deterministic source in, retime, crop/reframe, opacity/composite, reversal/ping-pong/freeze treatment, duration, shot ID, and chapter ID.
- Chapter markers and the blueprint-validated edit-event range cover the complete song with no gap.

## Autonomous reference preview

FFmpeg renders one ordinary local media file per edit event under `derived-media`, preserving an editable timeline instead of flattening it. It applies only the selected project's validated deterministic treatments, then muxes the bound 48 kHz stereo master as the only audio source. `Static Into Signal` uses restrained 92–108% preview retimes, modest crops, straight cuts and no automatic reverse; more expressive adjustments remain manual Resolve work.

## Import into DaVinci Resolve

1. Keep the XML and its output directory in place so absolute paths resolve.
2. In Resolve, choose **File → Import Timeline → Import AAF, EDL, XML…**.
3. Select the package's preferred FCPXML (`trackprompt-timeline.fcpxml` for `Static Into Signal`).
4. If Resolve asks to relink, select the package's `derived-media` directory and consult `relink-map.csv`.
5. If that XML is rejected, import the package's FCP7 XML; use the EDL only as a last fallback.
6. Compare the imported timeline with the package's 1080p autonomous preview before making final touches.

The operator can move cuts, replace derived events from the immutable originals named in `relink-map.csv`, adjust transitions, remove effects, grade, add overlays/titles, and finish the edit manually.

## Final-only work

- Refine transitions and event treatments where desired.
- Color-match clips in the project's color-managed workflow.
- Follow the package handoff notes. For `Static Into Signal`, protect graphite/cold-blue separation, keep warning red rare, and introduce muted amber only near the ending.
- Add title, artist, and credits in Resolve rather than generated footage.
- Export the final Rec.709 master and delivery copy.
