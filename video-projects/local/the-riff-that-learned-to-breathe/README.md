# The Riff That Learned to Breathe

A self-contained prompt and implementation package for a 2:29 local anime-style music video in TrackPrompt Studio.

## Working project path

Extract this folder to:

`C:\Users\theon\GitHub\TrackPrompt-Studio\video-projects\local\the-riff-that-learned-to-breathe`

The ZIP already contains the `the-riff-that-learned-to-breathe` top-level folder, so extract it inside `video-projects\local`.

## Creative premise

A complex progressive-metal MIDI idea is represented by a small mechanical origami bird. One fictional adult creator sends it to another. The second creator slows its pulse, gives it a downtempo body, and keeps the original amber mechanical heart. The two meet in a symbolic shared space and release the transformed melody into the city. The bird eventually returns, visibly changed but recognizably itself.

This is an original symbolic story. The Architect and the Listener are fictional adults, not likenesses of either collaborator.

## Model plan

- **Quality video tier:** Wan2.2-I2V-A14B through ComfyUI, using the highest GGUF quantization that passes a one-time bounded hardware qualification on the RTX 3060 12 GB. Candidate order: Q5_K_M, then Q4_K_M.
- **Reliable fallback:** Wan2.2-TI2V-5B with ComfyUI native offloading.
- **Keyframes:** FLUX.1-schnell in a low-memory ComfyUI profile; SDXL 1.0 is the ungated fallback.
- **Post:** local frame interpolation to 24 fps, anime-aware upscaling, then exact 1920×1080 delivery.
- **Final resolution:** 1080p. Generation is intentionally performed below native 1080p and finished through interpolation/upscaling because native A14B 1080p generation is not a realistic target for 12 GB VRAM.

No model weights are included. Setup must be explicit, license-aware, resumable, and never silent.

## One unavoidable input

Place the final track in `audio` as one of:

- `track.wav` — preferred
- `track.flac`
- `track.mp3`

The actual ffprobe duration is authoritative. The 149.000-second timing map in this package is provisional and must be proportionally adjusted, then snapped to musical boundaries detected by TrackPrompt Studio.

## Codex handoff

Open the repository root in Codex and give it:

`video-projects/local/the-riff-that-learned-to-breathe/CODEX_IMPLEMENTATION_PROMPT.md`

The prompt tells Codex to integrate this as a reusable local provider and to render only after the pipeline passes preflight. It also tells Codex to preserve the working tree and to report real validation results.

## Rights status

Nir Pache supplied the original MIDI composition/source idea. DJ WaZaHaKa created the downtempo remix, arrangement, production, and sound design. The collaborators have confirmed full creative permission for this project. The workflow must record that status once and must not repeatedly block on copyright or collaborator approval. Model-license checks, hardware checks, and output validation remain required.

## 16-scene structure

1. The Iron Seed
2. Send
3. Rain Room
4. Old Pulse
5. Turn the Tempo
6. Armor to Paper
7. New Drums
8. The Solo Becomes a Comet
9. Parallel Rooms
10. The Fracture
11. Keep the Heart
12. Bridge of Measures
13. Co-Write the Sky
14. The City Learns the Groove
15. Return Signal
16. Between Two Rooms

## Non-negotiable visual rules

- Original hand-drawn anime language; no named artist or studio imitation.
- One stable design for each fictional adult and for the origami bird.
- No text, subtitles, logos, watermarks, brand marks, DAW screens, or musical notation.
- Slow, readable camera language; subject movement may become energetic while the camera remains coherent.
- All audio stays local and is muxed after visual generation.
- Project-owned analyses, plans, prompts, seeds, and manifests are archived persistently. Temporary render cache may have TTL cleanup, but project analysis may only be removed by explicit project deletion.

## Package validation

From PowerShell:

```powershell
Set-Location "C:\Users\theon\GitHub\TrackPrompt-Studio\video-projects\local\the-riff-that-learned-to-breathe"
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\Validate-Package.ps1
```

The validator checks JSON syntax, all 16 scene IDs, continuous provisional timing, exact 149-second coverage, prompt presence, and required handoff files.
