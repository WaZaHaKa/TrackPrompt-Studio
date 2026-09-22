"""One GCS generation-fenced manifest is the cloud execution authority."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any

from ..jsonio import sha256_json
from .cloud import CloudError, GcsClient

ROLES = ("logo", "exterior-01", "exterior-02", "customer-01", "product-01", "vibe-01", "tour-01",
         "customer-02", "product-02", "vibe-02", "tour-02", "customer-03", "product-03", "vibe-03", "tour-03", "logo")


def now() -> str:
    return datetime.now(UTC).isoformat()


def validate_manifest(value: dict[str, Any]) -> None:
    if value.get("schemaVersion") not in {1, "1.0.0"} or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,100}", str(value.get("runId", ""))):
        raise CloudError("cloud_manifest_identity_invalid")
    if (value.get("project") != "nexus-core-481713" or value.get("bucket") != "nexus-brain-481713"
            or value.get("region") != "us-central1" or value.get("model") != "veo-3.1-fast-generate-001"
            or not re.fullmatch(r"[a-f0-9]{64}", str(value.get("planDigest", "")))):
        raise CloudError("cloud_manifest_scope_invalid")
    expected_root = f"hayale-final/{value['runId']}/"
    if value.get("gcsRoot", "").rstrip("/") + "/" != expected_root:
        raise CloudError("cloud_manifest_prefix_invalid")
    sources, scenes = value.get("sources"), value.get("scenes")
    if not isinstance(sources, dict) or set(sources) != set(ROLES) or len(sources) != 15:
        raise CloudError("cloud_manifest_source_roles_invalid")
    if not isinstance(scenes, list) or len(scenes) != 16:
        raise CloudError("cloud_manifest_scene_count_invalid")
    for index, scene in enumerate(scenes):
        if scene.get("sceneId") != f"scene-{index+1:03d}" or scene.get("role") != ROLES[index]:
            raise CloudError("cloud_manifest_scene_mapping_invalid")
        source_recipe = sources[scene["role"]].get("seedRecipe", {})
        if scene.get("seedRecipe", source_recipe) != source_recipe:
            raise CloudError("cloud_manifest_seed_recipe_changed")
        attempts = scene.get("attempts", [])
        if len(attempts) > 6 or len({a.get("attemptId") for a in attempts}) != len(attempts):
            raise CloudError("cloud_manifest_attempt_history_invalid")
        for number, attempt in enumerate(attempts, 1):
            request = attempt.get("request")
            seed = scene.get("seed") or {}
            expected_prefix = expected_root + f"generation/{scene['sceneId']}/attempt-{number:02d}/"
            if (attempt.get("attemptNumber") != number or not isinstance(request, dict)
                    or attempt.get("requestDigest") != sha256_json(request)
                    or attempt.get("storagePrefix") != expected_prefix
                    or request.get("parameters", {}).get("storageUri") != f"gs://{value['bucket']}/{expected_prefix}"
                    or attempt.get("sourceSha256") != sources[scene["role"]]["sha256"]
                    or attempt.get("seedSha256") != seed.get("sha256")):
                raise CloudError("cloud_manifest_attempt_binding_invalid")
    for source in [*sources.values(), value.get("soundtrack")]:
        if (not isinstance(source, dict) or not str(source.get("object", "")).startswith(expected_root + "inputs/")
                or not str(source.get("generation", "")).isdigit()
                or not re.fullmatch(r"[a-f0-9]{64}", str(source.get("sha256", "")))):
            raise CloudError("cloud_manifest_original_binding_invalid")
    if len({v["sha256"] for v in sources.values()}) != 15:
        raise CloudError("cloud_manifest_originals_not_distinct")
    plan = value.get("immutablePlan")
    if not isinstance(plan, dict):
        raise CloudError("cloud_manifest_immutable_plan_missing")
    if plan is not None:
        digest = hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
        if digest != value["planDigest"]:
            raise CloudError("cloud_manifest_plan_digest_mismatch")
        for key in ("project", "bucket", "region", "model", "arrangement", "profile", "policy"):
            if value.get(key) != plan.get(key):
                raise CloudError("cloud_manifest_immutable_plan_changed")
        if plan.get("sceneOrder") != list(ROLES) or plan.get("soundtrack", {}).get("sha256") != value["soundtrack"]["sha256"]:
            raise CloudError("cloud_manifest_immutable_sources_changed")
        for role, source in sources.items():
            expected = plan.get("sources", {}).get(role)
            if not isinstance(expected, dict) or any(source.get(k) != expected.get(k) for k in ("sha256", "mimeType", "seedRecipe")):
                raise CloudError("cloud_manifest_immutable_sources_changed")


class ManifestStore:
    def __init__(self, storage: GcsClient, object_name: str) -> None:
        self.storage, self.object_name = storage, object_name
        self.value, meta = storage.get_json(object_name)
        validate_manifest(self.value)
        if object_name != self.value["gcsRoot"].rstrip("/") + "/state/run-manifest.json":
            raise CloudError("cloud_manifest_object_identity_invalid")
        self.generation = str(meta["generation"])
        self.identity = (self.value["runId"], self.value["planDigest"])

    def save(self) -> None:
        validate_manifest(self.value)
        if (self.value["runId"], self.value["planDigest"]) != self.identity:
            raise CloudError("cloud_manifest_immutable_identity_changed")
        self.value["updatedAt"] = now()
        meta = self.storage.put_json(self.object_name, self.value, if_generation_match=self.generation)
        self.generation = str(meta["generation"])
