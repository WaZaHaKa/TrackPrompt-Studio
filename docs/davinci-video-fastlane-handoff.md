# DaVinci Resolve handoff and autonomous assembly

## Export priority

The generated package contains:

1. `trackprompt-timeline.fcpxml` — preferred modern exchange.
2. `trackprompt-timeline.xml` — FCP7 XML fallback commonly accepted by Resolve.
3. `trackprompt-timeline.edl` — conservative straight-cut fallback.
4. `edit-sheet.csv` — human-readable exact edit decisions.
5. `davinci-markers.csv` — chapter timing.
6. `ASSEMBLE-PREVIEW.ps1` — autonomous local FFmpeg assembly.
7. `assembly-plan.json` — exact commands without executing them.

DaVinci Resolve 20 documentation includes FCPXML 1.11 support. Import behavior can still vary by installed Resolve build, media path, or XML structure, which is why the package emits multiple fallbacks and an editor-independent preview.

Official reference used for this package:

```text
https://documents.blackmagicdesign.com/SupportNotes/DaVinci_Resolve_20_Supported_Codec_List.pdf
```

## Timeline contract

- 24 FPS non-drop timeline.
- Original song starts at 00:00:00:00 and remains the master clock.
- Generated assets contain video only.
- Every segment has deterministic source in, duration, shot ID, and chapter ID.
- Chapter markers cover the complete song.
- 1080p default: 1920×1080.
- Optional 4K: 3840×2160.

## Autonomous preview

The FFmpeg path normalizes each edit segment to the configured dimensions and 24 FPS, concatenates them, then muxes the original audio locally as AAC 320 kb/s. It prioritizes reliability and immediate review over sophisticated transitions.

## Final touches in Resolve

Recommended final-only work:

- replace straight chapter cuts with the intended restrained dissolve/glitch language;
- color-match generated clips in DaVinci Wide Gamut or the project's established color-managed workflow;
- protect wet-glass shadow detail and keep amber accents rare;
- add title/artist/credits in Resolve, never inside generated footage;
- use optical flow, reverse, speed ramps, crops, and composites only after visual review;
- export the final Rec.709 master and YouTube delivery.
