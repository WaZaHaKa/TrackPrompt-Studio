from __future__ import annotations

import io
from typing import BinaryIO, cast

import pytest

from app.subprocess_utils import _bounded_drain


def test_cleanup_can_close_pipe_before_drain_thread_reads_again() -> None:
    stream = io.BytesIO(b"discarded")
    stream.close()
    target = bytearray(b"already captured")
    exceeded = [False]
    _bounded_drain(stream, 64, target, exceeded)
    assert target == b"already captured"
    assert exceeded == [False]


@pytest.mark.parametrize("failure", [OSError("pipe closed"), ValueError("I/O operation on closed file")])
def test_shutdown_race_preserves_captured_prefix_and_output_limit(failure: Exception) -> None:
    class ClosingPipe:
        calls = 0

        def read(self, size: int) -> bytes:
            self.calls += 1
            if self.calls == 1:
                return b"prefix and excess"
            raise failure

    target = bytearray()
    exceeded = [False]
    _bounded_drain(cast(BinaryIO, ClosingPipe()), 6, target, exceeded)
    assert target == b"prefix"
    assert exceeded == [True]
