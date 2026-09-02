from __future__ import annotations

import json
from importlib import metadata
from pathlib import Path

PIN_FILES = ("requirements.lock.txt", "requirements.full-gpu.txt")
SEPARATE_PINS = {"torch": "2.7.1", "torchaudio": "2.7.1"}
REPORT_PACKAGES = {
    "ctranslate2",
    "demucs",
    "faster-whisper",
    "huggingface-hub",
    "librosa",
    "numpy",
    "safetensors",
    "scikit-learn",
    "sentencepiece",
    "scipy",
    "tokenizers",
    "torch",
    "torchaudio",
    "transformers",
}


def _normalize(name: str) -> str:
    return name.casefold().replace("_", "-")


def _read_direct_pins(root: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for filename in PIN_FILES:
        path = root / filename
        if not path.is_file():
            raise RuntimeError(f"{filename} is unavailable.")
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "==" not in line:
                continue
            name, version = line.split("==", 1)
            pins[_normalize(name.strip())] = version.strip()
    pins.update(SEPARATE_PINS)
    return pins


def inspect_direct_dependencies(root: Path) -> dict[str, object]:
    pins = _read_direct_pins(root)
    versions: dict[str, str] = {}
    drift: list[dict[str, str]] = []
    for name in sorted(pins):
        expected = pins[name]
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError:
            installed = "missing"
        if installed != expected and not installed.startswith(f"{expected}+"):
            drift.append({"package": name, "expected": expected, "installed": installed})
    for name in sorted(REPORT_PACKAGES):
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = "not-installed"
    return {
        "diagnostic": "dependencies",
        "status": "ok" if not drift else "error",
        "importantVersions": versions,
        "directPinDrift": drift,
    }


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    try:
        payload = inspect_direct_dependencies(root)
    except (OSError, RuntimeError) as exc:
        payload = {
            "diagnostic": "dependencies",
            "status": "error",
            "errorType": type(exc).__name__,
            "message": str(exc),
        }
    print(json.dumps(payload, sort_keys=True))
    return 0 if payload.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
