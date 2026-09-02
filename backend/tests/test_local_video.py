from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.local_video import controller as local_video_controller
from app.local_video.archive import LocalVideoProjectArchive
from app.local_video.comfyui import ComfyUIClient, ComfyUIProviderError, discover_model_names
from app.local_video.interchange import export_resolve_interchange
from app.local_video.models import LocalVideoTimelineScene, LocalVideoWorkflowRequest
from app.local_video.orchestrator import ResumableLocalVideoRun, UnitResult
from app.local_video.package import (
    PACKAGE_FILES,
    LocalVideoPackageError,
    load_project_package,
)
from app.local_video.planning import build_shot_plan, build_story_plan
from app.local_video.prompting import compile_prompts, derive_seed
from app.local_video.qc import parse_ffprobe_contract, validate_final_contract
from app.local_video.qualification import (
    HardwareIdentity,
    QualificationCache,
    QualificationProbe,
    QualificationSample,
    qualify_hardware,
)
from app.local_video.registry import WorkflowRegistry
from app.local_video.timeline import BoundaryCandidate, resolve_timeline
from app.local_video.workflow import (
    compile_i2v_workflow,
    compile_keyframe_workflow,
    discover_keyframe_semantic_nodes,
    discover_semantic_nodes,
)
from app.mission_control.router import install_mission_control
from app.video_generation.audio import AudioEvidence

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROJECTS_ROOT = REPOSITORY_ROOT / "video-projects" / "local"
PROJECT_ID = "the-riff-that-learned-to-breathe"


def package():
    return load_project_package(PROJECTS_ROOT, PROJECT_ID)


def test_local_video_json_reader_accepts_powershell_utf8_bom(tmp_path: Path) -> None:
    lock = tmp_path / "tools-lock.json"
    lock.write_text('\ufeff{"rife":{"executable":"rife.exe"}}', encoding="utf-8")
    assert local_video_controller._read_json(lock)["rife"]["executable"] == "rife.exe"


def workflow_fixture() -> dict[str, Any]:
    return {
        "image-node": {"class_type": "LoadImage", "inputs": {"image": "old.png"}},
        "positive-node": {
            "class_type": "CLIPTextEncode",
            "_meta": {"title": "Positive Prompt"},
            "inputs": {"text": "old"},
        },
        "negative-node": {
            "class_type": "CLIPTextEncode",
            "_meta": {"title": "Negative Prompt"},
            "inputs": {"text": "old"},
        },
        "latent-node": {
            "class_type": "Wan22ImageToVideoLatent",
            "inputs": {"width": 1, "height": 1, "length": 1},
        },
        "high-model": {
            "class_type": "UnetLoaderGGUF",
            "_meta": {"title": "High Noise Expert"},
            "inputs": {"unet_name": "old-high.gguf"},
        },
        "low-model": {
            "class_type": "UnetLoaderGGUF",
            "_meta": {"title": "Low Noise Expert"},
            "inputs": {"unet_name": "old-low.gguf"},
        },
        "high-sample": {
            "class_type": "KSamplerAdvanced",
            "_meta": {"title": "High Noise Sampling"},
            "inputs": {
                "noise_seed": 0,
                "steps": 1,
                "cfg": 1,
                "start_at_step": 0,
                "end_at_step": 1,
            },
        },
        "low-sample": {
            "class_type": "KSamplerAdvanced",
            "_meta": {"title": "Low Noise Sampling"},
            "inputs": {
                "noise_seed": 0,
                "steps": 1,
                "cfg": 1,
                "start_at_step": 0,
                "end_at_step": 1,
            },
        },
        "output-node": {
            "class_type": "VHS_VideoCombine",
            "inputs": {"filename_prefix": "old"},
        },
    }


def test_package_loads_exact_local_contract_without_reading_audio() -> None:
    value = package()
    assert value.project_id == PROJECT_ID
    assert len(value.shots) == 16
    assert [shot["shotId"] for shot in value.shots] == [f"shot-{index:03d}" for index in range(1, 17)]
    assert all(len(str(shot["prompt"]).split()) <= 100 for shot in value.shots)
    assert value.project_config["spendPolicy"]["networkInferenceAllowed"] is False
    assert value.rights["rightsStatus"] == "confirmed-collaboration-full-creative-permission"


def test_package_rejects_motion_prompt_over_100_words(tmp_path: Path) -> None:
    project_root = tmp_path / PROJECT_ID
    for relative in PACKAGE_FILES:
        source = PROJECTS_ROOT / PROJECT_ID / relative
        destination = project_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    shot_path = project_root / "shot-bank.json"
    value = json.loads(shot_path.read_text(encoding="utf-8"))
    value["shots"][0]["prompt"] = " ".join(f"word{index}" for index in range(101))
    shot_path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(LocalVideoPackageError, match="100 words"):
        load_project_package(tmp_path, PROJECT_ID)


def test_timeline_rescales_snaps_and_preserves_exact_audio_clock() -> None:
    value = package()
    actual_duration = 149.354172
    scale = actual_duration / 149.0
    candidates = [
        BoundaryCandidate(float(shot["provisionalEndSeconds"]) * scale + 0.1, "phrase")
        for shot in value.shots[:-1]
    ]
    timeline = resolve_timeline(value, actual_duration_seconds=actual_duration, candidates=candidates)
    assert len(timeline) == 16
    assert timeline[-1].end_seconds == pytest.approx(actual_duration, abs=0.000001)
    assert all(6 <= scene.duration_seconds <= 14 for scene in timeline)
    assert all(scene.boundary_source == "phrase" for scene in timeline[:-1])
    assert timeline[-1].boundary_source == "audio-end"


def test_prompt_compilation_is_deterministic_and_keeps_wan_motion_concise() -> None:
    value = package()
    first = compile_prompts(value)
    second = compile_prompts(value)
    assert first == second
    assert len(first) == 16
    assert first[0].seed == derive_seed(24_081_000, 1)
    assert first[-1].seed == derive_seed(24_081_000, 16)
    assert all(len(item.motion_prompt.split()) <= 100 for item in first)
    assert all("track.wav" not in json.dumps(item.to_dict()).casefold() for item in first)
    alternates = compile_prompts(value, variation_index=1)
    assert [item.shot_id for item in alternates] == ["shot-008", "shot-014", "shot-015", "shot-016"]


def test_workflow_mapping_uses_semantic_roles_not_numeric_node_ids() -> None:
    source = workflow_fixture()
    mapping = discover_semantic_nodes(source)
    assert mapping.valid
    compiled, mapping = compile_i2v_workflow(
        source,
        uploaded_image_name="safe-image.png",
        positive_prompt="slow bird wing movement",
        negative_prompt="text, flicker",
        seed=24_081_001,
        width=1024,
        height=576,
        length_frames=81,
        steps=24,
        cfg=3.5,
        expert_boundary=0.5,
        output_prefix="project/shot-001",
        high_model_name="wan-high-q5.gguf",
        low_model_name="wan-low-q5.gguf",
    )
    assert compiled["image-node"]["inputs"]["image"] == "safe-image.png"
    assert compiled["latent-node"]["inputs"] == {"width": 1024, "height": 576, "length": 81}
    assert compiled["high-sample"]["inputs"]["end_at_step"] == 12
    assert compiled["low-sample"]["inputs"]["start_at_step"] == 12
    assert compiled["high-model"]["inputs"]["unet_name"] == "wan-high-q5.gguf"
    assert mapping.role_nodes["load start image"] == ("image-node",)
    assert source["image-node"]["inputs"]["image"] == "old.png"


def test_flux_workflow_mapping_and_compilation_use_semantic_roles() -> None:
    source = {
        "model": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "flux.safetensors"},
        },
        "pos": {
            "class_type": "CLIPTextEncode",
            "_meta": {"title": "Encode positive prompt"},
            "inputs": {"text": "old"},
        },
        "neg": {
            "class_type": "CLIPTextEncode",
            "_meta": {"title": "Encode negative prompt"},
            "inputs": {"text": "old"},
        },
        "latent": {
            "class_type": "EmptySD3LatentImage",
            "inputs": {"width": 512, "height": 512, "batch_size": 1},
        },
        "sample": {
            "class_type": "KSampler",
            "inputs": {"steps": 4, "seed": 0, "latent_image": ["latent", 0]},
        },
        "save": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": "old"},
        },
    }
    assert discover_keyframe_semantic_nodes(source).valid
    compiled, mapping = compile_keyframe_workflow(
        source,
        positive_prompt="anime workbench",
        negative_prompt="text, watermark",
        seed=24_081_001,
        width=1024,
        height=576,
        output_prefix="project/qualification/flux",
    )
    assert compiled["pos"]["inputs"]["text"] == "anime workbench"
    assert compiled["neg"]["inputs"]["text"] == "text, watermark"
    assert compiled["latent"]["inputs"]["width"] == 1024
    assert compiled["sample"]["inputs"]["seed"] == 24_081_001
    assert compiled["save"]["inputs"]["filename_prefix"] == "project/qualification/flux"
    assert mapping.role_nodes["load keyframe model"] == ("model",)
    assert source["pos"]["inputs"]["text"] == "old"


def test_workflow_registry_keeps_workflow_private_in_public_view(tmp_path: Path) -> None:
    workflow = {
        "model": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "flux.safetensors"},
        },
        "pos": {
            "class_type": "CLIPTextEncode",
            "_meta": {"title": "Encode positive prompt"},
            "inputs": {"text": "positive"},
        },
        "neg": {
            "class_type": "CLIPTextEncode",
            "_meta": {"title": "Encode negative prompt"},
            "inputs": {"text": "negative"},
        },
        "latent": {
            "class_type": "EmptySD3LatentImage",
            "inputs": {"width": 512, "height": 512, "batch_size": 1},
        },
        "sample": {
            "class_type": "KSampler",
            "inputs": {"steps": 4, "seed": 1, "latent_image": ["latent", 0]},
        },
        "save": {"class_type": "SaveImage", "inputs": {"filename_prefix": "safe"}},
    }
    registry = WorkflowRegistry(tmp_path / "workflows")
    view = registry.install(
        LocalVideoWorkflowRequest(
            workflow_id="flux-test",
            capability="keyframe-flux",
            workflow=workflow,
        )
    )
    assert "workflow" not in view.model_dump()
    loaded_view, loaded_workflow = registry.load("flux-test")
    assert loaded_view.workflow_sha256 == view.workflow_sha256
    assert loaded_workflow == workflow


def test_comfyui_endpoint_fails_closed_and_model_discovery_strips_paths() -> None:
    with pytest.raises(ComfyUIProviderError, match="loopback"):
        ComfyUIClient("http://example.com:8188")
    names = discover_model_names(
        {
            "UnetLoaderGGUF": {
                "input": {
                    "required": {
                        "unet_name": [["models\\secret\\wan2.2-high.gguf", "wan2.2-low.gguf"]]
                    }
                }
            }
        }
    )
    assert names == ("wan2.2-high.gguf", "wan2.2-low.gguf")
    assert all("secret" not in name for name in names)


class FakeQualificationRunner:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def run_probe(self, probe: QualificationProbe) -> QualificationSample:
        self.calls.append(probe.tier)
        if probe.tier == "A14B-Q5_K_M":
            return QualificationSample(valid_output=False, elapsed_seconds=1, cuda_oom=True)
        return QualificationSample(valid_output=True, elapsed_seconds=2, peak_vram_bytes=10)


@pytest.mark.asyncio
async def test_qualification_is_bounded_sequential_and_cached(tmp_path: Path) -> None:
    identity = HardwareIdentity(
        gpu_name="RTX 3060",
        vram_bytes=12_288,
        driver_version="test",
        comfyui_revision="test",
        custom_node_revisions={"gguf": "abc"},
        model_sha256={"high": "a" * 64, "low": "b" * 64},
    )
    cache = QualificationCache(tmp_path)
    runner = FakeQualificationRunner()
    first = await qualify_hardware(identity, runner, cache)
    assert first.selected_tier == "A14B-Q4_K_M"
    assert runner.calls == ["A14B-Q5_K_M", "A14B-Q4_K_M"]
    assert [candidate.state for candidate in first.candidates] == ["failed", "passed", "skipped"]
    second_runner = FakeQualificationRunner()
    second = await qualify_hardware(identity, second_runner, cache)
    assert second.cached is True
    assert second_runner.calls == []


def test_project_revision_retains_audio_and_protects_analysis_dependency(tmp_path: Path) -> None:
    value = package()
    source = tmp_path / "source.bin"
    source.write_bytes(b"synthetic-test-audio-placeholder")
    import hashlib

    audio_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    audio = AudioEvidence(
        path=source,
        sha256=audio_hash,
        duration_seconds=149.354172,
        container="wav",
        codec="pcm_s16le",
        sample_rate_hz=44_100,
        channels=2,
        size_bytes=source.stat().st_size,
    )
    timeline = resolve_timeline(value, actual_duration_seconds=audio.duration_seconds)
    prompts = compile_prompts(value)
    story = build_story_plan(value, timeline)
    shots = build_shot_plan(value, timeline, prompts)
    archive = LocalVideoProjectArchive(tmp_path / "state")
    analysis_id = str(uuid4())
    analysis = {"schemaVersion": "test", "file": {"displayName": "untrusted.wav"}}
    revision = archive.create_revision(
        package=value,
        audio=audio,
        analysis=analysis,
        analysis_id=analysis_id,
        story_plan=story,
        shot_plan=shots,
        timeline=[item.model_dump(mode="json", by_alias=True) for item in timeline],
        prompts=[item.to_dict() for item in prompts],
    )
    source.unlink()
    current = archive.current(PROJECT_ID)
    assert current is not None
    assert current["current_revision_id"] == revision
    assert archive.artifact(PROJECT_ID, "source/audio-master.bin").is_file()
    assert archive.is_analysis_referenced(analysis_id) is True
    assert archive.create_revision(
        package=value,
        audio=AudioEvidence(path=archive.artifact(PROJECT_ID, "source/audio-master.bin"), **{
            "sha256": audio.sha256,
            "duration_seconds": audio.duration_seconds,
            "container": audio.container,
            "codec": audio.codec,
            "sample_rate_hz": audio.sample_rate_hz,
            "channels": audio.channels,
            "size_bytes": audio.size_bytes,
        }),
        analysis=analysis,
        analysis_id=analysis_id,
        story_plan=story,
        shot_plan=shots,
        timeline=[item.model_dump(mode="json", by_alias=True) for item in timeline],
        prompts=[item.to_dict() for item in prompts],
    ) == revision
    preview = archive.delete_preview(PROJECT_ID)
    assert preview.includes_retained_audio is True
    assert preview.artifact_count >= 7


class RecordingExecutor:
    def __init__(self, *, fail_once: tuple[str, str] | None = None, qc_passed: bool = True) -> None:
        self.fail_once = fail_once
        self.qc_passed = qc_passed
        self.calls: list[tuple[str, str]] = []

    async def execute(self, stage: str, unit_id: str) -> UnitResult:
        self.calls.append((stage, unit_id))
        if self.fail_once == (stage, unit_id):
            self.fail_once = None
            raise RuntimeError("synthetic failure")
        digest = __import__("hashlib").sha256(f"{stage}:{unit_id}".encode()).hexdigest()
        return UnitResult(output_sha256=digest, qc_passed=self.qc_passed, detail="verified")


@pytest.mark.asyncio
async def test_resumable_run_preserves_success_and_never_completes_before_qc(tmp_path: Path) -> None:
    run = ResumableLocalVideoRun(
        tmp_path / "run.json",
        project_id=PROJECT_ID,
        revision_id=str(uuid4()),
        package_digest="a" * 64,
        alternate_shot_ids=frozenset({"shot-008", "shot-014", "shot-015", "shot-016"}),
    )
    executor = RecordingExecutor(fail_once=("video", "shot-004"))
    first = await run.run(executor)
    assert first["status"] == "failed"
    assert first["finalQcPassed"] is False
    completed_before = list(executor.calls)
    run.retry_shot("shot-004")
    second = await run.run(executor)
    assert second["status"] == "complete"
    assert second["finalQcPassed"] is True
    assert executor.calls.count(("video", "shot-001")) == 1
    assert len(executor.calls) > len(completed_before)

    blocked = ResumableLocalVideoRun(
        tmp_path / "blocked.json",
        project_id=PROJECT_ID,
        revision_id=str(uuid4()),
        package_digest="b" * 64,
        alternate_shot_ids=frozenset(),
    )
    failed_qc = await blocked.run(RecordingExecutor(qc_passed=False))
    assert failed_qc["status"] == "failed"
    assert failed_qc["finalQcPassed"] is False


def test_final_qc_requires_exact_1080p24_audio_duration_scenes_transitions_and_freshness() -> None:
    evidence = parse_ffprobe_contract(
        {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1920,
                    "height": 1080,
                    "avg_frame_rate": "24/1",
                    "nb_read_frames": "3585",
                },
                {"codec_type": "audio", "codec_name": "aac"},
            ],
            "format": {"duration": "149.354172"},
        }
    )
    result = validate_final_contract(
        evidence,
        source_duration_seconds=149.354172,
        required_scene_count=16,
        represented_scene_ids=[f"shot-{index:03d}" for index in range(1, 17)],
        edit_transition_count=15,
        expected_manifest_hashes_match=True,
    )
    assert result.passed is True
    stale = validate_final_contract(
        evidence,
        source_duration_seconds=149.354172,
        required_scene_count=16,
        represented_scene_ids=[f"shot-{index:03d}" for index in range(1, 17)],
        edit_transition_count=15,
        expected_manifest_hashes_match=False,
    )
    assert stale.passed is False


def test_resolve_interchange_emits_all_required_formats(tmp_path: Path) -> None:
    scenes = tuple(
        LocalVideoTimelineScene(
            shot_id=f"shot-{index:03d}",
            order=index,
            start_seconds=(index - 1) * 8,
            end_seconds=index * 8,
            duration_seconds=8,
            boundary_source="test",
        )
        for index in range(1, 17)
    )
    clips: dict[str, Path] = {}
    for scene in scenes:
        path = tmp_path / "clips" / f"{scene.shot_id}.mov"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic")
        clips[scene.shot_id] = path
    transitions = [
        {
            "transitionId": f"transition-{index:03d}",
            "fromShotId": f"shot-{index:03d}",
            "toShotId": f"shot-{index + 1:03d}",
        }
        for index in range(1, 16)
    ]
    files = export_resolve_interchange(
        scenes=scenes,
        clip_paths=clips,
        transitions=transitions,
        output_root=tmp_path / "edit",
    )
    assert set(files) == {"fcpxml", "fcp7", "edl", "markers", "editSheet"}
    assert all(path.is_file() and path.stat().st_size > 0 for path in files.values())


def test_mission_control_exposes_local_projects_without_provider_contact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRACKPROMPT_DATA_DIR", str(tmp_path / "data"))
    application = FastAPI()
    application.state.settings = SimpleNamespace(data_dir=tmp_path / "data")
    install_mission_control(application)
    with TestClient(application) as client:
        response = client.get("/api/mission-control/video/local/projects")
        assert response.status_code == 200
        item = next(value for value in response.json() if value["projectId"] == PROJECT_ID)
        assert item["title"] == "The Riff That Learned to Breathe"
        project = client.get(f"/api/mission-control/video/local/projects/{PROJECT_ID}")
        assert project.status_code == 200
        assert project.json()["analysisArchived"] is False
        assert not (tmp_path / "provider-contacted").exists()
    service = getattr(application.state, "mission_control_service", None)
    if service is not None:
        service.close()
