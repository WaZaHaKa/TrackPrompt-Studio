from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any


NEAR_BLACK_LUMINANCE = 0.05
NEAR_BLACK_REVIEW_FRACTION = 0.60
CLIPPED_HIGHLIGHT_LUMINANCE = 0.98


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object in {path.name}.")
    return payload


def _artifact(root: Path, reference: object, label: str) -> Path:
    if not isinstance(reference, dict):
        raise ValueError(f"Missing {label} reference.")
    relative = reference.get("file")
    digest = reference.get("sha256")
    if not isinstance(relative, str) or not isinstance(digest, str):
        raise ValueError(f"Invalid {label} reference.")
    path = (root / relative).resolve(strict=True)
    try:
        path.relative_to(root.resolve(strict=True))
    except ValueError as exc:
        raise ValueError(f"{label} escapes the look-development root.") from exc
    if sha256_file(path) != digest:
        raise ValueError(f"{label} hash does not match the rendered artifact.")
    return path


def _decode_rgb(ffmpeg: Path, image: Path, width: int, height: int) -> bytes:
    command = [
        str(ffmpeg),
        "-v",
        "error",
        "-i",
        str(image),
        "-frames:v",
        "1",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "pipe:1",
    ]
    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        timeout=30,
        shell=False,
    )
    expected = width * height * 3
    if len(result.stdout) != expected:
        raise ValueError(
            f"Decoded {image.name} byte count {len(result.stdout)} does not match {expected}."
        )
    return result.stdout


def _luminance(red: int, green: int, blue: int) -> float:
    return (0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255.0


def luminance_metrics(rgb: bytes, width: int, height: int) -> dict[str, float]:
    expected = width * height * 3
    if len(rgb) != expected:
        raise ValueError(f"RGB byte count {len(rgb)} does not match {expected}.")
    luminances = [
        _luminance(rgb[index], rgb[index + 1], rgb[index + 2])
        for index in range(0, len(rgb), 3)
    ]
    count = len(luminances)
    return {
        "meanLuminance": sum(luminances) / count,
        "nearBlackFraction": sum(value < NEAR_BLACK_LUMINANCE for value in luminances) / count,
        "clippedHighlightFraction": sum(
            value > CLIPPED_HIGHLIGHT_LUMINANCE for value in luminances
        )
        / count,
    }


def masked_separation_metrics(
    rgb: bytes,
    mask_rgb: bytes,
    width: int,
    height: int,
) -> dict[str, float]:
    expected = width * height * 3
    if len(rgb) != expected or len(mask_rgb) != expected:
        raise ValueError("Beauty and mask byte counts must match the declared dimensions.")
    subject: list[float] = []
    background: list[float] = []
    for index in range(0, expected, 3):
        value = _luminance(rgb[index], rgb[index + 1], rgb[index + 2])
        mask_value = max(mask_rgb[index], mask_rgb[index + 1], mask_rgb[index + 2]) / 255.0
        if mask_value >= 0.5:
            subject.append(value)
        else:
            background.append(value)
    if not subject or not background:
        raise ValueError("Mask must contain both subject and background pixels.")
    subject_mean = sum(subject) / len(subject)
    background_mean = sum(background) / len(background)
    return {
        "occupancyFraction": len(subject) / (width * height),
        "subjectMeanLuminance": subject_mean,
        "backgroundMeanLuminance": background_mean,
        "signedLuminanceSeparation": subject_mean - background_mean,
        "absoluteLuminanceSeparation": abs(subject_mean - background_mean),
    }


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=True, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


def analyze_lookdev(
    root: Path,
    ffmpeg: Path,
    output: Path,
) -> dict[str, Any]:
    lookdev_root = root.resolve(strict=True)
    ffmpeg_path = ffmpeg.resolve(strict=True)
    manifest_path = lookdev_root / "r13-lookdev-render-manifest.json"
    manifest = _read_json(manifest_path)
    if (
        manifest.get("kind") != "trackprompt-cinematic-v2-r13-lookdev-render-manifest"
        or manifest.get("revisionId") != "andromeda-r13-lookdev-lock"
        or manifest.get("nativeVertical") != {"width": 1080, "height": 1920}
        or manifest.get("phoneReview")
        != {"width": 180, "height": 320, "crop": False}
    ):
        raise ValueError("The R13 look-development render manifest is invalid.")
    variants = manifest.get("variants")
    if not isinstance(variants, list) or len(variants) != 8:
        raise ValueError("R13 diagnostics require all eight look-development variants.")
    diagnostics: list[dict[str, Any]] = []
    for variant in variants:
        if not isinstance(variant, dict):
            raise ValueError("R13 variant evidence is invalid.")
        beauty = _artifact(lookdev_root, variant.get("beauty"), "native beauty")
        phone = _artifact(lookdev_root, variant.get("phone"), "phone beauty")
        subject_mask = _artifact(lookdev_root, variant.get("subjectMask"), "subject mask")
        native_rgb = _decode_rgb(ffmpeg_path, beauty, 1080, 1920)
        phone_rgb = _decode_rgb(ffmpeg_path, phone, 180, 320)
        subject_rgb = _decode_rgb(ffmpeg_path, subject_mask, 180, 320)
        native = luminance_metrics(native_rgb, 1080, 1920)
        phone_metrics = luminance_metrics(phone_rgb, 180, 320)
        subject = masked_separation_metrics(phone_rgb, subject_rgb, 180, 320)
        gate_reference = variant.get("gateMask")
        gate = None
        if isinstance(gate_reference, dict):
            gate_mask = _artifact(lookdev_root, gate_reference, "gate mask")
            gate_rgb = _decode_rgb(ffmpeg_path, gate_mask, 180, 320)
            gate = masked_separation_metrics(phone_rgb, gate_rgb, 180, 320)
        intentional_blackout = variant.get("intentionalBlackout") is True
        native_review = native["nearBlackFraction"] > NEAR_BLACK_REVIEW_FRACTION
        phone_review = phone_metrics["nearBlackFraction"] > NEAR_BLACK_REVIEW_FRACTION
        findings: list[str] = []
        if (native_review or phone_review) and not intentional_blackout:
            findings.append("ordinary-frame-near-black-fraction-exceeds-60-percent")
        if subject["absoluteLuminanceSeparation"] < 0.06:
            findings.append("subject-background-separation-is-weak")
        if gate is not None and gate["absoluteLuminanceSeparation"] < 0.045:
            findings.append("gate-background-separation-is-weak")
        diagnostics.append(
            {
                "id": variant.get("id"),
                "kind": variant.get("kind"),
                "intentionalBlackout": intentional_blackout,
                "native": native,
                "phone": phone_metrics,
                "subject": subject,
                "gate": gate,
                "operatorReviewRequired": bool(findings),
                "findings": findings,
            }
        )
    payload = {
        "schemaVersion": "1.0.0",
        "kind": "trackprompt-cinematic-v2-r13-lookdev-diagnostics",
        "revisionId": "andromeda-r13-lookdev-lock",
        "renderManifest": {
            "file": manifest_path.name,
            "sha256": sha256_file(manifest_path),
        },
        "thresholds": {
            "nearBlackLuminance": NEAR_BLACK_LUMINANCE,
            "ordinaryFrameReviewFraction": NEAR_BLACK_REVIEW_FRACTION,
            "clippedHighlightLuminance": CLIPPED_HIGHLIGHT_LUMINANCE,
            "subjectSeparationAdvisory": 0.06,
            "gateSeparationAdvisory": 0.045,
        },
        "variants": diagnostics,
        "summary": {
            "variantCount": len(diagnostics),
            "ordinaryNearBlackReviewCount": sum(
                "ordinary-frame-near-black-fraction-exceeds-60-percent" in item["findings"]
                for item in diagnostics
            ),
            "subjectSeparationReviewCount": sum(
                "subject-background-separation-is-weak" in item["findings"]
                for item in diagnostics
            ),
            "gateSeparationReviewCount": sum(
                "gate-background-separation-is-weak" in item["findings"]
                for item in diagnostics
            ),
            "clippedHighlightMaximum": max(
                max(item["native"]["clippedHighlightFraction"], item["phone"]["clippedHighlightFraction"])
                for item in diagnostics
            ),
        },
        "automaticArtisticApproval": False,
        "humanArtistApproval": "pending",
    }
    _atomic_json(output, payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure native and phone-size R13 look-development exposure diagnostics."
    )
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--ffmpeg", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = analyze_lookdev(args.root, args.ffmpeg, args.output)
    print(
        json.dumps(
            {
                "ok": True,
                "variantCount": payload["summary"]["variantCount"],
                "summary": payload["summary"],
                "output": str(args.output),
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
