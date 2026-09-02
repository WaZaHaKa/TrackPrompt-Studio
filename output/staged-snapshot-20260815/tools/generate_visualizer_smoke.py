from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

import soundfile as sf

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
BLENDER_ROOT = REPOSITORY_ROOT / "blender"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(BLENDER_ROOT) not in sys.path:
    sys.path.insert(0, str(BLENDER_ROOT))

from app.analysis.pipeline import analyze_audio  # noqa: E402
from app.schemas import AnalysisResult, FileInfo  # noqa: E402
from app.visualizer.compiler import compile_visual_cues  # noqa: E402
from app.visualizer.schemas import CuePreferences, CurveDetail, VisualFeatureArtifact  # noqa: E402
from trackprompt_visualizer.cue_loader import load_cue_sheet  # noqa: E402


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compile a synthetic TrackPrompt Blender smoke cue sheet.")
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--curve-detail", choices=[item.value for item in CurveDetail], default="compact")
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    audio_path = args.audio.resolve(strict=True)
    output = args.output.resolve()
    if output.suffix.casefold() != ".json":
        raise ValueError("The smoke cue output must use the .json extension.")
    output.parent.mkdir(parents=True, exist_ok=True)
    info = sf.info(audio_path)
    file_info = FileInfo(
        display_name="synthetic-audio.wav",
        duration_seconds=float(info.duration),
        sample_rate=info.samplerate,
        channels=info.channels,
        codec="pcm_s16le",
        container="wav",
        size_bytes=audio_path.stat().st_size,
    )
    job_id = "84291842-9184-4291-8429-184291842918"
    with tempfile.TemporaryDirectory(prefix="trackprompt-visualizer-smoke-") as temporary:
        work = Path(temporary)
        decoded = work / "decoded.wav"
        shutil.copy2(audio_path, decoded)
        serialized = analyze_audio(
            str(decoded),
            file_info.model_dump(mode="json", by_alias=True),
            job_id,
            "fast",
            str(work / "progress.json"),
            str(work / "cancel.flag"),
        )
        analysis = AnalysisResult.model_validate_json(serialized)
        artifact = VisualFeatureArtifact.model_validate_json(
            (work / "visual-features.json").read_text(encoding="utf-8")
        )
        cue_sheet = compile_visual_cues(
            analysis,
            artifact,
            CuePreferences(
                fps=args.fps,
                curve_detail=CurveDetail(args.curve_detail),
            ),
        )
    output.write_text(
        json.dumps(
            cue_sheet.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ),
        encoding="utf-8",
    )
    loaded = load_cue_sheet(output)
    print(
        json.dumps(
            {
                "ok": True,
                "output": str(output),
                "schemaVersion": loaded["schemaVersion"],
                "durationSeconds": cue_sheet.timeline.duration_seconds,
                "fps": cue_sheet.timeline.fps,
                "frameEnd": cue_sheet.timeline.frame_end,
                "sections": len(cue_sheet.sections),
                "transitions": len(cue_sheet.transitions),
                "curves": len(cue_sheet.curves),
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
