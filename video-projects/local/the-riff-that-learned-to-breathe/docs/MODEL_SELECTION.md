# Model selection

## Decision

The quality target is **Wan2.2-I2V-A14B**, run locally through ComfyUI with low-memory GGUF loading. The machine qualification tries Q5_K_M first, then Q4_K_M. The first tier that completes a bounded probe safely becomes the cached project profile. **Wan2.2-TI2V-5B** is the reliable fallback.

This is a keyframe-led image-to-video design. It is intentionally not a pure text-to-video workflow because stable fictional characters, a stable origami bird, and a defined 16-scene narrative matter more than random novelty.

## Why not native 1080p generation

The final deliverable is 1920×1080, but the working generation size is 1024×576. The pipeline interpolates and upscales locally, then performs a high-quality downscale to exact 1080p. This concentrates the 12 GB VRAM budget on motion and consistency rather than attempting an impractical native-1080p diffusion pass.

## Keyframe model

FLUX.1-schnell is the preferred still-image model because it has a permissive Apache-2.0 license and strong prompt following. It may require one-time official model access acceptance. SDXL 1.0 is the fallback. No community anime checkpoint or LoRA is enabled automatically; every optional adapter needs a recorded source, checksum, and compatible license.

## Quality versus speed

- Four-step acceleration is for preflight and previews.
- Final A14B clips use a fuller sampling profile imported from the current supported ComfyUI workflow and tuned through a small deterministic test.
- Only one video generation runs at a time.
- Successful clips are never regenerated merely because another clip failed.
- Four hero scenes may receive one alternate each; all other scenes have one base generation.
