from __future__ import annotations

from pathlib import Path

import pytest

from cloud_render.imports import (
    ReturnImportError,
    import_quarantined_return,
    quarantine_return,
)
from cloud_render.manifests import CHUNK_OUTPUT_KIND, SCHEMA_VERSION, seal_manifest
from cloud_render.media import (
    MediaPlanError,
    plan_cloud_video_only_encode,
    plan_local_audio_mux,
)
from cloud_render.models import FrameRange, IdentityBundle
from cloud_render.storage.base import sha256_path


def test_cloud_encode_is_video_only_exact_count_and_no_shortest(tmp_path: Path) -> None:
    plan = plan_cloud_video_only_encode(
        ffmpeg="ffmpeg",
        frame_pattern="frames/frame_%06d.png",
        frame_range=FrameRange(1, 3),
        verified_frames=[1, 2, 3],
        fps=30,
        output=tmp_path / "video-only.mp4",
    )
    assert plan.audio_included is False
    assert "-an" in plan.arguments
    assert "-shortest" not in plan.arguments
    assert plan.arguments[plan.arguments.index("-frames:v") + 1] == "3"


def test_cloud_encode_rejects_incomplete_sequence(tmp_path: Path) -> None:
    with pytest.raises(MediaPlanError, match="incomplete"):
        plan_cloud_video_only_encode(
            ffmpeg="ffmpeg",
            frame_pattern="frame_%06d.png",
            frame_range=FrameRange(1, 3),
            verified_frames=[1, 3],
            fps=30,
            output=tmp_path / "video.mp4",
        )


def test_private_audio_mux_is_explicitly_local_and_not_shortened(tmp_path: Path) -> None:
    audio = tmp_path / "private.wav"
    plan = plan_local_audio_mux(
        ffmpeg="ffmpeg",
        video_only_input=tmp_path / "video.mp4",
        private_audio_input=audio,
        output=tmp_path / "delivery.mp4",
    )
    assert plan.audio_location == "LOCAL_ONLY"
    assert plan.shortest_allowed is False
    assert str(audio) in plan.arguments
    assert "-shortest" not in plan.arguments


def _return_manifest(identities: IdentityBundle, root: Path, digest: str) -> dict[str, object]:
    return seal_manifest(
        {
            "schemaVersion": SCHEMA_VERSION,
            "kind": CHUNK_OUTPUT_KIND,
            "sceneSha256": identities.scene_sha256,
            "profileSha256": identities.profile_sha256,
            "packageSha256": identities.package_sha256,
            "privateAudioUsed": False,
            "encodingPerformed": False,
            "frames": [
                {
                    "frame": 1,
                    "objectKey": root.as_posix(),
                    "sha256": digest,
                    "sizeBytes": 5,
                }
            ],
        }
    )


def _nonempty(path: Path, _frame: int) -> None:
    if not path.is_file() or path.stat().st_size <= 0:
        raise ReturnImportError("frame is empty")


def test_return_is_quarantined_validated_and_atomically_published(
    tmp_path: Path,
    identities: IdentityBundle,
) -> None:
    returned = tmp_path / "returned"
    frame = returned / "objects" / "frame_000001.png"
    frame.parent.mkdir(parents=True)
    frame.write_bytes(b"frame")
    manifest = _return_manifest(identities, Path("objects/frame_000001.png"), sha256_path(frame))
    result = import_quarantined_return(
        returned=returned,
        quarantine_root=tmp_path / "quarantine",
        output_frames=tmp_path / "output",
        manifest=manifest,
        identities=identities,
        frame_range=FrameRange(1, 1),
        extension="png",
        validate_frame=_nonempty,
    )
    assert result.published_frames == (1,)
    assert result.quarantine.is_dir()
    assert (tmp_path / "output" / "frame_000001.png").read_bytes() == b"frame"


def test_return_conflict_preserves_local_and_quarantines_both_candidates(
    tmp_path: Path,
    identities: IdentityBundle,
) -> None:
    returned = tmp_path / "returned"
    frame = returned / "objects" / "frame_000001.png"
    frame.parent.mkdir(parents=True)
    frame.write_bytes(b"cloud")
    output = tmp_path / "output"
    output.mkdir()
    local = output / "frame_000001.png"
    local.write_bytes(b"local")
    manifest = _return_manifest(identities, Path("objects/frame_000001.png"), sha256_path(frame))
    result = import_quarantined_return(
        returned=returned,
        quarantine_root=tmp_path / "quarantine",
        output_frames=output,
        manifest=manifest,
        identities=identities,
        frame_range=FrameRange(1, 1),
        extension="png",
        validate_frame=_nonempty,
    )
    assert result.published_frames == ()
    assert result.conflicts[0].local_preferred is True
    assert local.read_bytes() == b"local"


def test_return_rejects_identity_drift(tmp_path: Path, identities: IdentityBundle) -> None:
    returned = tmp_path / "returned"
    frame = returned / "objects" / "frame_000001.png"
    frame.parent.mkdir(parents=True)
    frame.write_bytes(b"frame")
    wrong = IdentityBundle("D" * 64, "E" * 64, "F" * 64)
    manifest = _return_manifest(wrong, Path("objects/frame_000001.png"), sha256_path(frame))
    with pytest.raises(ReturnImportError, match="does not match"):
        import_quarantined_return(
            returned=returned,
            quarantine_root=tmp_path / "quarantine",
            output_frames=tmp_path / "output",
            manifest=manifest,
            identities=identities,
            frame_range=FrameRange(1, 1),
            extension="png",
            validate_frame=_nonempty,
        )


def test_quarantine_rejects_source_ancestor_overlap_before_copy(tmp_path: Path) -> None:
    returned = tmp_path / "returned"
    returned.mkdir()
    nested_quarantine = returned / "quarantine"
    with pytest.raises(ReturnImportError, match="disjoint"):
        quarantine_return(returned, nested_quarantine)
    assert not nested_quarantine.exists()

    quarantine_root = tmp_path / "existing-quarantine"
    nested_return = quarantine_root / "returned"
    nested_return.mkdir(parents=True)
    with pytest.raises(ReturnImportError, match="disjoint"):
        quarantine_return(nested_return, quarantine_root)
    assert list(quarantine_root.iterdir()) == [nested_return]


def test_import_rejects_output_overlap_before_mkdir_or_copy(
    tmp_path: Path,
    identities: IdentityBundle,
) -> None:
    returned = tmp_path / "returned"
    frame = returned / "objects" / "frame_000001.png"
    frame.parent.mkdir(parents=True)
    frame.write_bytes(b"frame")
    manifest = _return_manifest(
        identities,
        Path("objects/frame_000001.png"),
        sha256_path(frame),
    )
    quarantine_root = tmp_path / "quarantine-source-output"
    output_inside_source = returned / "published"
    with pytest.raises(ReturnImportError, match="source and output"):
        import_quarantined_return(
            returned=returned,
            quarantine_root=quarantine_root,
            output_frames=output_inside_source,
            manifest=manifest,
            identities=identities,
            frame_range=FrameRange(1, 1),
            extension="png",
            validate_frame=_nonempty,
        )
    assert not quarantine_root.exists()
    assert not output_inside_source.exists()

    output_root = tmp_path / "output-with-quarantine"
    quarantine_inside_output = output_root / "quarantine"
    with pytest.raises(ReturnImportError, match="quarantine root and output"):
        import_quarantined_return(
            returned=returned,
            quarantine_root=quarantine_inside_output,
            output_frames=output_root,
            manifest=manifest,
            identities=identities,
            frame_range=FrameRange(1, 1),
            extension="png",
            validate_frame=_nonempty,
        )
    assert not output_root.exists()
