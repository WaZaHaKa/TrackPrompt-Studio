"""One-command Windows controller; never imports production media processors."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.video_generation.hayale_gcp.launcher import main  # noqa: E402

if __name__ == '__main__':
    raise SystemExit(main())
