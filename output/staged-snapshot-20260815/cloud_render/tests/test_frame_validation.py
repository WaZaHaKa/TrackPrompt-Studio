from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

from cloud_render.frame_validation import FrameValidationError, validate_png
from cloud_render.worker.mock import _chunk, _write_png


def _png_with_image_data(path: Path, image_data: bytes) -> None:
    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", image_data)
        + _chunk(b"IEND", b"")
    )


def test_png_validator_decompresses_complete_scanlines(tmp_path: Path) -> None:
    path = tmp_path / "valid.png"
    _write_png(path, 2, 3, 16, 4)
    header = validate_png(path)
    assert (header.width, header.height, header.bit_depth, header.color_mode) == (
        2,
        3,
        16,
        "RGB",
    )


def test_png_validator_rejects_crc_valid_but_invalid_zlib(tmp_path: Path) -> None:
    path = tmp_path / "invalid-zlib.png"
    _png_with_image_data(path, b"not-zlib")
    with pytest.raises(FrameValidationError, match="zlib"):
        validate_png(path)


def test_png_validator_rejects_invalid_scanline_filter(tmp_path: Path) -> None:
    path = tmp_path / "invalid-filter.png"
    _png_with_image_data(path, zlib.compress(b"\x05\x00\x00\x00"))
    with pytest.raises(FrameValidationError, match="filter"):
        validate_png(path)
