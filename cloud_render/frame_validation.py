from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path


class FrameValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class FrameHeader:
    width: int
    height: int
    bit_depth: int
    image_format: str
    color_mode: str


def _read_exact(handle: object, size: int, label: str) -> bytes:
    data = handle.read(size)  # type: ignore[attr-defined]
    if not isinstance(data, bytes) or len(data) != size:
        raise FrameValidationError(f"truncated {label}")
    return data


def validate_png(path: Path, *, maximum_bytes: int = 1024 * 1024 * 1024) -> FrameHeader:
    size = path.stat().st_size
    if size <= 0 or size > maximum_bytes:
        raise FrameValidationError("PNG size is empty or exceeds the validation bound")
    width: int | None = None
    height: int | None = None
    bit_depth: int | None = None
    color_type: int | None = None
    interlace_method: int | None = None
    saw_idat = False
    saw_iend = False
    compressed_image = bytearray()
    with path.open("rb") as handle:
        if _read_exact(handle, 8, "PNG signature") != b"\x89PNG\r\n\x1a\n":
            raise FrameValidationError("invalid PNG signature")
        while not saw_iend:
            length, chunk_type = struct.unpack(">I4s", _read_exact(handle, 8, "PNG chunk"))
            if length > 512 * 1024 * 1024:
                raise FrameValidationError("PNG chunk exceeds the validation bound")
            payload = _read_exact(handle, length, "PNG chunk payload")
            stored_crc = struct.unpack(">I", _read_exact(handle, 4, "PNG CRC"))[0]
            expected_crc = zlib.crc32(chunk_type)
            expected_crc = zlib.crc32(payload, expected_crc) & 0xFFFFFFFF
            if stored_crc != expected_crc:
                raise FrameValidationError("PNG chunk CRC mismatch")
            if chunk_type == b"IHDR":
                if width is not None or length != 13:
                    raise FrameValidationError("invalid PNG IHDR")
                (
                    width,
                    height,
                    bit_depth,
                    color_type,
                    compression,
                    filtering,
                    interlace_method,
                ) = struct.unpack(">IIBBBBB", payload)
                if (
                    width <= 0
                    or height <= 0
                    or compression != 0
                    or filtering != 0
                    or interlace_method != 0
                ):
                    raise FrameValidationError("invalid PNG header values")
            elif chunk_type == b"IDAT":
                saw_idat = True
                compressed_image.extend(payload)
                if len(compressed_image) > maximum_bytes:
                    raise FrameValidationError("PNG compressed image exceeds the validation bound")
            elif chunk_type == b"IEND":
                if length != 0:
                    raise FrameValidationError("invalid PNG IEND")
                saw_iend = True
        if handle.read(1):
            raise FrameValidationError("PNG has trailing data")
    if width is None or height is None or bit_depth not in {8, 16}:
        raise FrameValidationError("PNG dimensions or bit depth are invalid")
    if color_type != 2 or not saw_idat:
        raise FrameValidationError("PNG must be opaque RGB with image data")
    bytes_per_sample = bit_depth // 8
    scanline_bytes = width * 3 * bytes_per_sample
    expected_decoded_bytes = (scanline_bytes + 1) * height
    if expected_decoded_bytes > maximum_bytes:
        raise FrameValidationError("PNG decoded image exceeds the validation bound")
    try:
        decoder = zlib.decompressobj()
        decoded = decoder.decompress(
            bytes(compressed_image), expected_decoded_bytes + 1
        )
        remaining = expected_decoded_bytes + 1 - len(decoded)
        if remaining > 0:
            decoded += decoder.flush(remaining)
    except zlib.error as exc:
        raise FrameValidationError("PNG image data is not valid zlib content") from exc
    if (
        len(decoded) != expected_decoded_bytes
        or not decoder.eof
        or decoder.unused_data
        or decoder.unconsumed_tail
    ):
        raise FrameValidationError("PNG decoded image length is invalid")
    for offset in range(0, expected_decoded_bytes, scanline_bytes + 1):
        if decoded[offset] > 4:
            raise FrameValidationError("PNG scanline uses an invalid filter")
    return FrameHeader(width, height, bit_depth, "PNG", "RGB")


def validate_image(path: Path, image_format: str) -> FrameHeader:
    normalized = image_format.upper()
    if normalized == "PNG":
        return validate_png(path)
    raise FrameValidationError(f"bounded validation is unavailable for {normalized}")
