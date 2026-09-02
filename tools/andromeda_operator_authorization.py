from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.app.cinematic.operator_authorization import (  # noqa: E402
    OperatorAuthorizationError,
    context_summary,
    create_operator_start_authorization,
    enforce_operator_release_hold,
    load_operator_authorization_context,
    require_expected_matrix,
    validate_operator_start_authorization,
)
from backend.app.cinematic.production_contracts import (  # noqa: E402
    file_sha256,
    load_and_validate_final_release,
)

RELEASE_HOLD_RELATIVE_PATH = (
    Path("production") / "andromeda-v2" / "release-hold.json"
)


def _add_release_inputs(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument("--package-manifest", type=Path, required=True)
    parser.add_argument("--technical-authorization", type=Path, required=True)
    parser.add_argument("--enable-vertical", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create or validate a separate, exact release-bound Andromeda V2 "
            "operator-start authorization."
        )
    )
    commands = parser.add_subparsers(dest="command", required=True)

    inspect = commands.add_parser(
        "inspect",
        help="Inspect the exact release and print the required typed confirmation.",
    )
    _add_release_inputs(inspect)

    create = commands.add_parser(
        "create",
        help="Write a new non-overwriting operator-start authorization.",
    )
    _add_release_inputs(create)
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--typed-confirmation", required=True)

    validate = commands.add_parser(
        "validate",
        help="Validate a separate operator-start authorization fail closed.",
    )
    _add_release_inputs(validate)
    validate.add_argument("--operator-authorization", type=Path, required=True)
    return parser


def _print_json(payload: dict[str, object], *, stream: object = sys.stdout) -> None:
    print(
        json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
        file=stream,
    )


def _resolve_repository_root(path: Path) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise OperatorAuthorizationError(
            f"repository root is unavailable: {path}"
        ) from exc
    if not resolved.is_dir():
        raise OperatorAuthorizationError(
            f"repository root is not a directory: {resolved}"
        )
    return resolved


def _resolve_from_repository(repository_root: Path, path: Path) -> Path:
    candidate = path if path.is_absolute() else repository_root / path
    return candidate.resolve()


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        repository_root = _resolve_repository_root(args.repository_root)
        calibration_path = _resolve_from_repository(
            repository_root,
            args.calibration,
        )
        package_manifest_path = _resolve_from_repository(
            repository_root,
            args.package_manifest,
        )
        technical_authorization_path = _resolve_from_repository(
            repository_root,
            args.technical_authorization,
        )
        try:
            load_and_validate_final_release(
                calibration_path,
                package_manifest_path,
                technical_authorization_path,
                repository_root=repository_root,
            )
            package_manifest_sha256 = file_sha256(
                package_manifest_path.resolve(strict=True)
            )
        except (OSError, ValueError) as exc:
            raise OperatorAuthorizationError(
                f"the exact final-release package is invalid: {exc}"
            ) from exc
        context = load_operator_authorization_context(
            calibration_path,
            technical_authorization_path,
            package_manifest_path=package_manifest_path,
        )
        enforce_operator_release_hold(
            context,
            repository_root / RELEASE_HOLD_RELATIVE_PATH,
            package_manifest_sha256=package_manifest_sha256,
        )

        if args.command == "inspect":
            require_expected_matrix(
                context,
                enable_vertical=args.enable_vertical,
            )
            _print_json(
                context_summary(context, include_confirmation_phrase=True)
            )
            return 0

        if args.command == "create":
            output_path = _resolve_from_repository(
                repository_root,
                args.output,
            )
            authorization = create_operator_start_authorization(
                calibration_path,
                technical_authorization_path,
                output_path,
                package_manifest_path=package_manifest_path,
                typed_confirmation=args.typed_confirmation,
                enable_vertical=args.enable_vertical,
            )
            _print_json(
                {
                    "ok": True,
                    "authorizationId": authorization.authorization_id,
                    "operatorAuthorizationPath": str(output_path),
                    "releaseIdentitySha256": (
                        authorization.release_identity_sha256
                    ),
                    "outputMatrixId": authorization.output_matrix_id,
                    "enabledVariantIds": [
                        str(value)
                        for value in authorization.enabled_variant_ids
                    ],
                    "productionRenderStarted": False,
                }
            )
            return 0

        operator_authorization_path = _resolve_from_repository(
            repository_root,
            args.operator_authorization,
        )
        authorization, context = validate_operator_start_authorization(
            calibration_path,
            technical_authorization_path,
            operator_authorization_path,
            package_manifest_path=package_manifest_path,
            enable_vertical=args.enable_vertical,
        )
        payload = context_summary(
            context,
            include_confirmation_phrase=False,
        )
        payload.update(
            {
                "ok": True,
                "authorizationId": authorization.authorization_id,
                "operatorAuthorizationPath": str(operator_authorization_path),
                "productionStartAuthorized": True,
                "productionRenderStarted": False,
            }
        )
        _print_json(payload)
        return 0
    except OperatorAuthorizationError as exc:
        _print_json(
            {
                "ok": False,
                "error": {
                    "code": "operator-authorization-rejected",
                    "message": str(exc),
                },
                "productionRenderStarted": False,
            },
            stream=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
