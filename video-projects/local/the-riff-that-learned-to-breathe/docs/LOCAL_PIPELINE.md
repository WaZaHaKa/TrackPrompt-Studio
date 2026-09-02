# Local pipeline

## 1. Audio binding

TrackPrompt Studio reads `audio/track.*`, validates it with ffprobe, computes SHA-256, and creates a durable project revision. The audio remains local.

## 2. Persistent analysis

Run the existing local analysis stack to obtain duration, beats, downbeats, phrases, sections, and energy. Scale the provisional 149-second map to the exact audio length and snap boundaries according to `timing/sync-policy.json`. Archive the result under the project revision; do not leave the video workflow dependent on an expiring analysis job.

## 3. Reference sheets

Use the prompts in `prompts/character-reference-prompts.md` to produce one deterministic reference sheet for the Architect, Listener, and bird. Store selected sheets by content hash.

## 4. Keyframes

Generate one 1024×576 keyframe per scene with the selected still-image model. The full static scene description is in `shot-bank.json`. Run automated checks for dimensions, decode integrity, duplicate outputs, obvious text artifacts, and reference similarity. Produce a contact sheet without blocking the run for another rights approval.

## 5. Wan2.2 I2V clips

For each scene, use the approved keyframe and the short motion prompt. Generate 81 frames at 16 fps. Keep one continuous action per clip. Save every frame, preview MP4, exact seed, workflow JSON, ComfyUI prompt ID, model hashes, and timing.

## 6. Post

Interpolate to 24 fps, upscale at least 2× with an anime-aware model, and downscale to exact 1920×1080. Apply subtle final grain only after frame validation.

## 7. Edit

Use `edit-blueprint.json` and the snapped timeline. Extend the 5.0625-second generated core with 2.5D parallax, restrained optical-flow slowdown, held poses, and motif transitions. Do not visibly repeat entire clips. Mux the original audio and export Resolve interchange files.

## 8. QC

The final MP4 is complete only after:

- ffprobe reports 1920×1080, 24 fps, expected duration, and audio.
- Duration matches the source audio within one output frame.
- No stale output predates its required inputs.
- No missing, black, frozen, corrupt, or unintended duplicate frame run exceeds configured thresholds.
- All 16 scene IDs appear in the edit manifest.
- Prompt, seed, model, workflow, and file hashes are recorded.
- The final file decodes from beginning to end.
