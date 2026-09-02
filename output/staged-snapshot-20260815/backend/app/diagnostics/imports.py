from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from typing import Any
from unittest.mock import patch

IMPORT_ORDERS: dict[str, tuple[str, ...]] = {
    "core-first": (
        "app.analysis.core",
        "app.tagging.music",
        "app.tagging",
        "app.adapters",
        "app.analysis.pipeline",
        "app.main",
    ),
    "adapters-first": (
        "app.adapters",
        "app.tagging.music",
        "app.analysis.core",
        "app.tagging",
        "app.main",
        "app.analysis.pipeline",
    ),
    "main-first": (
        "app.main",
        "app.adapters",
        "app.tagging.music",
        "app.analysis.core",
        "app.analysis.pipeline",
        "app.tagging",
    ),
}

OPTIONAL_DEPENDENCY_ROOTS = {
    "ctranslate2",
    "demucs",
    "faster_whisper",
    "huggingface_hub",
    "torch",
    "torchaudio",
    "transformers",
}


def _verify_apis() -> dict[str, bool]:
    pipeline = importlib.import_module("app.analysis.pipeline")
    music = importlib.import_module("app.tagging.music")
    lyrics = importlib.import_module("app.lyrics.transcriber")
    prompt_writer = importlib.import_module("app.prompting.local_writer")
    adapters = importlib.import_module("app.adapters")
    main = importlib.import_module("app.main")
    checks = {
        "analyzeAudio": callable(getattr(pipeline, "analyze_audio", None)),
        "analysisCancelled": isinstance(getattr(pipeline, "AnalysisCancelled", None), type),
        "musicTaggerFactory": callable(getattr(music, "create_music_tagger", None)),
        "lyricsAdapterFactory": callable(getattr(lyrics, "create_lyrics_adapter", None)),
        "promptWriterFactory": callable(getattr(prompt_writer, "create_prompt_writer", None)),
        "demucsReadiness": callable(getattr(adapters, "demucs_ready", None)),
        "capabilityFactory": callable(getattr(adapters, "get_capabilities", None)),
        "fastApiApplication": getattr(main, "app", None) is not None,
    }
    if not all(checks.values()):
        raise RuntimeError("A required direct-module API is unavailable.")
    return checks


def _import_and_verify(order_name: str) -> dict[str, Any]:
    modules = IMPORT_ORDERS[order_name]
    pipeline_loaded_early = False
    imported: list[str] = []
    for module_name in modules:
        importlib.import_module(module_name)
        imported.append(module_name)
        if (
            order_name != "main-first"
            and module_name in {"app.analysis.core", "app.adapters"}
            and "app.analysis.pipeline" not in imported
        ):
            pipeline_loaded_early = pipeline_loaded_early or "app.analysis.pipeline" in sys.modules
    if pipeline_loaded_early:
        raise RuntimeError("Analysis orchestration loaded through a leaf-module import.")
    return {
        "order": order_name,
        "status": "ok",
        "modules": imported,
        "pipelineLoadedEarly": False,
        "apis": _verify_apis(),
    }


def _without_optional_dependencies(name: str, package: str | None = None) -> Any:
    if name.partition(".")[0] in OPTIONAL_DEPENDENCY_ROOTS:
        return None
    return _ORIGINAL_FIND_SPEC(name, package)


_ORIGINAL_FIND_SPEC = importlib.util.find_spec


def _child(order_name: str, simulate_optional_absence: bool) -> int:
    try:
        if simulate_optional_absence:
            with patch("importlib.util.find_spec", side_effect=_without_optional_dependencies):
                result = _import_and_verify(order_name)
        else:
            result = _import_and_verify(order_name)
        result["optionalDependenciesSimulatedAbsent"] = simulate_optional_absence
    except Exception as exc:
        result = {
            "order": order_name,
            "status": "error",
            "errorType": type(exc).__name__,
            "message": str(exc),
            "optionalDependenciesSimulatedAbsent": simulate_optional_absence,
        }
        print(json.dumps(result, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


def _last_json_line(output: str) -> dict[str, Any] | None:
    for line in reversed(output.splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _parent() -> int:
    scenarios = (
        ("core-first", False),
        ("adapters-first", False),
        ("main-first", True),
    )
    environment = os.environ.copy()
    environment.update(
        {
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "NO_PROXY": "*",
        }
    )
    results: list[dict[str, Any]] = []
    success = True
    for order_name, simulate_optional_absence in scenarios:
        arguments = [
            sys.executable,
            "-m",
            "app.diagnostics.imports",
            "--child",
            order_name,
        ]
        if simulate_optional_absence:
            arguments.append("--simulate-optional-absence")
        try:
            completed = subprocess.run(
                arguments,
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
                env=environment,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            completed_payload: dict[str, Any] = {
                "order": order_name,
                "status": "error",
                "errorType": "TimeoutExpired",
            }
            success = False
        else:
            completed_payload = _last_json_line(completed.stdout) or {
                "order": order_name,
                "status": "error",
                "errorType": "MissingChildResult",
            }
            if completed.returncode != 0 or completed_payload.get("status") != "ok":
                success = False
        results.append(completed_payload)
    print(
        json.dumps(
            {
                "diagnostic": "imports",
                "status": "ok" if success else "error",
                "freshProcesses": len(results),
                "results": results,
            },
            sort_keys=True,
        )
    )
    return 0 if success else 1


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify TrackPrompt import boundaries in fresh processes.")
    parser.add_argument("--child", choices=tuple(IMPORT_ORDERS))
    parser.add_argument("--simulate-optional-absence", action="store_true")
    parsed = parser.parse_args(arguments)
    if parsed.child is not None:
        return _child(str(parsed.child), bool(parsed.simulate_optional_absence))
    return _parent()


if __name__ == "__main__":
    raise SystemExit(main())
