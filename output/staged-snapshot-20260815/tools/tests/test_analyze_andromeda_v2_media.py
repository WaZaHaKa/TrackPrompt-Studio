from __future__ import annotations

import hashlib
import struct
import subprocess
import zlib
from pathlib import Path
from typing import Any

import pytest
from tools import analyze_andromeda_v2_media as qa


def _chunk(kind: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(payload, zlib.crc32(kind)) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)


def _png(width: int, height: int, value: int = 96) -> bytes:
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    rows = b"".join(b"\0" + bytes([value, value, value]) * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(rows))
        + _chunk(b"IEND", b"")
    )


def _probe(*, frame_count: int = 3, codec: str = "h264") -> dict[str, Any]:
    return {
        "videoPresent": True,
        "audioPresent": True,
        "videoCodec": codec,
        "pixelFormat": "yuv420p",
        "width": 4,
        "height": 1,
        "fps": 30.0,
        "frameCount": frame_count,
        "videoDurationSeconds": frame_count / 30,
        "audioCodec": "aac",
        "audioSampleRate": 44_100,
        "audioChannels": 2,
        "audioDurationSeconds": frame_count / 30,
        "formatDurationSeconds": frame_count / 30,
        "formatName": "mov,mp4,m4a,3gp,3g2,mj2",
    }


def _fixture(tmp_path: Path, frame_numbers: tuple[int, ...] = (1, 2, 3)) -> dict[str, Path]:
    frames = tmp_path / "frames"
    frames.mkdir()
    for frame in frame_numbers:
        (frames / f"frame_{frame:06d}.png").write_bytes(_png(4, 1, 80 + frame))
    media = tmp_path / "animatic.mp4"
    media.write_bytes(b"synthetic encoded media fixture")
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffmpeg.write_bytes(b"synthetic ffmpeg fixture")
    ffprobe = tmp_path / "ffprobe.exe"
    ffprobe.write_bytes(b"synthetic ffprobe fixture")
    return {
        "frames": frames,
        "media": media,
        "ffmpeg": ffmpeg,
        "ffprobe": ffprobe,
        "output": tmp_path / "qa.json",
    }


def _rgb(_ffmpeg: Path, _image: Path, width: int, height: int) -> bytes:
    row = bytes([32, 32, 32, 96, 96, 96, 160, 160, 160, 220, 220, 220])
    return (row * height)[: width * height * 3]


def _analyze(paths: dict[str, Path], *, frame_end: int = 3) -> dict[str, Any]:
    return qa.analyze_andromeda_v2_media(
        frame_directory=paths["frames"],
        encoded_media=paths["media"],
        ffmpeg=paths["ffmpeg"],
        ffprobe=paths["ffprobe"],
        output=paths["output"],
        expected_width=4,
        expected_height=1,
        expected_fps=30.0,
        frame_start=1,
        frame_end=frame_end,
        review_frames=(1, frame_end),
    )


def test_media_qa_passes_exact_sequence_and_is_deterministic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _fixture(tmp_path)
    monkeypatch.setattr(qa, "_probe_media", lambda _probe_path, _media: _probe())
    monkeypatch.setattr(qa, "_decode_rgb", _rgb)

    first = _analyze(paths)
    first_hash = hashlib.sha256(paths["output"].read_bytes()).hexdigest()
    second = _analyze(paths)
    second_hash = hashlib.sha256(paths["output"].read_bytes()).hexdigest()

    assert first == second
    assert first_hash == second_hash
    assert first["technicalPass"] is True
    assert first["humanArtisticApproval"] is False
    assert first["humanReviewRequired"] is True
    assert first["frameSequence"]["observedCount"] == 3
    assert first["frameSequence"]["filenamePrefix"] == "frame_"
    assert len(first["frameSequence"]["sequenceSha256"]) == 64
    assert all(item["sha256"] for item in first["frameSequence"]["frames"])
    assert len(first["reviewFrames"]) == 2


def test_media_qa_reports_missing_png_without_hiding_other_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _fixture(tmp_path, (1, 3))
    monkeypatch.setattr(qa, "_probe_media", lambda _probe_path, _media: _probe())
    monkeypatch.setattr(qa, "_decode_rgb", _rgb)

    payload = _analyze(paths)

    assert payload["technicalPass"] is False
    assert payload["frameSequence"]["missingFrames"] == [2]
    checks = {item["id"]: item["pass"] for item in payload["checks"]}
    assert checks["png-exact-contiguous-frame-range"] is False
    assert checks["encoded-media-video-contract"] is True


def test_media_qa_reports_corrupt_png_and_codec_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _fixture(tmp_path)
    corrupt = paths["frames"] / "frame_000002.png"
    payload = bytearray(corrupt.read_bytes())
    payload[-5] ^= 0xFF
    corrupt.write_bytes(payload)
    monkeypatch.setattr(
        qa,
        "_probe_media",
        lambda _probe_path, _media: _probe(codec="hevc"),
    )
    monkeypatch.setattr(qa, "_decode_rgb", _rgb)

    result = _analyze(paths)

    assert result["technicalPass"] is False
    frames = {item["frame"]: item for item in result["frameSequence"]["frames"]}
    assert frames[2]["integrity_pass"] is False
    assert "CRC" in frames[2]["error"]
    checks = {item["id"]: item["pass"] for item in result["checks"]}
    assert checks["png-integrity-and-dimensions"] is False
    assert checks["encoded-media-video-contract"] is False


def test_media_qa_labels_exposure_diagnostics_as_technical_not_artistic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _fixture(tmp_path)
    monkeypatch.setattr(qa, "_probe_media", lambda _probe_path, _media: _probe())
    monkeypatch.setattr(
        qa,
        "_decode_rgb",
        lambda _ffmpeg, _image, width, height: bytes([0, 0, 0] * width * height),
    )

    payload = _analyze(paths)

    assert payload["technicalPass"] is False
    assert payload["humanArtisticApproval"] is False
    assert "near-black-fraction-exceeds-review-limit" in payload["reviewFrames"][0]["findings"]
    assert "luminance-contrast-span-is-weak" in payload["reviewFrames"][0]["findings"]


def test_sparse_subject_contrast_can_pass_when_global_p10_p90_is_flat() -> None:
    pixels = bytearray([32, 32, 32] * 100)
    pixels[-6:] = bytes([240, 240, 240] * 2)

    diagnostics = qa._diagnostics(bytes(pixels), 10, 10)  # noqa: SLF001
    findings = qa._diagnostic_findings(diagnostics, diagnostics)  # noqa: SLF001

    assert diagnostics["contrast"]["p90MinusP10"] == pytest.approx(0.0)
    assert diagnostics["contrast"]["p995MinusP005"] > 0.20
    assert "luminance-contrast-span-is-weak" not in findings


def test_media_qa_rejects_non_explicit_review_frame_contract(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)

    with pytest.raises(qa.MediaQaError, match="without duplicates"):
        qa.analyze_andromeda_v2_media(
            frame_directory=paths["frames"],
            encoded_media=paths["media"],
            ffmpeg=paths["ffmpeg"],
            ffprobe=paths["ffprobe"],
            output=paths["output"],
            expected_width=4,
            expected_height=1,
            expected_fps=30.0,
            frame_start=1,
            frame_end=3,
            review_frames=(1, 1),
        )


def test_run_bounded_uses_argument_array_shell_false_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, Any] = {}

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        observed["command"] = command
        observed.update(kwargs)
        kwargs["stdout"].write(b"ok")
        kwargs["stderr"].write(b"")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = qa._run_bounded(  # noqa: SLF001
        ["tool.exe", "--safe-argument"],
        timeout_seconds=7,
        stdout_limit=16,
        stderr_limit=8,
    )

    assert result == qa.ProcessResult(returncode=0, stdout=b"ok", stderr=b"")
    assert observed["command"] == ["tool.exe", "--safe-argument"]
    assert observed["shell"] is False
    assert observed["timeout"] == 7
    assert observed["stdin"] == subprocess.DEVNULL
