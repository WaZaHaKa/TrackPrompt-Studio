from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from uuid import uuid4

MIB = 1024 * 1024


def benchmark_storage(target: Path, *, size_mib: int) -> dict[str, object]:
    """Measure bounded sequential write/read throughput without retaining data."""
    if not 16 <= size_mib <= 4096:
        raise ValueError("size_mib must be between 16 and 4096")
    root = target.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("target must be an existing directory")
    temporary = root / f".trackprompt-storage-benchmark-{uuid4().hex}.bin"
    block = hashlib.sha256(b"trackprompt-render-storage-benchmark-v1").digest()
    payload = (block * (MIB // len(block) + 1))[:MIB]
    expected_digest = hashlib.sha256()
    written = 0
    write_started = time.perf_counter()
    try:
        with temporary.open("xb", buffering=0) as stream:
            for _ in range(size_mib):
                stream.write(payload)
                expected_digest.update(payload)
                written += len(payload)
            stream.flush()
            os.fsync(stream.fileno())
        write_seconds = max(time.perf_counter() - write_started, 1e-9)

        observed_digest = hashlib.sha256()
        read = 0
        read_started = time.perf_counter()
        with temporary.open("rb", buffering=0) as stream:
            for chunk in iter(lambda: stream.read(MIB), b""):
                observed_digest.update(chunk)
                read += len(chunk)
        read_seconds = max(time.perf_counter() - read_started, 1e-9)
        if read != written or observed_digest.digest() != expected_digest.digest():
            raise OSError("storage benchmark read-back did not match the written bytes")
        return {
            "schemaVersion": "1.0.0",
            "kind": "trackprompt-render-storage-benchmark",
            "sizeBytes": written,
            "writeSeconds": round(write_seconds, 6),
            "writeMiBPerSecond": round(written / MIB / write_seconds, 3),
            "readSeconds": round(read_seconds, 6),
            "readMiBPerSecond": round(read / MIB / read_seconds, 3),
            "readBackSha256": observed_digest.hexdigest(),
            "temporaryArtifactRetained": False,
        }
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a bounded sequential throughput check for a render target."
    )
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--size-mib", type=int, default=256)
    args = parser.parse_args()
    try:
        result = benchmark_storage(args.target, size_mib=args.size_mib)
    except (OSError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": "storage-benchmark-failed",
                        "message": str(exc)[:500],
                    },
                },
                separators=(",", ":"),
            )
        )
        return 2
    print(json.dumps({"ok": True, **result}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
