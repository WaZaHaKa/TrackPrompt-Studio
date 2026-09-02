from __future__ import annotations

import hashlib
import json
import struct
import zlib
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from app.renderers.wzhk_spectrum.generative import composition_review as review
from app.renderers.wzhk_spectrum.production import SpectrumProductionError, review_frame_timestamps
from app.subprocess_utils import BoundedProcessResult, ProcessTimedOut


def _composition() -> dict[str, Any]:
    return {
        "schemaVersion": "1.0.0",
        "revision": "scattered-geometry-first-3.7",
        "geometryCoverage": "full-frame",
        "production": {
            "logoVisible": True, "artistVisible": True, "titleVisible": True,
            "spectrumBarsVisible": False, "spectralRibbonVisible": False,
            "technicalMetadataVisible": False, "sectionLabelsVisible": False,
        },
        "readability": {
            "mode": "soft-ellipses", "minimumBrightness": 0.42, "haloSuppression": 0.72,
            "zones": [
                {"id": "logo", "center": [0.1073, 0.1722], "radius": [0.092, 0.145], "strength": 0.46},
                {"id": "identity", "center": [0.162, 0.402], "radius": [0.177, 0.095], "strength": 0.62},
            ],
        },
        "framing": {"center": [0.55, 0.54], "shapeScale": 1.0, "depthStrength": 0.75},
        "envelope": [
            {"timeSeconds": 0.0, "density": 0.3, "brightness": 0.3, "scale": 0.9, "deformation": 0.1},
            {"timeSeconds": 196.619796, "density": 0.0, "brightness": 0.0, "scale": 0.8, "deformation": 0.0},
        ],
    }


def _json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _chunk(name: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data))


def _png(path: Path, pixels: review.ReviewPixels, *, compression: int = 6) -> None:
    stride = pixels.width * 3
    uncompressed = b"".join(
        b"\x00" + pixels.rgb[row * stride:(row + 1) * stride]
        for row in range(pixels.height)
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", pixels.width, pixels.height, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(uncompressed, compression))
        + _chunk(b"IEND", b"")
    )


def _decode_fixture_png(_ffmpeg_path: str, path: Path) -> review.ReviewPixels:
    content = path.read_bytes()
    width, height = struct.unpack(">II", content[16:24])
    offset = 8
    compressed = bytearray()
    while offset < len(content):
        size = struct.unpack(">I", content[offset:offset + 4])[0]
        kind = content[offset + 4:offset + 8]
        if kind == b"IDAT":
            compressed.extend(content[offset + 8:offset + 8 + size])
        offset += 12 + size
    decoded = zlib.decompress(compressed)
    stride = width * 3 + 1
    rgb = b"".join(decoded[row * stride + 1:(row + 1) * stride] for row in range(height))
    return review.ReviewPixels(width, height, rgb)


def _pixels(index: int, *, baseline: bool = False) -> review.ReviewPixels:
    rgb = np.zeros((540, 960, 3), dtype=np.uint8)
    rgb[:] = [7, 10, 18]
    if index != 8:
        color = [35, 185, 205] if index < 7 else [20, 75, 110]
        shift = (index * 3 + (7 if baseline else 0)) % 19
        for y in range(12 + shift, 530, 25):
            for x in range(12 + shift, 950, 27):
                rgb[y:y + 3, x:x + 3] = color
    # Synthetic identity-shaped content only; no private or recorded assets.
    rgb[48:112, 70:130] = [235, 240, 245]
    rgb[198:209, 45:190] = [225, 230, 235]
    rgb[227:241, 45:255] = [225, 230, 235]
    return review.ReviewPixels(960, 540, rgb.tobytes())


@dataclass
class _Fixture:
    current: Path
    baseline: Path
    video_pixels: dict[tuple[Path, float], review.ReviewPixels]

    def create(self) -> dict[str, Any]:
        return review.create_composition_review(
            "synthetic-ffmpeg", self.current, self.baseline,
            expected_baseline_sha256=_digest(self.baseline / "output/final.mp4"),
        )

    def validate(self, payload: dict[str, Any]) -> dict[str, Any]:
        return review.validate_composition_review("synthetic-ffmpeg", self.current, self.baseline, payload)


@pytest.fixture
def fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Fixture:
    roots = [
        tmp_path / "11111111-1111-4111-8111-111111111111",
        tmp_path / "22222222-2222-4222-8222-222222222222",
    ]
    video_pixels: dict[tuple[Path, float], review.ReviewPixels] = {}
    for source_index, root in enumerate(roots):
        video = root / "output/final.mp4"
        video.parent.mkdir(parents=True)
        video.write_bytes(f"synthetic-video-{source_index}".encode())
        config = {
            "mode": "production", "logoUrl": "/assets/logo", "composition": _composition(),
            "branding": {"enabled": True, "artist": "DJ WaZaHaKa", "title": "SCATTERED", "meta": ""},
            "developerLab": {"enabled": False, "previewOverride": None},
        }
        _json(root / "geometry/config/runtime-config.json", config)
        _json(root / "design.json", {"resolvedPreset": {"composition": config["composition"]}})
        artifacts: list[dict[str, Any]] = [{
            "artifactType": "final-video", "relativePath": "output/final.mp4",
            "sha256": _digest(video), "sizeBytes": video.stat().st_size,
        }]
        for index, (label, timestamp) in enumerate(review_frame_timestamps(196.619796)):
            path = root / f"output/review-frames/{label}.png"
            pixels = _pixels(index, baseline=source_index == 0)
            _png(path, pixels)
            video_pixels[(video, timestamp)] = pixels
            artifacts.append({
                "artifactType": "review-frame", "relativePath": path.relative_to(root).as_posix(),
                "sha256": _digest(path), "sizeBytes": path.stat().st_size, "timestampSeconds": timestamp,
            })
        _json(root / "manifest.json", {
            "jobId": root.name, "mode": "production", "state": "COMPLETE", "backgroundMode": "generative-geometry",
            "validationReport": {"valid": True}, "generatedWorkspaceHash": "b" * 64,
            "designHash": _digest(root / "design.json"), "masterTiming": {"masterDurationSeconds": 196.619796},
            "artifacts": artifacts, "visualQaRequired": True,
        })

    def decode_video(_ffmpeg_path: str, video: Path, timestamp: float) -> review.ReviewPixels:
        return video_pixels[(video, timestamp)]

    def comparison(_ffmpeg_path: str, left: Path, right: Path, target: Path) -> None:
        _png(target, review._comparison_pixels(
            _decode_fixture_png("", left), _decode_fixture_png("", right),
        ))

    monkeypatch.setattr(review, "_png_pixels", _decode_fixture_png)
    monkeypatch.setattr(review, "_video_pixels", decode_video)
    monkeypatch.setattr(review, "_create_comparison", comparison)
    return _Fixture(roots[1], roots[0], video_pixels)


def _source_update(root: Path, update: Any) -> None:
    path = root / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    update(manifest)
    _json(path, manifest)


def test_create_and_validate_nine_matched_frames_preserves_sources(fixture: _Fixture) -> None:
    originals = {path: _digest(path) for root in (fixture.current, fixture.baseline) for path in root.rglob("*") if path.is_file()}
    payload = fixture.create()
    validated = fixture.validate(payload)
    assert validated["valid"] is True
    assert validated["sourceLineageValid"] is True
    assert validated["matchedFrames"] == 9
    assert payload["frames"][-1]["timestampSeconds"] == pytest.approx(196.119796)
    assert payload["userAestheticApproval"] == "pending"
    assert "not proof that bars" in payload["evidenceLimits"]
    assert payload["extraction"]["lineageComparison"] == "decoded-rgb24-sha256-against-source-video"
    assert all(entry["comparison"]["width"] == 1920 and entry["comparison"]["height"] == 540 for entry in payload["frames"])
    assert all(_digest(path) == digest for path, digest in originals.items())
    assert (fixture.current / review.MANIFEST_RELATIVE_PATH).is_file()
    assert not (fixture.baseline / review.COMPARISON_DIRECTORY).exists()


def test_readback_is_deterministic_and_does_not_write(fixture: _Fixture) -> None:
    payload = fixture.create()
    paths = {path: (path.stat().st_mtime_ns, _digest(path)) for path in fixture.current.rglob("*") if path.is_file()}
    assert fixture.validate(payload) == fixture.validate(payload)
    assert all((path.stat().st_mtime_ns, _digest(path)) == prior for path, prior in paths.items())


def test_existing_evidence_is_never_overwritten(fixture: _Fixture) -> None:
    fixture.create()
    with pytest.raises(SpectrumProductionError, match="already exists"):
        fixture.create()


def test_expected_baseline_hash_is_required_and_checked(fixture: _Fixture) -> None:
    with pytest.raises(SpectrumProductionError, match="source video hash"):
        review.create_composition_review("fake", fixture.current, fixture.baseline, expected_baseline_sha256="0" * 64)
    assert not (fixture.current / review.COMPARISON_DIRECTORY).exists()


@pytest.mark.parametrize("source_name", ["current", "baseline"])
def test_changed_source_video_is_rejected(fixture: _Fixture, source_name: str) -> None:
    payload = fixture.create()
    root: Path = getattr(fixture, source_name)
    (root / "output/final.mp4").write_bytes(b"changed video")
    with pytest.raises(SpectrumProductionError, match="source video hash"):
        fixture.validate(payload)


def test_stale_frame_is_rejected_even_when_its_recorded_hash_is_updated(fixture: _Fixture) -> None:
    path = fixture.current / "output/review-frames/main-mid-0200.png"
    _png(path, _pixels(0))

    def update(manifest: dict[str, Any]) -> None:
        entry = next(item for item in manifest["artifacts"] if item["relativePath"] == "output/review-frames/main-mid-0200.png")
        entry.update(sha256=_digest(path), sizeBytes=path.stat().st_size)

    _source_update(fixture.current, update)
    with pytest.raises(SpectrumProductionError, match="stale"):
        fixture.create()


def test_png_compression_difference_does_not_break_pixel_lineage(fixture: _Fixture) -> None:
    path = fixture.current / "output/review-frames/main-mid-0200.png"
    _png(path, _pixels(3), compression=1)

    def update(manifest: dict[str, Any]) -> None:
        entry = next(item for item in manifest["artifacts"] if item["relativePath"] == "output/review-frames/main-mid-0200.png")
        entry.update(sha256=_digest(path), sizeBytes=path.stat().st_size)

    _source_update(fixture.current, update)
    assert fixture.validate(fixture.create())["valid"] is True


def test_missing_review_frame_is_rejected(fixture: _Fixture) -> None:
    (fixture.current / "output/review-frames/main-mid-0200.png").unlink()
    with pytest.raises(SpectrumProductionError, match="missing"):
        fixture.create()


@pytest.mark.parametrize("time", [None, True, float("nan"), 121.0])
def test_wrong_review_timestamp_is_rejected(fixture: _Fixture, time: object) -> None:
    def update(manifest: dict[str, Any]) -> None:
        entry = next(item for item in manifest["artifacts"] if item["relativePath"] == "output/review-frames/main-mid-0200.png")
        entry["timestampSeconds"] = time

    _source_update(fixture.current, update)
    with pytest.raises(SpectrumProductionError, match="timestamp"):
        fixture.create()


def test_mismatched_review_dimensions_are_rejected(fixture: _Fixture) -> None:
    path = fixture.current / "output/review-frames/main-mid-0200.png"
    double = review._comparison_pixels(_pixels(3), _pixels(3))
    _png(path, double)

    def update(manifest: dict[str, Any]) -> None:
        entry = next(item for item in manifest["artifacts"] if item["relativePath"] == "output/review-frames/main-mid-0200.png")
        entry.update(sha256=_digest(path), sizeBytes=path.stat().st_size)

    _source_update(fixture.current, update)
    with pytest.raises(SpectrumProductionError, match="stale"):
        fixture.create()


@pytest.mark.parametrize("field", ["spectrumBarsVisible", "spectralRibbonVisible", "technicalMetadataVisible", "sectionLabelsVisible"])
def test_production_diagnostic_flags_are_rejected(fixture: _Fixture, field: str) -> None:
    path = fixture.current / "geometry/config/runtime-config.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    config["composition"]["production"][field] = True
    _json(path, config)
    with pytest.raises(SpectrumProductionError, match="composition contract"):
        fixture.create()


def test_nonlocal_readability_mask_is_rejected(fixture: _Fixture) -> None:
    path = fixture.current / "geometry/config/runtime-config.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    config["composition"]["readability"]["zones"][0]["radius"] = [0.9, 0.9]
    _json(path, config)
    with pytest.raises(SpectrumProductionError, match="composition contract"):
        fixture.create()


@pytest.mark.parametrize("change", ["metadata", "missing-title", "preview"])
def test_production_identity_and_debug_separation_are_checked(fixture: _Fixture, change: str) -> None:
    path = fixture.current / "geometry/config/runtime-config.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    if change == "metadata":
        config["branding"]["meta"] = "120 BPM / 4/4"
    elif change == "missing-title":
        config["branding"].pop("title")
    else:
        config["developerLab"]["enabled"] = True
    _json(path, config)
    with pytest.raises(SpectrumProductionError, match="identity or preview"):
        fixture.create()


@pytest.mark.parametrize("mutation", ["source-hash", "frame-hash", "timestamp", "missing-frame", "extraction", "approval"])
def test_manifest_lineage_tampering_is_rejected(fixture: _Fixture, mutation: str) -> None:
    payload = fixture.create()
    changed = deepcopy(payload)
    if mutation == "source-hash":
        changed["baseline"]["video"]["sha256"] = "0" * 64
    elif mutation == "frame-hash":
        changed["frames"][0]["current"]["sha256"] = "0" * 64
    elif mutation == "timestamp":
        changed["frames"][0]["timestampSeconds"] = 11.0
    elif mutation == "missing-frame":
        changed["frames"].pop()
    elif mutation == "extraction":
        changed["extraction"]["filters"] = "scale=100:100"
    else:
        changed["userAestheticApproval"] = "approved"
    with pytest.raises(SpectrumProductionError):
        fixture.validate(changed)


def test_changed_comparison_is_rejected(fixture: _Fixture) -> None:
    payload = fixture.create()
    path = fixture.current / payload["frames"][0]["comparison"]["relativePath"]
    _png(path, review._comparison_pixels(_pixels(3), _pixels(3)))
    with pytest.raises(SpectrumProductionError, match="side-by-side"):
        fixture.validate(payload)


def test_geometry_first_sanity_does_not_reuse_spectrum_presence() -> None:
    config = {"composition": _composition()}
    frames = {label: _pixels(index) for index, (label, _) in enumerate(review_frame_timestamps(196.619796))}
    report = review.build_composition_visual_sanity(config, frames, {label: pixels.sha256 for label, pixels in frames.items()})
    assert report["valid"] is True
    checks = {check["id"]: check for check in report["checks"]}
    assert "spectrum-present" not in checks
    assert checks["geometry-spatial-occupancy"]["passed"] is True
    assert checks["tail-decays"]["passed"] is True
    assert set(checks["geometry-spatial-occupancy"]["measured"]) == {"left-outside-identity", "lower", "center", "right"}


def test_geometry_first_sanity_fails_for_identity_only_frames() -> None:
    frames = {label: _pixels(8) for label, _ in review_frame_timestamps(196.619796)}
    report = review.build_composition_visual_sanity(
        {"composition": _composition()}, frames, {label: pixels.sha256 for label, pixels in frames.items()},
    )
    checks = {check["id"]: check for check in report["checks"]}
    assert report["valid"] is False
    assert checks["geometry-spatial-occupancy"]["passed"] is False
    assert checks["timeline-changes"]["passed"] is False
    assert checks["tail-decays"]["passed"] is False


@pytest.mark.parametrize("failure", ["timeout", "overflow", "nonzero"])
def test_ffmpeg_inspection_is_bounded_and_fails_closed(monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    def run(args: list[str], **kwargs: Any) -> BoundedProcessResult:
        assert args == ["fake-ffmpeg"]
        assert kwargs["timeout_seconds"] == 60
        assert kwargs["stdout_limit"] == 100
        assert kwargs["stderr_limit"] == 64_000
        if failure == "timeout":
            raise ProcessTimedOut()
        return BoundedProcessResult(1 if failure == "nonzero" else 0, b"", b"", failure == "overflow", False)

    monkeypatch.setattr(review, "run_process_bounded", run)
    with pytest.raises(SpectrumProductionError):
        review._ffmpeg(["fake-ffmpeg"], stdout_limit=100)


@dataclass
class _RegistrationFixture:
    current: Path
    baseline: Path
    validation: dict[str, Any]
    calls: list[tuple[str, Path, Path, dict[str, Any]]]

    def register(self) -> dict[str, Any]:
        return review.register_composition_review("mock-ffmpeg", self.current, self.baseline)


@pytest.fixture
def registration_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _RegistrationFixture:
    # Registration tests mock the already separately tested RGB readback stage.
    # These tiny files exercise persistence only: no media decoding or rendering.
    current = tmp_path / "33333333-3333-4333-8333-333333333333"
    baseline = tmp_path / "44444444-4444-4444-8444-444444444444"
    baseline.mkdir()
    (baseline / "preserved.txt").write_bytes(b"immutable synthetic baseline")
    output = current / review.COMPARISON_DIRECTORY
    output.mkdir(parents=True)
    (current / "output/final.mp4").write_bytes(b"synthetic completed 3.7 final")
    _json(current / "design.json", {"composition": "synthetic registration only"})
    _json(current / review.SANITY_RELATIVE_PATH, {"valid": True, "userAestheticApproval": "pending"})
    final = review._artifact(
        current, current / "output/final.mp4", review.SpectrumArtifactType.FINAL_VIDEO,
        review.SpectrumProductionState.VALIDATING, "Existing final artifact retained unchanged",
    ).model_dump(mode="json", by_alias=True)
    _json(current / "manifest.json", {
        "jobId": current.name,
        "mode": "production",
        "state": "COMPLETE",
        "backgroundMode": "generative-geometry",
        "compositionRevision": "scattered-geometry-first-3.7",
        "validationReport": {"valid": True},
        "generatedWorkspaceHash": "c" * 64,
        "designHash": _digest(current / "design.json"),
        "masterTiming": {"masterDurationSeconds": 196.619796},
        "artifacts": [final],
        "visualQaRequired": True,
        "updatedAt": "preserved-existing-timestamp",
        "operatorNotes": {"retained": "opaque pre-existing metadata"},
    })
    frames: list[dict[str, Any]] = []
    for label, timestamp in review_frame_timestamps(196.619796):
        records = {}
        for directory, role in (("milestone-3.6", "baseline"), ("side-by-side", "comparison")):
            path = output / directory / f"{label}.png"
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(f"synthetic-{role}-{label}".encode())
            records[role] = review._file_record(current, path)
        frames.append({"label": label, "timestampSeconds": timestamp, **records})
    payload = {
        "frames": frames,
        "visualSanity": review._file_record(current, current / review.SANITY_RELATIVE_PATH),
    }
    _json(current / review.MANIFEST_RELATIVE_PATH, payload)
    validation = {"valid": True, "sourceLineageValid": True}
    calls: list[tuple[str, Path, Path, dict[str, Any]]] = []

    def validate(ffmpeg: str, job_root: Path, baseline_root: Path, supplied: dict[str, Any]) -> dict[str, Any]:
        calls.append((ffmpeg, job_root, baseline_root, supplied))
        return validation

    monkeypatch.setattr(review, "validate_composition_review", validate)
    return _RegistrationFixture(current, baseline, validation, calls)


def test_register_appends_twenty_typed_artifacts_and_preserves_existing_state(
    registration_fixture: _RegistrationFixture,
) -> None:
    fixture = registration_fixture
    before = json.loads((fixture.current / "manifest.json").read_text(encoding="utf-8"))
    originals = {
        path: _digest(path)
        for root in (fixture.current, fixture.baseline)
        for path in root.rglob("*")
        if path.is_file() and path != fixture.current / "manifest.json"
    }
    updated = fixture.register()
    assert len(fixture.calls) == 1
    assert fixture.calls[0][:3] == ("mock-ffmpeg", fixture.current, fixture.baseline)
    assert len(updated["artifacts"]) == len(before["artifacts"]) + 20
    assert updated["artifacts"][:len(before["artifacts"])] == before["artifacts"]
    assert {key: value for key, value in updated.items() if key != "artifacts"} == {
        key: value for key, value in before.items() if key != "artifacts"
    }
    for record in updated["artifacts"][len(before["artifacts"]):]:
        parsed = review.SpectrumArtifact.model_validate(record)
        assert parsed.created_state == review.SpectrumProductionState.COMPLETE
        assert parsed.relative_path.startswith(review.COMPARISON_DIRECTORY + "/")
    kinds = [item["artifactType"] for item in updated["artifacts"][len(before["artifacts"]):]]
    assert kinds.count("comparison-manifest") == 1
    assert kinds.count("visual-sanity-report") == 1
    assert kinds.count("comparison-frame") == 18
    assert all(_digest(path) == digest for path, digest in originals.items())
    assert updated["visualQaRequired"] is True


def test_register_exact_replay_validates_but_does_not_write(
    registration_fixture: _RegistrationFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = registration_fixture
    first = fixture.register()
    path = fixture.current / "manifest.json"
    before = (_digest(path), path.stat().st_mtime_ns)

    def no_write(_path: Path, _value: Any) -> None:
        pytest.fail("Exact registration replay must not write")

    monkeypatch.setattr(review, "_atomic_json", no_write)
    assert fixture.register() == first
    assert len(fixture.calls) == 2
    assert (_digest(path), path.stat().st_mtime_ns) == before


def test_register_conflicting_same_path_fails_without_replacing_any_records(
    registration_fixture: _RegistrationFixture,
) -> None:
    fixture = registration_fixture
    fixture.register()
    path = fixture.current / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    target = next(item for item in manifest["artifacts"] if item["artifactType"] == "comparison-manifest")
    target["provenance"] = "Different pre-existing same-path provenance"
    _json(path, manifest)
    before = (_digest(path), path.stat().st_mtime_ns)
    with pytest.raises(SpectrumProductionError, match="same-path"):
        fixture.register()
    assert (_digest(path), path.stat().st_mtime_ns) == before


@pytest.mark.parametrize("validation", [
    {"valid": False, "sourceLineageValid": True},
    {"valid": True, "sourceLineageValid": False},
    {"valid": 1, "sourceLineageValid": True},
    {"valid": True},
])
def test_register_requires_successful_visual_and_lineage_validation(
    registration_fixture: _RegistrationFixture,
    validation: dict[str, Any],
) -> None:
    fixture = registration_fixture
    fixture.validation.clear()
    fixture.validation.update(validation)
    path = fixture.current / "manifest.json"
    before = (_digest(path), path.stat().st_mtime_ns)
    with pytest.raises(SpectrumProductionError, match="requires valid visual"):
        fixture.register()
    assert (_digest(path), path.stat().st_mtime_ns) == before


@pytest.mark.parametrize("field,value", [
    ("state", "FAILED"),
    ("compositionRevision", "scattered-milestone-3.6"),
    ("visualQaRequired", False),
])
def test_register_rejects_noncomplete_legacy_or_approval_changed_jobs(
    registration_fixture: _RegistrationFixture,
    field: str,
    value: object,
) -> None:
    fixture = registration_fixture
    _source_update(fixture.current, lambda manifest: manifest.update({field: value}))
    path = fixture.current / "manifest.json"
    before = (_digest(path), path.stat().st_mtime_ns)
    with pytest.raises(SpectrumProductionError):
        fixture.register()
    assert fixture.calls == []
    assert (_digest(path), path.stat().st_mtime_ns) == before


def test_register_rejects_evidence_changed_after_validation(
    registration_fixture: _RegistrationFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = registration_fixture
    path = fixture.current / "manifest.json"
    before = (_digest(path), path.stat().st_mtime_ns)

    def validate(_ffmpeg: str, _current: Path, _baseline: Path, _payload: dict[str, Any]) -> dict[str, Any]:
        (fixture.current / review.SANITY_RELATIVE_PATH).write_bytes(b"changed during validation")
        return {"valid": True, "sourceLineageValid": True}

    monkeypatch.setattr(review, "validate_composition_review", validate)
    with pytest.raises(SpectrumProductionError, match="evidence changed"):
        fixture.register()
    assert (_digest(path), path.stat().st_mtime_ns) == before


def test_register_rejects_job_changed_during_validation_without_overwriting_it(
    registration_fixture: _RegistrationFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = registration_fixture

    def validate(_ffmpeg: str, _current: Path, _baseline: Path, _payload: dict[str, Any]) -> dict[str, Any]:
        _source_update(fixture.current, lambda manifest: manifest.update(operatorNotes={"changed": "retain this"}))
        return {"valid": True, "sourceLineageValid": True}

    monkeypatch.setattr(review, "validate_composition_review", validate)
    with pytest.raises(SpectrumProductionError, match="manifest changed"):
        fixture.register()
    manifest = json.loads((fixture.current / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["operatorNotes"] == {"changed": "retain this"}
    assert len(manifest["artifacts"]) == 1
