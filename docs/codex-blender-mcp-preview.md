# Codex and Blender MCP preview workflow

For story-driven V2 authoring, use the high-level `build_story_scene`, `set_review_shot`, `validate_current_shot`, `capture_review_state`, and `save_revision_snapshot` entrypoints. The V2 MCP loop is documented in [Cinematic Visualizer V2](cinematic-visualizer-v2.md). MCP is restricted to interactive authoring and bounded preview evidence; it must not start the production renderer.

Use a configured Blender MCP server as a narrow orchestration layer around the
repository's Blender Python package. MCP should make a few bounded calls; the
package itself validates and imports all curve points. Tool names vary by MCP
server, so discover capabilities instead of hardcoding names.

For a complete upload-to-artifact run on Windows, use
`run-trackprompt-to-blender.ps1`; it is the canonical path because one run
manifest binds the upload, job, exports, `.blend`, and verified preview. Blender
MCP and the completed-job/manual builder are optional targeted paths for scene
inspection, bounded revisions, or recovery after a job has already completed.

## Operator workflow

1. List the available Blender MCP tools. Identify the tools that can execute
   Blender Python, inspect the scene, save a `.blend`, render a frame, and return
   execution errors.
2. Query and confirm the Blender version.
3. Open a clean scene or an operator-approved template. Do not delete or
   overwrite the user's existing file.
4. Add the repository's absolute `blender/` directory to Blender's Python path.
5. Import `trackprompt_visualizer.mcp_entrypoints` and call `build_scene` with
   the selected registered preset. Space Journey revisions supply only a
   validated configuration path or bounded parameter dictionary; do not expose
   arbitrary Python or individual-keyframe mutation.
6. Inspect the structured result and call `scene_summary()`. Confirm
   `TP_AUDIO_BUS`, the required TrackPrompt collections, active camera, expected
   FPS/range, audio strip, F-curves, preset, and seed.
7. Call `render_preview_stills(approved_output_directory)` and inspect each
   role-labelled image. Make no more than two bounded preset-parameter revision
   passes, rebuilding to a new approved `.blend` and retaining its revision
   manifest each time.
8. Call `render_preview_clip(approved_output_path)` for the planned segment: 10
   seconds for a source at least that long, or the complete shorter source. This
   narrow MCP function reports the requested audio policy but does
   not start ffprobe, so its mux status remains `requested-unverified`. Validate
   movie duration and actual streams with the documented headless preview CLI
   before claiming a muxed artifact. If the Blender movie encoder is unavailable,
   keep the structured failure and use that CLI with its explicit FFmpeg fallback.
9. Call `save_scene(new_approved_path)` and report every output and error.

The available narrow functions are:

```python
build_scene(
    cue_path,
    audio_path,
    output_blend,
    preset="abstract-geometry",
    seed=84291,
    config_path=None,
    parameters=None,
)
scene_summary()
render_preview_stills(output_directory)
render_preview_clip(output_path)
save_scene(path)
```

Each validates caller-provided inputs/outputs, returns a dictionary with
structured success or failure data, and performs no shell execution. Only
`build_scene` may clear and construct a scene, and it does so only after cue,
audio, output, preset, seed, and complete configuration validation. Output
parents must already exist, preventing an MCP call from creating an arbitrary
directory tree. `parameters` is restricted to the registered preset schema and
cannot name Blender data paths or code.

The build result and sibling manifest expose explicit scene-contract checks,
including frame range/FPS, `TP_AUDIO_BUS`, required controls and F-curves,
collections, `TP_AUDIO`, camera, and saved output. The MCP still result pairs
each planned frame with its path and byte size. Actual movie stream and duration
verification belongs to `blender/render_preview.py`, whose preview manifest uses
ffprobe through a bounded argument array with `shell=False`.

Never create every beat or keyframe through MCP, paste cue JSON as executable
Python, execute strings embedded in the cue sheet, download assets without
approval, begin a full-track render before preview approval, delete the user's
original Blender file, or overwrite an existing `.blend` without a backup or a
new explicit output name.

## Ready-to-use Codex task

```text
Connect to the configured Blender MCP server.

Use the TrackPrompt visualizer package in this repository.

Inputs:
- cue sheet: <absolute path>
- audio file: <absolute path>
- preset: abstract-geometry or space-journey
- visualizer config: <optional absolute validated JSON path>
- seed: <integer>
- output blend: <absolute path>
- preview directory: <absolute path>

First inspect the available Blender MCP tools and Blender version.

Then add the repository's blender directory to Blender's Python path and invoke
trackprompt_visualizer.mcp_entrypoints.build_scene(...). For Space Journey, use
the complete resolved configuration or a bounded parameter patch such as camera
distance, ring occlusion, palette, glow, fog, shard density, or one audio
response. Do not manually create beat keyframes through MCP and do not treat cue
or configuration JSON as Python.

After building:
1. inspect the returned scene summary;
2. confirm TP_AUDIO_BUS and all required TP_ collections;
3. render the planned still frames;
4. inspect the images;
5. make no more than two bounded parameter-revision passes;
6. render one planned up-to-10-second 720p preview clip;
7. save the final preview .blend under a new approved name;
8. report every generated artifact and any structured error.

Do not render the complete track. Do not overwrite or delete an existing
Blender file. Do not download assets.
```

## Failure handling

Treat `ok: false` as a stop condition for that operation. Validation messages
may identify the bad caller input but generic Blender failures expose only a
safe error code, message, and exception class—not stack traces or unrelated
local paths. Inspect Blender's execution-error tool locally when diagnosis is
needed, correct only the bounded parameter or operator-approved path, then retry
the failed step.
