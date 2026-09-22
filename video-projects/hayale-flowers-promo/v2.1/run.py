"""Explicit manual runner. Prepare and Status never contact a provider."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import uuid4

PROJECT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT.parents[2] / "backend"))
from app.local_video.production_lease import production_lease  # noqa: E402
from app.video_generation.jsonio import atomic_write_json, sha256_file, sha256_json  # noqa: E402


def sibling(name: str) -> ModuleType:
    key = f"hayale_v21_{name}"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, PROJECT / f"{name}.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return sys.modules[key]


def require_creation_allowed() -> None:
    if os.environ.get("TRACKPROMPT_HAYALE_PREPARE_ONLY") == "1":
        raise ValueError("This session is preparation-only; local creation is disabled")


def status() -> dict[str, Any]:
    prep = sibling("prepare")
    latest = prep.ROOT / "latest-run.json"
    if not latest.exists():
        return {
            "status": "prepared-not-started"
            if (prep.ROOT / "prepared/manifest.json").exists()
            else "not-prepared",
            "scenesVerified": 0,
            "generationStarted": False,
        }
    run_root = prep.contained(Path(prep.read(latest)["runRoot"]), prep.ROOT / "runs")
    observation = (
        prep.read(run_root / "recovery/latest-observation.json")
        if (run_root / "recovery/latest-observation.json").exists()
        else {}
    )
    return {
        "runRoot": str(run_root),
        "scenes": {
            f"shot-{n:03d}": sibling("runtime").recorded_scene_state(
                run_root / f"shot-{n:03d}",
                observation.get("scenes", {}).get(f"shot-{n:03d}"),
                observation.get("observedAt"),
            )
            for n in range(1, 8)
        },
        "deliveryRecorded": (run_root / "delivery.json").exists(),
        "statusSource": "saved disk receipts only",
        "recoveryObservation": prep.read(run_root / "recovery/latest-observation.json")
        if (run_root / "recovery/latest-observation.json").exists()
        else None,
    }


def create(
    *, profile: str, new_run: bool, silent: bool, existing_run_id: str | None = None
) -> dict[str, Any]:
    require_creation_allowed()
    prep, runtime, finish = sibling("prepare"), sibling("runtime"), sibling("finish")
    root = prep.contained(prep.ROOT)
    latest = root / "latest-run.json"
    if existing_run_id is not None and (
        len(existing_run_id) != 12
        or any(c not in "0123456789abcdef" for c in existing_run_id)
    ):
        raise ValueError("Invalid pinned existing run identity")
    if existing_run_id is not None and (
        new_run or not (root / "runs" / existing_run_id / "run.json").is_file()
    ):
        raise ValueError(
            "The explicitly pinned existing run is required; a new run is not allowed"
        )
    selected_id = existing_run_id or (
        Path(prep.read(latest)["runRoot"]).name
        if latest.exists() and not new_run
        else uuid4().hex[:12]
    )
    lifecycle = runtime.durable_module()
    with lifecycle.ControllerLease(root, selected_id):
        manifest = prep.verify_prepared()
        latest = root / "latest-run.json"
        # Bind implementation as well as preparation; never finish old work with changed code.
        code = {
            str(p.relative_to(PROJECT)): sha256_file(p)
            for p in sorted(PROJECT.glob("*.py"))
        }
        identity = {
            "preparation": manifest["digest"],
            "profile": profile,
            "silent": silent,
            "code": code,
        }
        digest = sha256_json(identity)
        if existing_run_id is not None or (latest.exists() and not new_run):
            run_root = prep.contained(
                root / "runs" / existing_run_id
                if existing_run_id
                else Path(prep.read(latest)["runRoot"]),
                root / "runs",
            )
            original_digest = prep.read(run_root / "run.json")["digest"]
            compatibility_path = run_root / "recovery/compatible-code.json"
            compatible = (
                prep.read(compatibility_path) if compatibility_path.exists() else {}
            )
            if original_digest != digest and not (
                compatible.get("originalRunDigest") == original_digest
                and compatible.get("approvedIdentityDigest") == digest
            ):
                raise ValueError(
                    "This run's profile, audio mode, code or preparation changed. Reconcile the existing run with -Action Recover -RunId "
                    + run_root.name
                    + ". Do not create a fresh run to bypass existing evidence."
                )
        else:
            run_root = prep.contained(root / "runs" / selected_id)
            run_root.mkdir(parents=True)
            atomic_write_json(run_root / "run.json", {**identity, "digest": digest})
            atomic_write_json(latest, {"runRoot": str(run_root)})
        if (run_root / "delivery.json").exists():
            delivery = prep.read(run_root / "delivery.json")
            for record in delivery["files"]:
                prep.contained(Path(record["path"]), run_root)
                prep.verify_record(record)
            return delivery["report"]  # type: ignore[no-any-return]
        size = prep.PROFILES[profile]
        scene_ids = [f"shot-{n:03d}" for n in range(1, 8)]
        for shot in scene_ids:
            if (run_root / shot / "raw").exists():
                lifecycle.verify_checkpoint(run_root, shot, manifest, write_proof=True)
        lifecycle.restore_completed(run_root, manifest, runtime, sibling("recovery"))
        missing = [
            shot
            for shot in scene_ids
            if runtime.verified_frames(run_root / shot, size) is None
        ]
        # A proven completed scene can be recovered from the owned server's retained
        # lossless output directory without restarting ComfyUI or generating again.
        missing = [
            shot
            for shot in missing
            if not runtime.recover_completed(
                run_root / shot, run_root / "comfy/output", size
            )
        ]
        runtime.report(
            f"V2.1 {profile}: {len(scene_ids) - len(missing)}/7 raw scenes verified. Run: {run_root}",
            flush=True,
        )
        recovery = sibling("recovery")
        if missing:
            borrowed = runtime.existing_server_available(run_root)
            if borrowed:
                server_context = recovery.existing_server(run_root)
            else:
                if any(
                    (run_root / shot / "submission.json").exists()
                    or (
                        (
                            attempt := lifecycle.Attempts(
                                run_root / shot, lifecycle.CURRENT_OWNER
                            ).get()
                        )
                        is not None
                        and attempt["state"] != "prepared"
                    )
                    for shot in missing
                ):
                    raise ValueError(
                        "The stopped provider has unresolved submissions; inspect retained history before any restart"
                    )
                atomic_write_json(
                    run_root / "resource-check.json", runtime.resource_check(run_root)
                )
                server_context = runtime.local_server(
                    Path(manifest["comfyRoot"]), run_root / "comfy"
                )
            with server_context as client:
                health = client.health()
                allowed = {
                    "UnetLoaderGGUF",
                    "KSamplerAdvanced",
                    "ModelSamplingSD3",
                    "WanImageToVideo",
                    "CLIPLoader",
                    "CLIPTextEncode",
                    "VAELoader",
                    "VAEDecodeTiled",
                    "LoadImage",
                    "SaveImage",
                }
                for shot in missing:
                    scene_root = prep.contained(run_root / shot)
                    existing_attempt = lifecycle.Attempts(
                        scene_root, lifecycle.CURRENT_OWNER
                    ).get()
                    if (
                        existing_attempt
                        and existing_attempt["state"] != "prepared"
                        and not (scene_root / "submission.json").exists()
                    ):
                        atomic_write_json(
                            scene_root / "submission.json",
                            {
                                "identity": f"v21-{run_root.name}-{shot}",
                                "workflowDigest": existing_attempt["identity"][
                                    "workflow_sha256"
                                ],
                                "state": "submitted"
                                if existing_attempt.get("prompt_id")
                                else "submission-intent",
                                "promptId": existing_attempt.get("prompt_id"),
                                "authority_projection": "attempt.json",
                            },
                        )
                    if (scene_root / "submission.json").exists():
                        current = recovery.inspect_submission(scene_root, client)
                        if current["state"] not in {
                            "generating",
                            "queued",
                            "frames_complete",
                        }:
                            atomic_write_json(
                                run_root / "recovery/blocked-submission.json", current
                            )
                            raise ValueError(
                                f"{shot}: {current['state']}; no automatic generation retry"
                            )
                        prompt_id = current["promptId"]
                        lifecycle.adopt_existing(
                            run_root,
                            shot,
                            manifest,
                            client.provider_instance,
                            prompt_id,
                        )
                        history = current.get("history") or runtime.wait_history(
                            client, prompt_id, scene_root / "submission.json"
                        )
                        atomic_write_json(
                            scene_root / "completed-history.json", history
                        )
                        runtime.collect_frames(
                            client, history, prompt_id, scene_root, size
                        )
                        continue
                    graph = prep.read(
                        root / "prepared/workflows" / profile / f"{shot}.json"
                    )
                    for node in graph.values():
                        if (
                            node["class_type"] not in allowed
                            or node["class_type"] not in health.object_info
                        ):
                            raise ValueError(
                                "The prepared graph contains a missing or disallowed local node"
                            )
                    for node, key in (
                        ("high_model", "unet_name"),
                        ("low_model", "unet_name"),
                        ("text_encoder", "clip_name"),
                        ("vae", "vae_name"),
                    ):
                        if graph[node]["inputs"][key] not in health.model_names:
                            raise ValueError(
                                "ComfyUI cannot resolve a prepared model; no download or fallback"
                            )
                    reference = next(
                        r for r in manifest["references"] if r["shotId"] == shot
                    )
                    scene_root = prep.contained(run_root / shot)
                    scene_root.mkdir(parents=True, exist_ok=True)
                    if existing_attempt:
                        graph, _, _ = lifecycle.scene_binding(run_root, shot, manifest)
                        if (
                            existing_attempt["identity"]["provider_instance"]
                            != client.provider_instance
                        ):
                            raise ValueError(
                                "Prepared-but-unsent attempt belongs to another provider instance; preserve and rebind explicitly"
                            )
                    else:
                        upload = client.upload_image(
                            Path(reference["path"]),
                            upload_name=f"v21-{run_root.name}-{shot}.png",
                        )
                        graph["start_image"]["inputs"]["image"] = upload
                        graph["save_frames"]["inputs"]["filename_prefix"] = (
                            f"{shot}/attempt-001/frame"
                        )
                        atomic_write_json(scene_root / "submitted-workflow.json", graph)
                    runtime.report(
                        f"Generating {shot} once ({size[0]}x{size[1]}, 129 frames, 40 steps).",
                        flush=True,
                    )
                    prompt_id = runtime.submit_once(
                        client,
                        graph,
                        scene_root / "submission.json",
                        f"v21-{run_root.name}-{shot}",
                        binding={
                            "run": run_root.name,
                            "shot": shot,
                            "attempt": "attempt-001",
                            "workflow_sha256": sha256_json(graph),
                            "input_sha256": reference["sha256"],
                            "seed": graph["high_sampler"]["inputs"]["noise_seed"],
                            "settings": {
                                "width": size[0],
                                "height": size[1],
                                "frames": 129,
                                "steps": 40,
                            },
                            "output_prefix": graph["save_frames"]["inputs"][
                                "filename_prefix"
                            ],
                            "provider_instance": client.provider_instance,
                        },
                    )
                    history = runtime.wait_history(
                        client, prompt_id, scene_root / "submission.json"
                    )
                    runtime.collect_frames(client, history, prompt_id, scene_root, size)
                    lifecycle.verify_checkpoint(
                        run_root, shot, manifest, write_proof=True
                    )
                    runtime.report(
                        f"{shot}: 129 lossless frames saved and verified.", flush=True
                    )
            # Owned ComfyUI process has exited, releasing VRAM before NCNN tools.
        # Also applies to finishing-only resumes with all raw checkpoints present.
        if runtime.existing_server_available(run_root):
            with recovery.existing_server(run_root) as client:
                runtime.handoff_gpu(client)
        clips = {}
        for shot in scene_ids:
            prep.contained(run_root / shot)
            runtime.verified_frames(run_root / shot, size)
            runtime.report(f"Finishing {shot} locally.", flush=True)
            runtime.publish_stage(run_root / shot, "finalizing", "local_postproduction")
            setattr(finish, "print", runtime.report)
            clips[shot] = finish.finish_scene(manifest["tools"], run_root / shot, size)
        output = prep.contained(run_root / "deliveries" / uuid4().hex[:8])
        report = finish.finish_package(
            manifest["tools"], clips, root / "prepared", output, digest, silent=silent
        )
        prep.verify_prepared()
        for shot in scene_ids:
            runtime.verified_frames(run_root / shot, size)
        files = [
            prep.file_record(path)
            for path in sorted(output.rglob("*"))
            if path.is_file()
        ]
        atomic_write_json(
            run_root / "delivery.json", {"report": report, "files": files}
        )
        runtime.publish_stage(run_root / "shot-007", "complete")
        return report  # type: ignore[no-any-return]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=("prepare", "create", "status", "recover", "resume"),
        nargs="?",
        default="prepare",
    )
    parser.add_argument(
        "--profile", choices=("quality", "low-memory"), default="quality"
    )
    parser.add_argument("--new-run", action="store_true")
    parser.add_argument("--silent", action="store_true")
    parser.add_argument("--run-id")
    args = parser.parse_args()
    if args.action != "create" and (args.new_run or args.silent):
        parser.error("--new-run and --silent apply only to create")
    if args.action in {"recover", "resume"} and not args.run_id:
        parser.error("Recover/Resume require --run-id; they never create a new run")
    try:
        if args.action in {"recover", "resume"}:
            result = sibling("recovery").recover(
                args.run_id, resume=args.action == "resume"
            )
        elif args.action == "create":
            result = create(
                profile=args.profile, new_run=args.new_run, silent=args.silent
            )
        elif args.action == "status":
            result = status()
        else:
            prep = sibling("prepare")
            with production_lease(prep.contained(prep.ROOT)):
                prepared = prep.prepare()
            result = {
                "status": prepared["status"],
                "generationStarted": False,
                "providerCalls": 0,
                "preparationDigest": prepared["digest"],
                "frames": 1440,
                "fps": 24,
                "references": len(prepared["references"]),
                "inferenceQualification": "not-run",
            }
        print(json.dumps(result, indent=2), flush=True)
    except KeyboardInterrupt:
        print(
            "Stopped by operator. Saved attempts are preserved; no automatic generation retry.",
            file=sys.stderr,
        )
        raise SystemExit(130) from None
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"V2.1 stopped: {exc}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
