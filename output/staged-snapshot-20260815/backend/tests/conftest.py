from __future__ import annotations

from pathlib import Path

import pytest

from app.schemas import AnalysisResult

from .helpers import FIXTURES, analysis_for


@pytest.fixture(scope="session")
def fixture_dir() -> Path:
    assert (FIXTURES / "120bpm_click.wav").is_file(), (
        "Run `python tools/generate_test_audio.py --output-dir test-fixtures` first."
    )
    return FIXTURES


@pytest.fixture(scope="session")
def click_analysis(tmp_path_factory: pytest.TempPathFactory, fixture_dir: Path) -> AnalysisResult:
    return analysis_for(
        fixture_dir / "120bpm_click.wav",
        tmp_path_factory.mktemp("click-analysis"),
        display_name="secret-source-name.wav",
    )
