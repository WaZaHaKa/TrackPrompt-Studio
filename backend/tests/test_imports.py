from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _run_import_diagnostic(*arguments: str) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
    environment = os.environ.copy()
    environment.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "NO_PROXY": "*"})
    completed = subprocess.run(
        [sys.executable, "-m", "app.diagnostics.imports", *arguments],
        cwd=BACKEND_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
        env=environment,
        shell=False,
    )
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    payload = json.loads(lines[-1]) if lines else {}
    return completed, payload


@pytest.mark.parametrize(
    ("order_name", "extra_arguments"),
    [
        ("core-first", ()),
        ("adapters-first", ()),
        ("main-first", ("--simulate-optional-absence",)),
    ],
)
def test_critical_import_orders_work_in_fresh_processes(
    order_name: str,
    extra_arguments: tuple[str, ...],
) -> None:
    completed, payload = _run_import_diagnostic("--child", order_name, *extra_arguments)
    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert payload["status"] == "ok"
    assert payload["pipelineLoadedEarly"] is False
    assert all(payload["apis"].values())


def test_import_diagnostic_runs_all_isolated_scenarios() -> None:
    completed, payload = _run_import_diagnostic()
    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert payload["diagnostic"] == "imports"
    assert payload["status"] == "ok"
    assert payload["freshProcesses"] == 3
    assert all(result["status"] == "ok" for result in payload["results"])


def test_direct_module_apis_and_minimal_packages() -> None:
    import app.adapters as adapters
    import app.analysis
    import app.analysis.core as core
    import app.analysis.pipeline as pipeline
    import app.tagging
    import app.tagging.music as music

    assert callable(core.load_audio)
    assert callable(pipeline.analyze_audio)
    assert issubclass(pipeline.AnalysisCancelled, RuntimeError)
    assert callable(music.create_music_tagger)
    assert callable(adapters.demucs_ready)
    assert "analyze_audio" not in vars(app.analysis)
    assert "create_music_tagger" not in vars(app.tagging)
