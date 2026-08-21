# ruff: noqa: E402
from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import wave
import xml.etree.ElementTree as ET
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[3]
if str(PACKAGE_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT / "backend"))

from app.mission_control.store import MissionControlStore
from app.video_generation import cli as video_cli
from app.video_generation import timeline as timeline_module
from app.video_generation.assembly import build_assembly_plan, execute_assembly
from app.video_generation.authorization import (
    BatchAuthorization,
    authorization_phrase,
)
from app.video_generation.continuity import derive_shot_seed
from app.video_generation.contracts import (
    ContractError,
    GenerationProfile,
    load_project_config,
    load_shot_bank,
)
from app.video_generation.costs import estimate
from app.video_generation.davinci import (
    _file_uri,
    export_edl,
    export_fcp7_xml,
    export_fcpxml,
)
from app.video_generation.exporter import export_davinci_package
from app.video_generation.gcp_veo import (
    ProviderError,
    ProviderRequestContext,
    VeoRestClient,
    build_request_payload,
    doctor,
    response_output_uris,
    validate_gcs_uri,
)
from app.video_generation.jsonio import atomic_write_json
from app.video_generation.media import MediaProbe, probe
from app.video_generation.mission_controller import VideoGenerationController
from app.video_generation.mission_models import (
    VideoAuthorizationRequest,
    VideoChainReferenceRequest,
    VideoError,
    VideoJobState,
    VideoPlanCreateRequest,
    VideoRetryRequest,
    VideoReviewState,
    VideoShotAttempt,
    VideoShotState,
    utc_now,
)
from app.video_generation.operations import OperationRecord, save_operation
from app.video_generation.planning import compile_project_plan
from app.video_generation.timeline import resolve_timeline

PROJECT = PACKAGE_ROOT / "video-projects" / "the-glitch-is-me"


def _compile(tmp_path: Path, config_name: str = "project-config.json"):
    return compile_project_plan(
        project_config_path=PROJECT / config_name,
        creative_bible_path=PROJECT / "creative-bible.json",
        shot_bank_path=PROJECT / "shot-bank.json",
        gcs_bucket="gs://example-trackprompt-video",
    )


def _write_silence(path: Path, seconds: float = 41.25) -> None:
    rate = 48_000
    samples = round(seconds * rate)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        silence = b"\x00\x00\x00\x00"
        chunk = silence * 4_800
        remaining = samples
        while remaining:
            count = min(4_800, remaining)
            handle.writeframesraw(chunk[: count * 4])
            remaining -= count


def test_project_and_shot_contracts_are_complete() -> None:
    config = load_project_config(PROJECT / "project-config.json")
    shots = load_shot_bank(PROJECT / "shot-bank.json")
    assert config.selected_profile().model_id == "veo-3.1-fast-generate-001"
    assert config.selected_profile().resolution == "1080p"
    assert len(shots) == 16
    assert [shot.order for shot in shots] == list(range(1, 17))
    assert set(config.required_shot_ids) == {shot.shot_id for shot in shots}


def test_fast_4k_is_rejected(tmp_path: Path) -> None:
    profile = GenerationProfile.from_dict(
        {
            "profileId": "bad-fast-4k",
            "modelId": "veo-3.1-fast-generate-001",
            "resolution": "4k",
            "durationSeconds": 8,
            "sampleCount": 1,
            "generateAudio": False,
        }
    )
    shot = replace(_compile(tmp_path).shots[0], model_id=profile.model_id, resolution="4k")
    with pytest.raises(ContractError, match="supports 720p/1080p"):
        build_request_payload(shot)

    with pytest.raises(ContractError, match="currently supports 720p/1080p"):
        _compile(tmp_path, "project-config.4k-optional.json")


def test_cost_profiles_match_reviewed_snapshot() -> None:
    fast = load_project_config(PROJECT / "project-config.json")
    quality = load_project_config(PROJECT / "project-config.quality-1080p.json")
    four_k = load_project_config(PROJECT / "project-config.4k-optional.json")
    assert float(estimate(fast.selected_profile(), 16, 1.5).base_usd) == 12.80
    assert float(estimate(quality.selected_profile(), 16, 1.5).base_usd) == 25.60
    assert float(estimate(four_k.selected_profile(), 16, 1.5).base_usd) == 51.20


def test_compile_is_deterministic_and_privacy_bounded(tmp_path: Path) -> None:
    first = _compile(tmp_path)
    second = _compile(tmp_path)
    assert first.plan_digest == second.plan_digest
    assert len(first.shots) == 16
    assert first.base_estimated_cost_usd == 12.8
    assert first.conservative_estimated_cost_usd == 19.2
    assert first.max_spend_usd == 24.0
    serialized = json.dumps(first.to_dict(), ensure_ascii=False).lower()
    assert "c:\\users\\" not in serialized
    assert "/users/" not in serialized
    assert "i'm a soul in a loop" not in serialized
    assert "ollama_api_key" not in serialized
    assert 'generateaudio": true' not in serialized
    assert first.pricing_snapshot_date
    assert first.rate_usd_per_output_second == 0.1


def test_pricing_snapshot_and_exact_request_parameters_are_digest_bound(tmp_path: Path) -> None:
    plan = _compile(tmp_path)
    changed_price = replace(plan, pricing_snapshot_date="2099-01-01").with_digest()
    changed_enhancement = replace(
        plan,
        shots=(replace(plan.shots[0], enhance_prompt=not plan.shots[0].enhance_prompt), *plan.shots[1:]),
    ).with_digest()
    assert changed_price.plan_digest != plan.plan_digest
    assert changed_enhancement.plan_digest != plan.plan_digest


def test_request_payload_matches_veo_async_contract(tmp_path: Path) -> None:
    shot = _compile(tmp_path).shots[0]
    payload = build_request_payload(shot)
    assert payload["instances"] == [{"prompt": shot.prompt}]
    parameters = payload["parameters"]
    assert parameters["storageUri"].startswith("gs://example-trackprompt-video/")
    assert parameters["durationSeconds"] == 8
    assert parameters["resolution"] == "1080p"
    assert parameters["aspectRatio"] == "16:9"
    assert parameters["generateAudio"] is False
    assert "task" not in parameters
    assert "negativePrompt" in parameters
    assert response_output_uris({"response": {"videos": [{"gcsUri": "gs://bucket/a.mp4"}]}}) == (
        "gs://bucket/a.mp4",
    )


@pytest.mark.parametrize(
    ("body", "content_type", "expected_format"),
    [
        (
            json.dumps(
                {
                    "error": {
                        "code": 400,
                        "status": "INVALID_ARGUMENT",
                        "message": 'Unknown name "task" at parameters',
                        "accessToken": "must-not-survive",
                    }
                }
            ).encode(),
            "application/json",
            "json",
        ),
        (b"bad gateway Bearer must-not-survive", "text/plain", "text"),
    ],
)
def test_http_400_writes_redacted_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    body: bytes,
    content_type: str,
    expected_format: str,
) -> None:
    shot = _compile(tmp_path).shots[0]

    def reject(request, *, timeout):  # type: ignore[no-untyped-def]
        raise urllib.error.HTTPError(
            request.full_url,
            400,
            "Bad Request",
            {"Content-Type": content_type, "Set-Cookie": "must-not-survive"},
            io.BytesIO(body),
        )

    monkeypatch.setattr("urllib.request.urlopen", reject)
    diagnostics = tmp_path / "provider-errors"
    client = VeoRestClient(
        project_id="test-project",
        token_provider=lambda: "must-not-survive",
        diagnostics_root=diagnostics,
    )
    with pytest.raises(ProviderError) as raised:
        client.submit(
            shot,
            context=ProviderRequestContext(
                phase="submit",
                job_id="job-1",
                shot_id=shot.shot_id,
                attempt_id="attempt-1",
            ),
        )
    assert raised.value.http_status == 400
    assert raised.value.diagnostic_id
    receipt_path = next(diagnostics.glob("*.json"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["jobId"] == "job-1"
    assert receipt["response"]["bodyFormat"] == expected_format
    assert receipt["response"]["headers"] == {"content-type": content_type}
    serialized = json.dumps(receipt)
    assert "must-not-survive" not in serialized
    assert "authorization" in serialized.lower()
    assert shot.prompt not in serialized


def test_seed_derivation_and_reference_hash_are_digest_bound(tmp_path: Path) -> None:
    groups = ("world-global", "quantum-siren-identity")
    first = derive_shot_seed(
        master_seed=42,
        project_id="project",
        continuity_group_ids=groups,
        shot_id="shot-001",
        variation_index=0,
    )
    assert first == derive_shot_seed(
        master_seed=42,
        project_id="project",
        continuity_group_ids=groups,
        shot_id="shot-001",
        variation_index=0,
    )
    assert first != derive_shot_seed(
        master_seed=42,
        project_id="project",
        continuity_group_ids=groups,
        shot_id="shot-001",
        variation_index=1,
    )
    reference = tmp_path / "character.png"
    reference.write_bytes(b"\x89PNG\r\n\x1a\nfirst approved image")
    plan = compile_project_plan(
        project_config_path=PROJECT / "project-config.json",
        creative_bible_path=PROJECT / "creative-bible.json",
        shot_bank_path=PROJECT / "shot-bank.json",
        continuity_profile_path=PROJECT / "continuity-profile.json",
        gcs_bucket="gs://example-trackprompt-video",
        reference_image_path=reference,
    )
    reference.write_bytes(b"\x89PNG\r\n\x1a\nchanged approved image")
    changed = compile_project_plan(
        project_config_path=PROJECT / "project-config.json",
        creative_bible_path=PROJECT / "creative-bible.json",
        shot_bank_path=PROJECT / "shot-bank.json",
        continuity_profile_path=PROJECT / "continuity-profile.json",
        gcs_bucket="gs://example-trackprompt-video",
        reference_image_path=reference,
    )
    assert plan.plan_digest != changed.plan_digest
    assert plan.shots[0].first_frame_reference is not None
    request = build_request_payload(plan.shots[0])
    assert request["instances"][0]["image"]["gcsUri"].startswith("gs://")
    assert "referenceImages" not in request["instances"][0]


def test_one_authorization_binds_exact_plan_and_cap(tmp_path: Path) -> None:
    plan = _compile(tmp_path)
    phrase = authorization_phrase(plan.project_id, plan.plan_digest, plan.max_spend_usd)
    authorization = BatchAuthorization.create(
        project_id=plan.project_id,
        plan_digest=plan.plan_digest,
        max_spend_usd=plan.max_spend_usd,
        confirmation=phrase,
    )
    authorization.validate_for(
        project_id=plan.project_id,
        plan_digest=plan.plan_digest,
        current_reserved_usd=12.8,
        next_request_usd=0.8,
    )
    with pytest.raises(ContractError, match="exceed"):
        authorization.validate_for(
            project_id=plan.project_id,
            plan_digest=plan.plan_digest,
            current_reserved_usd=23.5,
            next_request_usd=0.8,
        )
    with pytest.raises(ContractError, match="another project"):
        authorization.validate_for(
            project_id="another-project",
            plan_digest=plan.plan_digest,
            current_reserved_usd=0,
            next_request_usd=0.8,
        )
    expired = replace(
        authorization,
        expires_at=(datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
    )
    with pytest.raises(ContractError, match="expired"):
        expired.validate_for(
            project_id=plan.project_id,
            plan_digest=plan.plan_digest,
            current_reserved_usd=0,
            next_request_usd=0.8,
        )


def test_provider_output_uris_are_scoped_and_reject_traversal() -> None:
    assert validate_gcs_uri(
        "gs://example-trackprompt-video/video-generation/project/shot/result.mp4",
        required_prefix="gs://example-trackprompt-video/video-generation/project/shot/",
    ).endswith("result.mp4")
    with pytest.raises(ProviderError, match="authorized storage prefix"):
        validate_gcs_uri(
            "gs://another-bucket/result.mp4",
            required_prefix="gs://example-trackprompt-video/video-generation/project/shot/",
        )
    with pytest.raises(ProviderError, match="invalid"):
        validate_gcs_uri("gs://example-trackprompt-video/../private.mp4")


def test_doctor_reports_no_network_contact_when_gcloud_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    result = doctor(
        project_id="trackprompt-preflight",
        bucket="trackprompt-video-preflight",
    )
    assert result.ok is False
    assert result.network_contacted is False
    assert result.checks[0]["id"] == "gcloud-installed"


def test_timeline_covers_audio_and_exports_parseable_xml(tmp_path: Path) -> None:
    audio = tmp_path / "master.wav"
    _write_silence(audio)
    clips = tmp_path / "clips"
    clips.mkdir()
    timeline = resolve_timeline(
        project_id="the-glitch-is-me",
        title="The Glitch Is Me",
        audio_path=audio,
        chapter_map_path=PROJECT / "chapter-map.json",
        edit_blueprint_path=PROJECT / "edit-blueprint.json",
        clips_root=clips,
        output_width=1920,
        output_height=1080,
        fps=24,
        generated_clip_duration_seconds=8,
        target_edit_seconds=6.0,
    )
    segments = timeline["segments"]
    assert segments[0]["timelineStartFrames"] == 0
    assert (
        segments[-1]["timelineStartFrames"] + segments[-1]["durationFrames"]
        == timeline["timeline"]["durationFrames"]
    )
    assert len(timeline["markers"]) == 8
    assert all(int(item["durationFrames"]) > 0 for item in segments)

    fcpxml = tmp_path / "timeline.fcpxml"
    fcp7 = tmp_path / "timeline.xml"
    edl = tmp_path / "timeline.edl"
    export_fcpxml(timeline, fcpxml)
    export_fcp7_xml(timeline, fcp7)
    export_edl(timeline, edl)
    ET.parse(fcpxml)
    ET.parse(fcp7)
    assert '<fcpxml version="1.11">' in fcpxml.read_text(encoding="utf-8")
    assert "FCM: NON-DROP FRAME" in edl.read_text(encoding="utf-8")


def test_full_song_rough_cut_uses_all_shots_and_escalates_final_chorus(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio = tmp_path / "master.wav"
    _write_silence(audio, seconds=0.1)
    monkeypatch.setattr(timeline_module, "audio_duration_seconds", lambda *_args, **_kwargs: 297.68)
    clip_paths = {f"shot-{index:03d}": tmp_path / f"shot-{index:03d}.mp4" for index in range(1, 17)}
    timeline = resolve_timeline(
        project_id="the-glitch-is-me",
        title="The Glitch Is Me",
        audio_path=audio,
        chapter_map_path=PROJECT / "chapter-map.json",
        edit_blueprint_path=PROJECT / "edit-blueprint.json",
        clips_root=tmp_path,
        clip_paths=clip_paths,
        target_edit_seconds=4.5,
        local_edit_digest="d" * 64,
    )
    segments = timeline["segments"]
    assert 55 <= len(segments) <= 80
    assert {item["shotId"] for item in segments} == {
        f"shot-{index:03d}" for index in range(1, 17)
    }
    assert len({(item["shotId"], item["sourceInFrames"], item["treatment"]) for item in segments}) == len(segments)
    first = [item for item in segments if item["chapterId"] == "unsolvable-distance"]
    final = [item for item in segments if item["chapterId"] == "transcendence"]
    closing = [item for item in segments if item["chapterId"] == "system-failure"]
    assert {"shot-005", "shot-006"}.issubset({item["shotId"] for item in first})
    assert {"shot-005", "shot-006", "shot-013", "shot-014"}.issubset(
        {item["shotId"] for item in final}
    )
    assert max(float(item["cropScale"]) for item in final) > max(
        float(item["cropScale"]) for item in first
    )
    assert {"shot-001", "shot-002"}.issubset({item["shotId"] for item in closing})
    assert closing[-1]["shotId"] == "shot-016"


def test_assembly_plan_is_local_and_audio_mastered(tmp_path: Path) -> None:
    audio = tmp_path / "master.wav"
    _write_silence(audio, seconds=8.0)
    clips = tmp_path / "clips"
    clips.mkdir()
    timeline = resolve_timeline(
        project_id="test",
        title="Test",
        audio_path=audio,
        chapter_map_path=PROJECT / "chapter-map.json",
        clips_root=clips,
        target_edit_seconds=6.0,
    )
    plan = build_assembly_plan(
        timeline,
        output_path=tmp_path / "delivery.mp4",
        work_root=tmp_path / "work",
    )
    assert len(plan.commands) == len(timeline["segments"]) + 2
    final = plan.commands[-1]
    assert str(audio) in final
    assert "-shortest" in final
    assert "aac" in final
    assert all("curl" not in token.lower() for command in plan.commands for token in command)


def test_export_package_writes_all_resolve_fallbacks(tmp_path: Path) -> None:
    audio = tmp_path / "master.wav"
    _write_silence(audio, seconds=8.0)
    clips = tmp_path / "clips"
    clips.mkdir()
    timeline = resolve_timeline(
        project_id="test",
        title="Test",
        audio_path=audio,
        chapter_map_path=PROJECT / "chapter-map.json",
        clips_root=clips,
        target_edit_seconds=6.0,
    )
    outputs = export_davinci_package(timeline, output_root=tmp_path / "davinci")
    for key in (
        "fcpxml",
        "fcp7Xml",
        "edl",
        "editSheet",
        "markers",
        "assemblyPlan",
        "assemblyPowerShell",
        "readme",
    ):
        assert Path(outputs[key]).is_file(), key
    assert Path(outputs["previewOutput"]).name == "autonomous-preview-1080p.mp4"
    for key in ("editPlan", "relinkMap", "coverageReport", "renderManifest", "verificationReport"):
        assert Path(outputs[key]).is_file(), key
    assert Path(outputs["fcpxml"]).name == "trackprompt-timeline.fcpxml"
    assert Path(outputs["fcp7Xml"]).name == "trackprompt-timeline.xml"
    assert Path(outputs["edl"]).name == "trackprompt-timeline.edl"
    ET.parse(outputs["fcpxml"])
    ET.parse(outputs["fcp7Xml"])


def test_real_ffmpeg_assembles_and_probes_complete_preview(tmp_path: Path) -> None:
    ffmpeg = os.getenv("TRACKPROMPT_MC_FFMPEG_PATH") or shutil.which("ffmpeg")
    ffprobe = os.getenv("TRACKPROMPT_MC_FFPROBE_PATH") or shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("real FFmpeg smoke requires ffmpeg and ffprobe on PATH")
    audio = tmp_path / "master.wav"
    clip = tmp_path / "clip.mp4"
    _write_silence(audio, seconds=1.0)
    created = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x183040:s=320x180:r=24:d=1",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(clip),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert created.returncode == 0, created.stderr
    timeline = {
        "schemaVersion": "1.0.0",
        "projectId": "synthetic-proof",
        "title": "Synthetic proof",
        "timeline": {
            "fps": 24,
            "width": 320,
            "height": 180,
            "durationFrames": 24,
            "durationSeconds": 1.0,
            "audioPath": str(audio),
            "generatedClipDurationSeconds": 1,
            "targetEditSeconds": 1.0,
        },
        "markers": [
            {
                "markerId": "chapter-01",
                "chapterId": "chapter-01",
                "title": "Proof",
                "startFrames": 0,
                "durationFrames": 24,
            }
        ],
        "segments": [
            {
                "segmentId": "seg-0001",
                "chapterId": "chapter-01",
                "shotId": "shot-001",
                "clipPath": str(clip),
                "timelineStartFrames": 0,
                "durationFrames": 24,
                "sourceInFrames": 0,
                "sourceDurationFrames": 24,
                "transitionIn": "cut",
                "transitionOut": "cut",
                "editorialNote": "Synthetic bounded assembly proof.",
            }
        ],
    }
    plan = build_assembly_plan(
        timeline,
        output_path=tmp_path / "complete-preview.mp4",
        work_root=tmp_path / "assembly-work",
        ffmpeg=ffmpeg,
    )
    execute_assembly(plan)
    evidence = probe(Path(plan.output_path), ffprobe=ffprobe)
    assert evidence.width == 320
    assert evidence.height == 180
    assert evidence.has_audio
    assert abs(evidence.duration_seconds - 1.0) <= 0.1


def test_windows_paths_export_as_real_file_uris() -> None:
    assert _file_uri(r"C:\Users\theon\TrackPrompt Studio\clip 1.mp4") == (
        "file:///C:/Users/theon/TrackPrompt%20Studio/clip%201.mp4"
    )


def test_poll_ignores_operations_from_another_plan(tmp_path: Path) -> None:
    project_root = tmp_path / "runtime" / "test-project"
    current = OperationRecord.new(
        operation_id="current",
        project_id="test-project",
        plan_digest="current-plan",
        shot_id="shot-001",
        model_id="veo-3.1-fast-generate-001",
        reserved_cost_usd=0.8,
        operation_name="current-operation",
        storage_uri="gs://bucket/current/",
    ).updated(status="succeeded", output_uris=("gs://bucket/current.mp4",))
    stale = OperationRecord.new(
        operation_id="stale",
        project_id="test-project",
        plan_digest="stale-plan",
        shot_id="shot-001",
        model_id="veo-3.1-fast-generate-001",
        reserved_cost_usd=0.8,
        operation_name="stale-operation",
        storage_uri="gs://bucket/stale/",
    ).updated(status="running")
    save_operation(project_root, current)
    save_operation(project_root, stale)

    class NoFetchClient:
        def fetch(self, **_: object) -> dict[str, object]:
            raise AssertionError("no provider fetch should be needed")

    counts = video_cli._poll_once(
        {"planDigest": "current-plan"},
        project_root,
        NoFetchClient(),  # type: ignore[arg-type]
    )
    assert counts == {"succeeded": 1}


def test_first_download_candidate_gets_canonical_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _compile(tmp_path).to_dict()
    plan_path = tmp_path / "plan.json"
    atomic_write_json(plan_path, plan)
    runtime_root = tmp_path / "runtime"
    project_root = runtime_root / plan["projectId"]
    record = OperationRecord.new(
        operation_id="download-test",
        project_id=str(plan["projectId"]),
        plan_digest=str(plan["planDigest"]),
        shot_id="shot-001",
        model_id="veo-3.1-fast-generate-001",
        reserved_cost_usd=0.8,
        operation_name="operation-download-test",
        storage_uri="gs://bucket/output/",
    ).updated(
        status="succeeded",
        output_uris=("gs://bucket/a.mp4", "gs://bucket/b.mp4"),
    )
    save_operation(project_root, record)
    copied: list[Path] = []

    def fake_copy(_: str, destination: Path) -> None:
        copied.append(destination)

    monkeypatch.setattr(video_cli, "copy_gcs_uri", fake_copy)
    video_cli.command_download(
        argparse.Namespace(
            plan=str(plan_path),
            runtime_root=str(runtime_root),
            clips_root=str(tmp_path / "clips"),
        )
    )
    assert [path.name for path in copied] == [
        "shot-001.mp4",
        "shot-001-candidate-2.mp4",
    ]


@pytest.mark.asyncio
async def test_retry_modes_preserve_setup_or_invalidate_authorization(tmp_path: Path) -> None:
    state_root = tmp_path / "state" / "mission-control"
    analysis_id = str(uuid4())
    (state_root.parent / "jobs" / analysis_id).mkdir(parents=True)
    store = MissionControlStore(state_root / "mission-control.sqlite3")
    blocker = asyncio.Event()

    class BlockingProvider:
        async def submit(self, shot, *, context):  # type: ignore[no-untyped-def]
            await blocker.wait()
            return {"name": "never"}

        async def fetch(self, *, model_id, operation_name, context):  # type: ignore[no-untyped-def]
            raise AssertionError("poll should not run")

        async def download(self, uri: str, destination: Path) -> None:
            raise AssertionError("download should not run")

        async def upload_reference(self, source: Path, destination_uri: str, expected_sha256: str) -> None:
            raise AssertionError("reference upload should not run")

    async def notify(_: int) -> None:
        return None

    controller = VideoGenerationController(
        repository_root=PACKAGE_ROOT,
        state_root=state_root,
        store=store,
        notify_event=notify,
        provider_factory=lambda _project, _region: BlockingProvider(),
        poll_interval_seconds=0.01,
    )
    try:
        planned = await controller.create_plan(
                VideoPlanCreateRequest(
                    analysis_job_id=analysis_id,
                    project_id="the-glitch-is-me",
                    gcp_project_id="test-project",
                gcs_bucket="example-trackprompt-video",
            )
        )
        authorized = await controller.authorize(
            planned.job_id,
            VideoAuthorizationRequest(confirmation=planned.authorization_phrase),
        )
        stored = store.get_video_job(planned.job_id)
        assert stored is not None
        shot = stored.shots[0]
        failed = VideoShotAttempt(
            id="shot-001-attempt-01-test",
            attempt=1,
            idempotency_key="a" * 64,
            state=VideoShotState.FAILED,
            reserved_cost_usd=shot.estimated_cost_usd,
            error=VideoError(code="provider_request_failed", summary="HTTP 400"),
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        failed_job = stored.model_copy(
            update={
                "state": VideoJobState.FAILED,
                "shots": (
                    shot.model_copy(update={"attempts": (failed,)}),
                    *stored.shots[1:],
                ),
                "reserved_cost_usd": shot.estimated_cost_usd,
            }
        )
        store.put_video_job(failed_job)
        same = await controller.retry(
            authorized.job_id,
            "shot-001",
            VideoRetryRequest(mode="same_setup"),
        )
        assert same.plan_digest == planned.plan_digest
        assert same.shots[0].seed == planned.shots[0].seed
        assert same.shots[0].variation_index == 0
        await controller.cancel(same.job_id)

        store.put_video_job(failed_job)
        variation = await controller.retry(
            planned.job_id,
            "shot-001",
            VideoRetryRequest(mode="new_variation"),
        )
        assert variation.state is VideoJobState.PLANNED
        assert variation.plan_digest != planned.plan_digest
        assert variation.shots[0].seed != planned.shots[0].seed
        assert variation.shots[0].variation_index == 1
        assert variation.authorization_expires_at is None
        history = (
            state_root.parent
            / "video-generation"
            / "the-glitch-is-me"
            / planned.job_id
            / "authorization-history"
            / f"{planned.plan_digest}.json"
        )
        assert history.is_file()
    finally:
        controller.close()
        store.close()


@pytest.mark.asyncio
async def test_accepted_previous_shot_frame_changes_digest_and_requires_fresh_authorization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_root = tmp_path / "state" / "mission-control"
    analysis_id = str(uuid4())
    (state_root.parent / "jobs" / analysis_id).mkdir(parents=True)
    store = MissionControlStore(state_root / "mission-control.sqlite3")

    async def notify(_: int) -> None:
        return None

    def fake_ffmpeg(arguments, **kwargs):  # type: ignore[no-untyped-def]
        Path(arguments[-1]).write_bytes(b"\x89PNG\r\n\x1a\naccepted final frame")
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr("app.video_generation.mission_controller.subprocess.run", fake_ffmpeg)
    controller = VideoGenerationController(
        repository_root=PACKAGE_ROOT,
        state_root=state_root,
        store=store,
        notify_event=notify,
        ffmpeg_path=lambda: Path("ffmpeg.exe"),
    )
    try:
        planned = await controller.create_plan(
                VideoPlanCreateRequest(
                    analysis_job_id=analysis_id,
                    project_id="the-glitch-is-me",
                    gcp_project_id="test-project",
                gcs_bucket="example-trackprompt-video",
            )
        )
        await controller.authorize(
            planned.job_id,
            VideoAuthorizationRequest(confirmation=planned.authorization_phrase),
        )
        stored = store.get_video_job(planned.job_id)
        assert stored is not None
        source_clip = tmp_path / "source.mp4"
        source_clip.write_bytes(b"verified source clip")
        accepted = VideoShotAttempt(
            id="shot-001-attempt-01-accepted",
            attempt=1,
            idempotency_key="b" * 64,
            state=VideoShotState.VERIFIED,
            reserved_cost_usd=stored.shots[0].estimated_cost_usd,
            local_clip_path=str(source_clip),
            clip_sha256="c" * 64,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        source = stored.shots[0].model_copy(
            update={
                "attempts": (accepted,),
                "review_state": VideoReviewState.ACCEPTED,
                "accepted_attempt_id": accepted.id,
            }
        )
        store.put_video_job(stored.model_copy(update={"shots": (source, *stored.shots[1:])}))
        chained = await controller.chain_reference(
            planned.job_id,
            "shot-002",
            VideoChainReferenceRequest(source_shot_id="shot-001"),
        )
        assert chained.state is VideoJobState.PLANNED
        assert chained.plan_digest != planned.plan_digest
        assert chained.authorization_expires_at is None
        assert chained.shots[1].continuation_mode == "accepted-previous-shot-end-frame"
        assert chained.shots[1].reference_asset_id
        preview = controller.request_preview(chained.job_id)
        assert preview.requests[1]["instances"][0]["image"]["mimeType"] == "image/png"
        serialized_plan = json.dumps(store.get_video_job(chained.job_id).plan)
        assert str(source_clip) not in serialized_plan
    finally:
        controller.close()
        store.close()


@pytest.mark.asyncio
async def test_mission_control_owns_exact_batch_smoke_first_and_persists_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_root = tmp_path / "state" / "mission-control"
    analysis_id = str(uuid4())
    (state_root.parent / "jobs" / analysis_id).mkdir(parents=True)
    store = MissionControlStore(state_root / "mission-control.sqlite3")
    submitted: list[str] = []

    class FakeProvider:
        async def submit(self, shot, *, context):  # type: ignore[no-untyped-def]
            submitted.append(shot.shot_id)
            return {"name": f"operations/{shot.shot_id}"}

        async def fetch(self, *, model_id: str, operation_name: str, context) -> dict[str, object]:  # type: ignore[no-untyped-def]
            shot_id = operation_name.rsplit("/", 1)[-1]
            job = store.list_video_jobs(limit=1)[0]
            shot = next(item for item in job.plan["shots"] if item["shotId"] == shot_id)
            return {"done": True, "response": {"videos": [{"gcsUri": f"{shot['storageUri']}result.mp4"}]}}

        async def download(self, uri: str, destination: Path) -> None:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"fake-video")

        async def upload_reference(self, source: Path, destination_uri: str, expected_sha256: str) -> None:
            raise AssertionError("no reference upload was planned")

    def fake_verify(path: Path, **_: object) -> MediaProbe:
        return MediaProbe(
            path=str(path),
            sha256="a" * 64,
            duration_seconds=8,
            width=1920,
            height=1080,
            fps=24,
            codec="h264",
            pixel_format="yuv420p",
            has_audio=False,
        )

    monkeypatch.setattr("app.video_generation.mission_controller.verify_generated_clip", fake_verify)
    sequences: list[int] = []

    async def notify(sequence: int) -> None:
        sequences.append(sequence)

    controller = VideoGenerationController(
        repository_root=PACKAGE_ROOT,
        state_root=state_root,
        store=store,
        notify_event=notify,
        provider_factory=lambda _project, _region: FakeProvider(),
        poll_interval_seconds=0.01,
    )
    try:
        planned = await controller.create_plan(
            VideoPlanCreateRequest(
                analysis_job_id=analysis_id,
                project_id="the-glitch-is-me",
                profile_id="fast-1080p",
                gcp_project_id="test-gcp-project",
                gcs_bucket="example-trackprompt-video",
            )
        )
        assert submitted == []
        with pytest.raises(Exception, match="exact displayed"):
            await controller.start(planned.job_id)
        stored = store.get_video_job(planned.job_id)
        assert stored is not None and stored.authorization is None

        authorized = await controller.authorize(
            planned.job_id,
            VideoAuthorizationRequest(confirmation=planned.authorization_phrase),
        )
        assert planned.authorization_phrase not in json.dumps(
            store.get_video_job(planned.job_id).authorization
        )
        started = await controller.start(authorized.job_id)
        assert started.state is VideoJobState.SMOKE_SUBMITTED
        task = controller._tasks[started.job_id]
        await asyncio.wait_for(task, timeout=5)

        completed = controller.get(started.job_id)
        assert completed.state is VideoJobState.REVIEW_READY
        assert completed.verified_shot_count == 16
        assert submitted == [f"shot-{index:03d}" for index in range(1, 17)]
        replay = store.events_after(0, job_id=started.job_id)
        assert replay
        assert [event.sequence for event in replay] == sorted(event.sequence for event in replay)
        assert sequences[-1] == replay[-1].sequence
    finally:
        controller.close()
        store.close()
