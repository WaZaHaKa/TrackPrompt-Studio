"""Repository entry point: imports have no rendering or cloud side effects."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.video_generation.hayale_operator import main  # noqa: E402

if __name__ == '__main__':
    raise SystemExit(main())
