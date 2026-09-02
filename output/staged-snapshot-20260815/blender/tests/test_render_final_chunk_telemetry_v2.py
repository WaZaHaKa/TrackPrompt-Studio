from __future__ import annotations

import json

from blender.render_final_chunk import RENDER_EVENT_PREFIX, RenderEventEmitter


class _Scene(dict[str, str]):
    frame_current = 12


def test_story_context_accepts_v2_embedded_shot_plan_key() -> None:
    scene = _Scene(
        trackprompt_shot_plan_json=json.dumps(
            {
                "shots": [
                    {
                        "id": "shot-signal-01",
                        "name": "Signal Emerges",
                        "actId": "signal",
                        "frameStart": 1,
                        "frameEnd": 30,
                        "complexityClass": "light",
                    }
                ]
            }
        )
    )
    emitter = RenderEventEmitter(
        scene,
        job_id="job-1",
        worker_id="worker-1",
        project_id="generic-project",
        scene_sha256="A" * 64,
        profile_sha256="B" * 64,
        output_variant_id="horizontal-master",
        width=1920,
        height=1080,
        composition_profile_id="horizontal-safe-v1",
        artifact_directory="frames",
        artifact_filename_pattern="frame_%06d.png",
        start=1,
        end=30,
    )

    assert emitter._story_context(12) == {
        "actId": "signal",
        "actName": "Signal",
        "shotId": "shot-signal-01",
        "shotName": "Signal Emerges",
        "complexityClass": "light",
    }


def test_frame_written_emits_hash_bound_variant_and_relative_artifact(capsys: object) -> None:
    emitter = RenderEventEmitter(
        _Scene(),
        job_id="job-1",
        worker_id="worker-1",
        project_id="generic-project",
        scene_sha256="A" * 64,
        profile_sha256="B" * 64,
        output_variant_id="horizontal-master",
        width=1920,
        height=1080,
        composition_profile_id="horizontal-safe-v1",
        artifact_directory="checkpoints/.inflight-safe/frames",
        artifact_filename_pattern="frame_%06d.png",
        start=1,
        end=30,
    )

    emitter.render_write()

    line = capsys.readouterr().out.strip()  # type: ignore[attr-defined]
    assert line.startswith(RENDER_EVENT_PREFIX)
    payload = json.loads(line.removeprefix(RENDER_EVENT_PREFIX))
    assert payload["schemaVersion"] == "2.0.0"
    assert payload["outputVariantId"] == "horizontal-master"
    assert payload["width"] == 1920
    assert payload["height"] == 1080
    assert payload["sceneSha256"] == "A" * 64
    assert payload["profileSha256"] == "B" * 64
    assert payload["artifactRelativePath"] == (
        "checkpoints/.inflight-safe/frames/frame_000012.png"
    )
    assert payload["emittedAt"].endswith("Z")
