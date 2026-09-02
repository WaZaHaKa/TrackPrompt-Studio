from __future__ import annotations

from ..adapters import get_capabilities
from ..config import Settings


def main() -> int:
    print(get_capabilities(Settings.from_env()).model_dump_json(by_alias=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
