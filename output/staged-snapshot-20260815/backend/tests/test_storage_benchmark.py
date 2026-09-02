from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from tools.benchmark_render_storage import benchmark_storage  # noqa: E402


def test_bounded_storage_benchmark_verifies_and_removes_its_artifact(
    tmp_path: Path,
) -> None:
    result = benchmark_storage(tmp_path, size_mib=16)

    assert result["sizeBytes"] == 16 * 1024 * 1024
    assert float(result["writeMiBPerSecond"]) > 0
    assert float(result["readMiBPerSecond"]) > 0
    assert result["temporaryArtifactRetained"] is False
    assert not list(tmp_path.glob(".trackprompt-storage-benchmark-*.bin"))


@pytest.mark.parametrize("size_mib", [0, 15, 4097])
def test_storage_benchmark_rejects_unbounded_sizes(
    tmp_path: Path,
    size_mib: int,
) -> None:
    with pytest.raises(ValueError, match="between 16 and 4096"):
        benchmark_storage(tmp_path, size_mib=size_mib)
