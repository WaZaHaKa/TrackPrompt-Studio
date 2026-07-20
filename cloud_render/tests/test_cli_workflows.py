from __future__ import annotations

import json
from pathlib import Path

import pytest

from cloud_render.cli import main
from cloud_render.manifests import CHUNK_OUTPUT_KIND, PACKAGE_KIND, SCHEMA_VERSION, seal_manifest
from cloud_render.storage.base import sha256_path
from cloud_render.worker.mock import _write_png

SCENE_SHA = "A" * 64
PROFILE_SHA = "B" * 64
PACKAGE_SHA = "C" * 64


def _read_cli(capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    return json.loads(capsys.readouterr().out)


def _unsigned_package() -> dict[str, object]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": PACKAGE_KIND,
        "privateAudioIncluded": False,
        "audioMuxLocation": "LOCAL_ONLY",
        "identities": {
            "sceneSha256": SCENE_SHA,
            "profileSha256": PROFILE_SHA,
            "packageSha256": PACKAGE_SHA,
        },
        "frameRange": {"start": 1, "end": 2},
        "files": [{"path": "scene.blend", "sha256": "D" * 64, "sizeBytes": 1}],
        "blenderVersion": "5.2.0",
        "resolution": {"width": 2, "height": 2},
        "image": {"format": "PNG", "extension": "png", "bitDepth": 8},
    }


def _identity_arguments() -> list[str]:
    return [
        "--scene-sha",
        SCENE_SHA,
        "--profile-sha",
        PROFILE_SHA,
        "--package-sha",
        PACKAGE_SHA,
    ]


def test_package_scheduler_and_mock_worker_cli_round_trip(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    unsigned_path = tmp_path / "package.unsigned.json"
    manifest_path = tmp_path / "package.json"
    database = tmp_path / "scheduler.sqlite3"
    storage = tmp_path / "storage"
    unsigned_path.write_text(json.dumps(_unsigned_package()), encoding="utf-8")

    assert main(["seal-manifest", "--input", str(unsigned_path), "--output", str(manifest_path)]) == 0
    assert _read_cli(capsys)["ok"] is True
    assert main(["validate-manifest", "--path", str(manifest_path)]) == 0
    assert _read_cli(capsys)["data"]["frameRange"] == {"start": 1, "end": 2}

    init_arguments = [
        "scheduler-init",
        "--database",
        str(database),
        "--job-id",
        "job-cli",
        *_identity_arguments(),
        "--frame-start",
        "1",
        "--frame-end",
        "2",
        "--frames-per-chunk",
        "2",
    ]
    assert main(init_arguments) == 0
    assert _read_cli(capsys)["data"]["chunkCount"] == 1

    assert main(
        [
            "mock-worker",
            "--package-manifest",
            str(manifest_path),
            "--database",
            str(database),
            "--storage-root",
            str(storage),
            "--job-id",
            "job-cli",
            "--worker-id",
            "mock-1",
            "--run-until-idle",
        ]
    ) == 0
    worker = _read_cli(capsys)
    assert worker["data"]["results"][0]["outcome"] == "COMPLETED_CHUNK"

    assert main(
        ["scheduler-status", "--database", str(database), "--job-id", "job-cli"]
    ) == 0
    status = _read_cli(capsys)["data"]
    assert status["complete"] is True
    assert status["published_frames"] == 2


def test_tournament_and_media_plans_are_machine_readable(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    tournament = tmp_path / "tournament.json"
    tournament.write_text(
        json.dumps(
            {
                "benchmarks": [
                    {
                        "provider": "brev",
                        "offerId": "h200",
                        "gpuName": "H200",
                        "hourlyPrice": "4.00",
                        "vramGiB": "141",
                        "secondsPerFrame": "2.0",
                        "p90SecondsPerFrame": "2.2",
                        "validatedFrames": 100,
                        "visualPassed": True,
                        "technicalPassed": True,
                    },
                    {
                        "provider": "brev",
                        "offerId": "l40s",
                        "gpuName": "L40S",
                        "hourlyPrice": "1.00",
                        "vramGiB": "48",
                        "secondsPerFrame": "3.0",
                        "p90SecondsPerFrame": "3.3",
                        "validatedFrames": 100,
                        "visualPassed": True,
                        "technicalPassed": True,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    assert main(["tournament-rank", "--input", str(tournament)]) == 0
    ranked = _read_cli(capsys)["data"]["ranked"]
    assert [entry["benchmark"]["offer"]["offer_id"] for entry in ranked] == [
        "l40s",
        "h200",
    ]

    assert main(
        [
            "encode-plan",
            "--frame-pattern",
            "frames/frame_%06d.png",
            "--frame-start",
            "1",
            "--frame-end",
            "2",
            "--verified-frames",
            "1,2",
            "--fps",
            "30",
            "--output",
            "video-only.mp4",
        ]
    ) == 0
    encode = _read_cli(capsys)["data"]
    assert encode["audioIncluded"] is False
    assert "-an" in encode["arguments"]
    assert "-shortest" not in encode["arguments"]

    verified_file = tmp_path / "verified-frames.json"
    verified_file.write_text("[1, 2]", encoding="utf-8")
    assert main(
        [
            "encode-plan",
            "--frame-pattern",
            "frames/frame_%06d.png",
            "--frame-start",
            "1",
            "--frame-end",
            "2",
            "--verified-frames-file",
            str(verified_file),
            "--fps",
            "30",
            "--output",
            "video-from-file.mp4",
        ]
    ) == 0
    assert _read_cli(capsys)["data"]["frameCount"] == 2

    assert main(
        [
            "mux-plan",
            "--video-only",
            "video-only.mp4",
            "--private-audio",
            "private.wav",
            "--output",
            "final.mp4",
        ]
    ) == 0
    mux = _read_cli(capsys)["data"]
    assert mux["audioLocation"] == "LOCAL_ONLY"
    assert mux["shortestAllowed"] is False
    assert "-shortest" not in mux["arguments"]


def test_import_return_cli_validates_then_publishes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    returned = tmp_path / "returned"
    frame_path = returned / "frames" / "frame_000001.png"
    frame_path.parent.mkdir(parents=True)
    _write_png(frame_path, 2, 2, 8, 1)
    manifest = seal_manifest(
        {
            "schemaVersion": SCHEMA_VERSION,
            "kind": CHUNK_OUTPUT_KIND,
            "sceneSha256": SCENE_SHA,
            "profileSha256": PROFILE_SHA,
            "packageSha256": PACKAGE_SHA,
            "privateAudioUsed": False,
            "encodingPerformed": False,
            "frames": [
                {
                    "frame": 1,
                    "objectKey": "frames/frame_000001.png",
                    "sha256": sha256_path(frame_path),
                    "sizeBytes": frame_path.stat().st_size,
                }
            ],
        }
    )
    manifest_path = tmp_path / "chunk-output.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    package = _unsigned_package()
    package["frameRange"] = {"start": 1, "end": 1}
    image = package["image"]
    assert isinstance(image, dict)
    image["colorMode"] = "RGB"
    package_path = tmp_path / "cloud-package.json"
    package_path.write_text(json.dumps(seal_manifest(package)), encoding="utf-8")
    output_frames = tmp_path / "published"

    assert main(
        [
            "import-return",
            "--returned",
            str(returned),
            "--quarantine-root",
            str(tmp_path / "quarantine"),
            "--output-frames",
            str(output_frames),
            "--manifest",
            str(manifest_path),
            "--package-manifest",
            str(package_path),
        ]
    ) == 0
    result = _read_cli(capsys)["data"]
    assert result["publishedFrames"] == [1]
    assert result["conflicts"] == []
    assert (output_frames / "frame_000001.png").is_file()


def test_import_return_cli_derives_chunk_bounds_inside_larger_package(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    returned = tmp_path / "returned"
    frames_directory = returned / "frames"
    frames_directory.mkdir(parents=True)
    frame_records: list[dict[str, object]] = []
    for frame in (3, 4):
        path = frames_directory / f"frame_{frame:06d}.png"
        _write_png(path, 2, 2, 8, frame)
        frame_records.append(
            {
                "frame": frame,
                "objectKey": f"frames/{path.name}",
                "sha256": sha256_path(path),
                "sizeBytes": path.stat().st_size,
            }
        )
    returned_manifest = seal_manifest(
        {
            "schemaVersion": SCHEMA_VERSION,
            "kind": CHUNK_OUTPUT_KIND,
            "sceneSha256": SCENE_SHA,
            "profileSha256": PROFILE_SHA,
            "packageSha256": PACKAGE_SHA,
            "privateAudioUsed": False,
            "encodingPerformed": False,
            "frames": frame_records,
        }
    )
    returned_manifest_path = tmp_path / "chunk-output.json"
    returned_manifest_path.write_text(json.dumps(returned_manifest), encoding="utf-8")

    package = _unsigned_package()
    package["frameRange"] = {"start": 1, "end": 6}
    image = package["image"]
    assert isinstance(image, dict)
    image["colorMode"] = "RGB"
    package_path = tmp_path / "cloud-package.json"
    package_path.write_text(json.dumps(seal_manifest(package)), encoding="utf-8")
    output_frames = tmp_path / "published"

    assert main(
        [
            "import-return",
            "--returned",
            str(returned),
            "--quarantine-root",
            str(tmp_path / "quarantine"),
            "--output-frames",
            str(output_frames),
            "--manifest",
            str(returned_manifest_path),
            "--package-manifest",
            str(package_path),
        ]
    ) == 0
    result = _read_cli(capsys)["data"]
    assert result["publishedFrames"] == [3, 4]
    assert sorted(path.name for path in output_frames.iterdir()) == [
        "frame_000003.png",
        "frame_000004.png",
    ]


def test_import_return_cli_rejects_frame_header_package_mismatch(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    returned = tmp_path / "returned"
    frame_path = returned / "frames" / "frame_000001.png"
    frame_path.parent.mkdir(parents=True)
    _write_png(frame_path, 3, 2, 8, 1)
    returned_manifest = seal_manifest(
        {
            "schemaVersion": SCHEMA_VERSION,
            "kind": CHUNK_OUTPUT_KIND,
            "sceneSha256": SCENE_SHA,
            "profileSha256": PROFILE_SHA,
            "packageSha256": PACKAGE_SHA,
            "privateAudioUsed": False,
            "encodingPerformed": False,
            "frames": [
                {
                    "frame": 1,
                    "objectKey": "frames/frame_000001.png",
                    "sha256": sha256_path(frame_path),
                    "sizeBytes": frame_path.stat().st_size,
                }
            ],
        }
    )
    returned_manifest_path = tmp_path / "chunk-output.json"
    returned_manifest_path.write_text(json.dumps(returned_manifest), encoding="utf-8")
    package = _unsigned_package()
    package["frameRange"] = {"start": 1, "end": 1}
    image = package["image"]
    assert isinstance(image, dict)
    image["colorMode"] = "RGB"
    package_path = tmp_path / "cloud-package.json"
    package_path.write_text(json.dumps(seal_manifest(package)), encoding="utf-8")

    assert main(
        [
            "import-return",
            "--returned",
            str(returned),
            "--quarantine-root",
            str(tmp_path / "quarantine"),
            "--output-frames",
            str(tmp_path / "published"),
            "--manifest",
            str(returned_manifest_path),
            "--package-manifest",
            str(package_path),
        ]
    ) == 2
    error = _read_cli(capsys)
    assert "header differs" in error["error"]["message"]
    assert not (tmp_path / "published" / "frame_000001.png").exists()
