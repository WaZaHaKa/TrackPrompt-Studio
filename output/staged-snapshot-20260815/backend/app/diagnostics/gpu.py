from __future__ import annotations

import json


def main() -> int:
    result: dict[str, object] = {}
    try:
        import torch

        available = bool(torch.cuda.is_available())
        result.update(
            {
                "torchVersion": torch.__version__,
                "torchCudaBuild": torch.version.cuda,
                "torchCudaAvailable": available,
                "gpuName": torch.cuda.get_device_name(0) if available else None,
            }
        )
        if available:
            value = (torch.ones(16, device="cuda") * 2).sum().item()
            result["torchTinyInference"] = value == 32.0
    except (ImportError, RuntimeError, OSError) as exc:
        result["torchError"] = type(exc).__name__
    try:
        import ctranslate2

        result["ctranslate2Version"] = ctranslate2.__version__
        result["ctranslate2CudaDeviceCount"] = int(ctranslate2.get_cuda_device_count())
    except (ImportError, RuntimeError, OSError) as exc:
        result["ctranslate2Error"] = type(exc).__name__
    print(json.dumps(result, indent=2))
    return 0 if result.get("torchTinyInference") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
