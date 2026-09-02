from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from blender.render_final_chunk import _apply_render_profile  # noqa: E402
from tools.final_render_tooling import ToolingError, load_render_profile, sha256_file  # noqa: E402


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render one assigned remote TrackPrompt chunk.")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--package-manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--start", required=True, type=int)
    parser.add_argument("--end", required=True, type=int)
    values = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    return parser.parse_args(values)


def main() -> int:
    import bpy  # type: ignore[import-not-found]

    try:
        args = _arguments()
        profile_path = Path(args.profile).resolve(strict=True)
        manifest_path = Path(args.package_manifest).resolve(strict=True)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        output = Path(args.output).resolve()
        if not output.is_dir() or any(output.iterdir()):
            raise ToolingError("unsafe-worker-output", "Worker output must be an existing empty package-contained directory.")
        profile = load_render_profile(profile_path)
        scene = Path(bpy.data.filepath).resolve(strict=True)
        if sha256_file(scene) != str(manifest["scene"]["sha256"]):
            raise ToolingError("scene-hash-mismatch", "Loaded remote scene hash differs from the package contract.")
        if sha256_file(profile_path) != str(manifest["profile"]["sha256"]):
            raise ToolingError("profile-hash-mismatch", "Remote profile hash differs from the package contract.")
        if bpy.app.version_string != str(manifest["blenderVersion"]):
            raise ToolingError("blender-version-mismatch", "Remote worker Blender version differs from the package contract.")
        _apply_render_profile(bpy.context.scene, profile, output, args.start, args.end)
        bpy.ops.render.render(animation=True)
        expected = [output / profile.image.filename(frame) for frame in range(args.start, args.end + 1)]
        if any(not path.is_file() or path.stat().st_size <= 0 for path in expected):
            raise ToolingError("remote-chunk-incomplete", "Blender did not produce every assigned frame.")
        print(json.dumps({"ok": True, "startFrame": args.start, "endFrame": args.end, "frameCount": len(expected)}))
        return 0
    except (OSError, KeyError, ValueError, ToolingError) as exc:
        code = exc.code if isinstance(exc, ToolingError) else "remote-worker-contract-error"
        print(json.dumps({"ok": False, "error": {"code": code, "message": str(exc)[:500]}}))
        return 2
    except Exception as exc:  # pragma: no cover
        print(json.dumps({"ok": False, "error": {"code": "remote-worker-unhandled-error", "type": type(exc).__name__, "message": str(exc)[:500]}}))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
