from __future__ import annotations

from pathlib import Path
from typing import Any
import struct
import zlib

from ..models import ChunkLease, IdentityBundle
from .core import RenderedFrame, RuntimeInfo


class MockRenderRuntime:
    """Deterministic no-Blender runtime used exclusively by offline tests."""

    def __init__(
        self,
        identities: IdentityBundle,
        *,
        blender_version: str = "5.2.0",
        width: int = 16,
        height: int = 16,
        bit_depth: int = 8,
        image_format: str = "PNG",
        extension: str = "png",
        gpu_visible: bool = True,
        software_rendering: bool = False,
    ) -> None:
        self.identities = identities
        self.blender_version = blender_version
        self.width = width
        self.height = height
        self.bit_depth = bit_depth
        self.image_format = image_format
        self.extension = extension
        self.gpu_visible = gpu_visible
        self.software_rendering = software_rendering
        self.shutdown_reasons: list[str] = []

    def inspect(self, _package_manifest: dict[str, Any]) -> RuntimeInfo:
        return RuntimeInfo(
            blender_version=self.blender_version,
            gpu_name="Mock GPU",
            gpu_visible=self.gpu_visible,
            software_rendering=self.software_rendering,
            identities=self.identities,
        )

    def render(
        self,
        lease: ChunkLease,
        output_directory: Path,
        progress: Any,
        cancelled: Any,
    ) -> list[RenderedFrame]:
        result: list[RenderedFrame] = []
        for frame in range(lease.frame_range.start, lease.frame_range.end + 1):
            if cancelled():
                break
            path = output_directory / f"frame_{frame:06d}.{self.extension}"
            if self.image_format.upper() != "PNG" or self.extension.lower() != "png":
                raise ValueError("mock runtime currently emits bounded PNG fixtures only")
            _write_png(path, self.width, self.height, self.bit_depth, frame)
            progress(frame)
            result.append(
                RenderedFrame(
                    frame,
                    path,
                    self.width,
                    self.height,
                    self.bit_depth,
                    self.image_format,
                )
            )
        return result

    def shutdown(self, reason: str) -> None:
        self.shutdown_reasons.append(reason)


def _chunk(kind: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(kind)
    checksum = zlib.crc32(payload, checksum) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)


def _write_png(path: Path, width: int, height: int, bit_depth: int, frame: int) -> None:
    if width < 1 or height < 1 or bit_depth not in {8, 16}:
        raise ValueError("mock PNG contract is invalid")
    bytes_per_sample = bit_depth // 8
    sample = frame % 256
    if bit_depth == 8:
        pixel = bytes((sample, (sample * 3) % 256, (sample * 7) % 256))
    else:
        pixel = b"".join(
            struct.pack(">H", value * 257)
            for value in (sample, (sample * 3) % 256, (sample * 7) % 256)
        )
    assert len(pixel) == 3 * bytes_per_sample
    scanline = b"\x00" + pixel * width
    payload = scanline * height
    header = struct.pack(">IIBBBBB", width, height, bit_depth, 2, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(payload, level=1))
        + _chunk(b"IEND", b"")
    )
