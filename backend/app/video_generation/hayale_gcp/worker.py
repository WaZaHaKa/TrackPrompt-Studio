"""Autonomous, generation-fenced single-task Batch production worker."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import tarfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..jsonio import sha256_file, sha256_json
from .cloud import (
    AttachedIdentity,
    BeforeSubmission,
    CloudError,
    GcsClient,
    StateConflict,
    SubmissionRejected,
    SubmissionUnknown,
    VertexClient,
)
from .media import MediaError
from .state import ManifestStore, now

DELIVERY_NAMES = ("Hayale-Flowers-Promo-1080p.mp4", "Hayale-Flowers-Promo-verification.json", "Hayale-Flowers-Promo-delivery-report.md")
_MOTION = {
    "logo": "An elegant premium florist logo shot. Preserve the supplied logo geometry, exact Hebrew lettering and proportions completely. Only extremely restrained light and background motion; do not redraw, transform or morph any lettering.",
    "exterior": "A premium florist storefront shot with a gentle lateral camera travel. Preserve the exact real shop architecture, existing signage and flower placement. Add no signs, text or people.",
    "customer": "A respectful portrait of the supplied real adult customer with flowers. Preserve the same recognizable person, face geometry, clothing and context. Only subtle natural micro-motion and restrained camera movement; no speech, testimonial, new text or drastic body or facial movement.",
    "product": "A premium hero shot of the supplied real floral arrangement. A restrained slow camera push with gentle depth, preserving flower and arrangement identity, color, wrapping and composition.",
    "vibe": "An elegant premium florist atmosphere shot. Restrained natural depth and parallax, believable floral detail, preserving the supplied environment and subjects without morphing.",
    "tour": "A gentle interior tour of the supplied real florist shop. Restrained forward or lateral camera movement, preserving the room layout, shelving and architecture; no invented crowds or signs.",
}


def _smoke_runtime_proof(project: str, account: str, verified: bool) -> dict[str, Any]:
    if (verified is not True or platform.system() != "Linux"
            or project != "nexus-core-481713"
            or account != "trackprompt-hayale-worker@nexus-core-481713.iam.gserviceaccount.com"
            or not os.environ.get("BATCH_JOB_UID") or os.environ.get("BATCH_TASK_INDEX") != "0"):
        raise CloudError("verified_attached_smoke_runtime_required")
    return {"provider": "gcp-batch", "projectId": project, "serviceAccount": account,
            "jobUid": os.environ["BATCH_JOB_UID"], "taskIndex": "0", "identityVerified": True}


def object_io_smoke(storage: GcsClient, prefix: str, work_root: Path, *, project: str,
                    service_account: str, identity_verified: bool = False) -> dict[str, Any]:
    """Exercise production GCS transport with two disposable, non-media objects."""
    if not re.fullmatch(r"hayale-final/hayale-gcp16-[a-f0-9]{24}/smoke-object-io/[a-f0-9]{24}/", prefix):
        raise CloudError("cloud_smoke_prefix_invalid")
    runtime = _smoke_runtime_proof(project, service_account, identity_verified)
    nonce = uuid4().hex
    child = prefix + nonce + "/"
    local = work_root / nonce
    local.mkdir(parents=True, exist_ok=True)
    categories: dict[str, dict[str, Any]] = {}
    result: dict[str, Any] = {"schemaVersion": 1, "mode": "cloud-object-io-smoke", "ok": False,
        "runtime": runtime, "smokePrefix": prefix, "probePrefix": child, "startedAt": now(),
        "categories": categories, "generationSubmitted": False, "productionInputsUploaded": False,
        "cleanup": {"status": "not-started", "retentionEnforced": False}}

    def checked(category: str, passed: bool, **proof: Any) -> None:
        categories[category] = {"ok": passed, **proof}
        if not passed:
            raise CloudError("object_io_smoke_" + category + "_failed")

    def publish_receipt() -> None:
        name = prefix + "result.json"
        current = storage.metadata(name)
        storage.put_json(name, result, if_generation_match=current["generation"] if current else 0)

    try:
        bucket = storage.bucket_metadata()
        checked("bucket_metadata", bucket.get("name") == storage.bucket)
        payload = b"TrackPrompt Hayale disposable object-I/O probe v1\n" + nonce.encode("ascii")
        original = local / "payload.bin"
        original.write_bytes(payload)
        expected_sha = sha256_file(original)
        payload_name, state_name = child + "payload.bin", child + "state.json"
        uploaded = storage.upload_file(payload_name, original, if_generation_match=0,
                                       content_type="application/octet-stream")
        checked("object_create", uploaded.get("sha256") == expected_sha and uploaded.get("size") == len(payload),
                generation=str(uploaded.get("generation")), objectIdentitySha256=sha256_json(payload_name))
        generation = str(uploaded["generation"])
        metadata = storage.metadata(payload_name)
        checked("object_metadata", bool(metadata and str(metadata.get("generation")) == generation
                and int(metadata.get("size", -1)) == len(payload)))
        pinned = storage.metadata(payload_name, generation)
        checked("generation_bound_read", bool(pinned and str(pinned.get("generation")) == generation),
                generation=generation)
        downloaded = local / "downloaded.bin"
        download = storage.download(payload_name, downloaded, generation=generation, expected_sha256=expected_sha)
        checked("object_download", downloaded.read_bytes() == payload and str(download.get("generation")) == generation)
        checked("object_sha256", sha256_file(downloaded) == expected_sha, sha256=expected_sha)
        listing = storage.list_objects(child)
        checked("object_list", any(item.get("name") == payload_name and str(item.get("generation")) == generation
                                    for item in listing) and all(item.get("name", "").startswith(child) for item in listing),
                matchedObjects=len(listing))
        state_v1 = {"schemaVersion": 1, "nonce": nonce, "revision": 1, "payloadSha256": expected_sha}
        first = storage.put_json(state_name, state_v1, if_generation_match=0)
        read_v1, pinned_v1 = storage.get_json(state_name, str(first["generation"]))
        checked("json_create", read_v1 == state_v1 and str(pinned_v1["generation"]) == str(first["generation"]))
        state_v2 = {**state_v1, "revision": 2}
        second = storage.put_json(state_name, state_v2, if_generation_match=str(first["generation"]))
        checked("json_cas_update", str(second["generation"]) != str(first["generation"]),
                previousGeneration=str(first["generation"]), currentGeneration=str(second["generation"]))
        read_v2, meta_v2 = storage.get_json(state_name)
        checked("json_read_after_update", read_v2 == state_v2 and str(meta_v2["generation"]) == str(second["generation"]))
        # A pinned old generation may legitimately be absent without versioning.
        # Record the bucket's observed behavior instead of calling it an I/O fault.
        try:
            prior_state, prior_meta = storage.get_json(state_name, str(first["generation"]))
            checked("prior_generation_behavior", prior_state == state_v1 and str(prior_meta["generation"]) == str(first["generation"]),
                    behavior="retained-version-readable")
        except CloudError as exc:
            if exc.status != 404:
                raise
            checked("prior_generation_behavior", True, behavior="replaced-generation-unavailable", httpStatus=404)
        updated = storage.update_metadata(payload_name, {"smokeProbe": "metadata-update"},
                                           generation=generation, required_prefix=child)
        checked("object_metadata_update", updated.get("metadata", {}).get("smokeProbe") == "metadata-update"
                and updated.get("metadata", {}).get("sha256") == expected_sha
                and str(updated.get("generation")) == generation,
                metageneration=str(updated.get("metageneration")))
        cleanup_targets = [(payload_name, generation), (state_name, str(second["generation"])),
                           (state_name, str(first["generation"]))]
        retained: list[dict[str, str]] = []
        for object_name, object_generation in cleanup_targets:
            if storage.metadata(object_name, object_generation) is None:
                continue
            try:
                storage.delete_object(object_name, generation=object_generation, required_prefix=child)
                if storage.metadata(object_name, object_generation) is not None:
                    raise CloudError("object_io_smoke_cleanup_unconfirmed")
            except CloudError as exc:
                if exc.status != 403:
                    raise
                retained.append({"object": object_name, "generation": object_generation})
        categories["object_delete"] = {"ok": not retained, "optional": True,
                                       "status": "retained-permission-denied" if retained else "passed"}
        result["cleanup"] = {"status": "retained-permission-denied" if retained else "deleted",
            "retainedObjects": retained, "maximumDisposableObjectNames": 2,
            "retentionEnforced": not retained, "receiptRetained": True,
            "retentionNote": "Only this smoke's disposable objects are eligible for deletion. No bucket retention policy was changed."
                if not retained else "Two bounded disposable object names remain as smoke evidence because deletion was denied; no automatic expiry is claimed."}
        result.update(ok=True, completedAt=now(), metadataVerified=True)
        publish_receipt()
        return result
    except Exception as exc:
        error = exc.to_dict() if isinstance(exc, CloudError) else {"code": "object_io_smoke_failed"}
        result.update(completedAt=now(), error=error)
        try:
            publish_receipt()
        except Exception:
            pass
        print(json.dumps(result), flush=True)
        raise


def generation_request(manifest: dict[str, Any], scene: dict[str, Any], number: int) -> dict[str, Any]:
    role = scene["role"].split("-", 1)[0]
    prompt = _MOTION[role]
    if number > 1:
        prompt += " Use simpler, slower camera movement and preserve the original subjects and source composition."
    prefix = manifest["gcsRoot"].rstrip("/") + f"/generation/{scene['sceneId']}/attempt-{number:02d}/"
    seed = int(hashlib.sha256(f"{manifest['runId']}:{scene['sceneId']}".encode()).hexdigest()[:8], 16)
    payload = {"instances": [{"prompt": prompt, "image": {
        "gcsUri": f"gs://{manifest['bucket']}/{scene['seed']['object']}", "mimeType": "image/png"}}],
        "parameters": {"storageUri": f"gs://{manifest['bucket']}/{prefix}", "sampleCount": 1,
            "durationSeconds": 4, "resolution": "1080p", "aspectRatio": "16:9", "generateAudio": False,
            "enhancePrompt": True, "personGeneration": "allow_adult" if role == "customer" else "disallow",
            "seed": seed, "compressionQuality": "optimized",
            "negativePrompt": "No invented lettering, distorted subjects, unstable morphing, speech or extreme motion."}}
    omissions = scene.get("optionalParameterOmissions", [])
    if not isinstance(omissions, list) or not set(omissions) <= {"compressionQuality", "enhancePrompt"}:
        raise CloudError("provider_request_repair_scope_invalid")
    parameters = payload["parameters"]
    assert isinstance(parameters, dict)
    for parameter in omissions:
        parameters.pop(parameter, None)
    return payload


class CloudWorker:
    def __init__(self, storage: GcsClient, manifest_object: str, provider: VertexClient, media: Any,
                 work_root: Path, *, sleep: Callable[[float], None] = time.sleep,
                 progress: Callable[[dict[str, Any]], None] | None = None) -> None:
        self.storage, self.provider, self.media = storage, provider, media
        self.store = ManifestStore(storage, manifest_object)
        self.manifest = self.store.value
        self.root: Path = work_root / str(self.manifest["runId"])
        self.root.mkdir(parents=True, exist_ok=True)
        self.sleep, self.progress = sleep, progress
        self.token = uuid4().hex

    def _save(self, stage: str) -> None:
        self.manifest["status"] = stage
        self.manifest["worker"] = {"token": self.token, "jobUid": os.getenv("BATCH_JOB_UID"),
                                    "taskIndex": os.getenv("BATCH_TASK_INDEX"), "heartbeatAt": now()}
        attempts = [a for s in self.manifest["scenes"] for a in s.get("attempts", [])]
        cost = self.manifest.setdefault("cost", {})
        cost["observedAcceptedOperations"] = sum(bool(a.get("operationName")) for a in attempts)
        cost["uncertainSubmissions"] = sum(a.get("status") in {"submission_intent", "submission_unknown"} for a in attempts)
        cost["estimatedReservedGenerationUsd"] = round(sum(a.get("status") not in {
            "not_submitted", "request_rejected", "request_validation_failed"} for a in attempts)
            * 4 * float(cost.get("rateUsdPerGeneratedSecond", cost.get("generationRateUsdPerSecond", .1))), 4)
        cost["invoicedCostUsd"] = None
        self.store.save()
        if self.progress:
            try:
                self.progress({"runId": self.manifest["runId"], "stage": stage,
                               "verifiedScenes": sum(bool(s.get("output")) for s in self.manifest["scenes"]),
                               "totalScenes": 16})
            except Exception:
                pass  # WNP/log transport is observation, never execution authority.

    def _object(self, suffix: str) -> str:
        return str(self.manifest["gcsRoot"]).rstrip("/") + "/" + suffix

    def _download(self, binding: dict[str, Any], path: Path) -> Path:
        self.storage.download(binding["object"], path, generation=str(binding["generation"]),
                              expected_sha256=binding.get("sha256"))
        return path

    def _seed(self, scene: dict[str, Any]) -> None:
        source = self.manifest["sources"][scene["role"]]
        if scene.get("seed"):
            seed = scene["seed"]
            meta = self.storage.metadata(seed["object"], str(seed["generation"]))
            if (not meta or seed.get("sourceSha256") != source["sha256"]
                    or meta.get("metadata", {}).get("sha256") != seed["sha256"]):
                raise CloudError("retained_seed_binding_changed")
            return
        self._save("seed_preparation")
        path = self._download(source, self.root / "inputs" / (scene["role"] + ".original"))
        target = self.root / "seeds" / (scene["sceneId"] + ".png")
        recipe = {**source.get("seedRecipe", {}), **scene.get("seedRecipe", {}), "sourceSha256": source["sha256"]}
        proof = self.media.prepare_seed(path, target, scene["role"], recipe)
        if proof.get("width") != 1920 or proof.get("height") != 1080 or proof.get("sha256") != sha256_file(target):
            raise CloudError("cloud_seed_verification_failed")
        receipt = self.storage.upload_file(self._object("seeds/" + scene["sceneId"] + ".png"), target,
                                           if_generation_match=0, content_type="image/png")
        scene["seed"] = {**receipt, "sourceSha256": source["sha256"], "preparation": proof}
        self._save("seed_preparation")

    def _acceptance_object(self, scene: dict[str, Any], attempt: dict[str, Any]) -> str:
        return self._object(f"state/operations/{scene['sceneId']}/{attempt['attemptId']}.json")

    def _recover_intent(self, scene: dict[str, Any], attempt: dict[str, Any]) -> None:
        receipt_object = self._acceptance_object(scene, attempt)
        if self.storage.metadata(receipt_object):
            receipt, _ = self.storage.get_json(receipt_object)
            if (receipt.get("runId") != self.manifest["runId"] or receipt.get("sceneId") != scene["sceneId"]
                    or receipt.get("attemptId") != attempt["attemptId"] or receipt.get("requestDigest") != attempt["requestDigest"]
                    or not self.provider.valid_operation(receipt.get("operationName"))):
                raise CloudError("provider_acceptance_receipt_mismatch")
            attempt.update(operationName=receipt["operationName"], status="accepted", acceptedAt=receipt["acceptedAt"])
            self._save("provider_polling")
            return
        prefix = attempt["storagePrefix"]
        objects = self.storage.list_objects(prefix)
        # An exclusive-prefix object is retained as evidence, never turned into
        # an invented operation name or proof that another POST is safe.
        attempt["outputReconciliation"] = {"checkedAt": now(), "objects": [
            {"object": x["name"], "generation": str(x["generation"]), "size": int(x["size"])} for x in objects]}
        attempt["status"] = "submission_unknown"
        self._save("blocked_submission_unknown")
        raise SubmissionUnknown()

    def _new_attempt(self, scene: dict[str, Any]) -> dict[str, Any]:
        attempts = scene.setdefault("attempts", [])
        if sum(a.get("status") not in {"not_submitted", "request_rejected", "request_validation_failed"} for a in attempts) >= 3 or len(attempts) >= 6:
            raise CloudError("scene_generation_attempt_limit")
        seed = scene["seed"]
        latest_seed = self.storage.metadata(seed["object"])
        if (not latest_seed or str(latest_seed["generation"]) != str(seed["generation"])
                or latest_seed.get("metadata", {}).get("sha256") != seed["sha256"]):
            raise CloudError("latest_seed_generation_changed")
        number = len(attempts) + 1
        payload = generation_request(self.manifest, scene, number)
        digest = sha256_json(payload)
        attempt = {"attemptId": f"{scene['sceneId']}-attempt-{number:02d}-{digest[:12]}", "attemptNumber": number,
                   "requestDigest": digest, "request": payload, "sourceSha256": self.manifest["sources"][scene["role"]]["sha256"],
                   "seedSha256": scene["seed"]["sha256"], "status": "submission_intent", "intentOwner": self.token,
                   "submissionIntentAt": now(), "storagePrefix": payload["parameters"]["storageUri"].split("/", 3)[3]}
        attempts.append(attempt)
        self._save("generation")  # This conditional durable write MUST precede POST.
        try:
            response = self.provider.submit_once(payload)
        except BeforeSubmission as exc:
            attempt.update(status="not_submitted", errorCode=exc.code, observedAt=now())
            self._save("generation")
            if exc.retryable and len(attempts) < 6:
                self.sleep(5)
                return attempt
            raise
        except SubmissionRejected as exc:
            attempt.update(status="request_rejected", errorCode=exc.code, httpStatus=exc.status,
                           rejectionEvidence=exc.evidence, observedAt=now())
            if exc.status in {400, 422} and self._repair_optional(scene, attempt, exc.evidence or {}):
                attempt["status"] = "request_validation_failed"
                self._save("generation")
                return attempt
            evidence_text = json.dumps(exc.evidence or {}).lower()
            hard_limit = any(text in evidence_text for text in ("daily", "per day", "billing", "disabled", "limit is 0", "limit: 0"))
            if exc.status in {401, 429} and not hard_limit and len(attempts) < 6:
                attempt["safeRetryBeforeAcceptance"] = True
                if exc.status == 401:
                    self.provider.refresh_auth()
                self._save("retrying")
                self.sleep(5 if exc.status == 401 else 30)
                return attempt
            self._save("blocked_provider_request")
            raise
        except SubmissionUnknown:
            attempt["status"] = "submission_unknown"
            self._save("blocked_submission_unknown")
            self._recover_intent(scene, attempt)
            return attempt
        if not self.provider.valid_operation(response.get("name")):
            attempt["status"] = "submission_unknown"
            self._save("blocked_submission_unknown")
            raise SubmissionUnknown()
        accepted = {"runId": self.manifest["runId"], "sceneId": scene["sceneId"],
                    "attemptId": attempt["attemptId"], "requestDigest": digest,
                    "operationName": response["name"], "acceptedAt": now(), "rawResponse": response}
        self.storage.put_json(self._acceptance_object(scene, attempt), accepted, if_generation_match=0)
        attempt.update(status="accepted", operationName=response["name"], acceptedAt=accepted["acceptedAt"])
        self._save("provider_polling")
        return attempt

    def _repair_optional(self, scene: dict[str, Any], attempt: dict[str, Any], evidence: dict[str, Any]) -> bool:
        error = evidence.get("error", {})
        message = error.get("message", "") if isinstance(error, dict) else ""
        if (not isinstance(message, str) or not re.search(
                r"unsupported|not supported|unrecognized|unknown\s+(?:field|parameter)|not allowed", message, re.I)):
            return False
        omissions = scene.setdefault("optionalParameterOmissions", [])
        parameters = attempt["request"]["parameters"]
        for parameter in ("compressionQuality", "enhancePrompt"):
            if parameter in omissions or parameter not in parameters or not re.search(r"\b" + parameter + r"\b", message):
                continue
            if parameter == "enhancePrompt" and parameters[parameter] is not True:
                return False
            omissions.append(parameter)
            scene.setdefault("requestRepairs", []).append({"parameterOmitted": parameter,
                "basedOnAttemptId": attempt["attemptId"], "evidenceSha256": sha256_json(evidence),
                "evidence": evidence, "repairedAt": now(),
                "basis": "Positive unsupported optional-parameter rejection; Veo 3 enhancement remains enabled by its default. "
                         "Native 1080p, four seconds, person policy and no-audio settings are unchanged."})
            return True
        return False

    def _poll(self, scene: dict[str, Any], attempt: dict[str, Any]) -> None:
        started = time.monotonic()
        read_failures = 0
        while not attempt.get("providerOutput"):
            if time.monotonic() - started > 7200:
                raise CloudError("provider_poll_deadline", retryable=True)
            try:
                response = self.provider.poll(attempt["operationName"])
                read_failures = 0
            except CloudError as exc:
                read_failures += 1
                attempt["lastReadError"] = {"code": exc.code, "observedAt": now()}
                self._save("provider_polling")
                if not exc.retryable or read_failures >= 8:
                    raise
                self.sleep(min(60, 2 ** read_failures))
                continue
            if response.get("name") != attempt["operationName"]:
                raise CloudError("provider_operation_identity_mismatch")
            attempt["lastProviderObservationAt"] = now()
            if response.get("done") is not True:
                self._save("provider_polling")
                self.sleep(10)
                continue
            result_object = self._object(f"generation/{scene['sceneId']}/attempt-{attempt['attemptNumber']:02d}/terminal-response.json")
            self.storage.put_json(result_object, response, if_generation_match=0)
            attempt["terminalResponseObject"] = result_object
            error = response.get("error")
            videos = response.get("response", {}).get("videos", [])
            if error or not videos:
                attempt.update(status="terminal_failed", providerError=error or {"code": "filtered"}, terminalAt=now())
                if isinstance(error, dict) and error.get("code") == 3 and not videos and self._repair_optional(scene, attempt, response):
                    attempt["status"] = "request_validation_failed"
                    self._save("generation")
                    return
                self._save("generation")
                if isinstance(error, dict) and error.get("code") in {3, 7, 8, 9, 16}:
                    attempt["retryAllowed"] = False
                    self._save("blocked_provider_request")
                    raise CloudError("provider_required_capability_or_access_unavailable")
                return
            if len(videos) != 1 or not isinstance(videos[0], dict):
                raise CloudError("provider_output_contract_invalid")
            uri = videos[0].get("gcsUri", "")
            expected = f"gs://{self.storage.bucket}/{attempt['storagePrefix']}"
            if not isinstance(uri, str) or not uri.startswith(expected) or ".." in uri.split("/"):
                raise CloudError("provider_output_prefix_mismatch")
            name = uri.removeprefix(f"gs://{self.storage.bucket}/")
            metadata = self.storage.metadata(name)
            if not metadata or int(metadata.get("size", 0)) <= 0:
                raise CloudError("provider_output_not_available", retryable=True)
            attempt.update(status="transfer", providerOutput=self.storage.receipt(metadata), terminalAt=now())
            self._save("transfer")

    def _scene(self, scene: dict[str, Any]) -> Path:
        target: Path = self.root / "clips" / (str(scene["sceneId"]) + ".mp4")
        if scene.get("output"):
            output = scene["output"]
            recorded = [a for a in scene.get("attempts", []) if a.get("attemptId") == output.get("attemptId")]
            if (len(recorded) != 1 or recorded[0].get("status") != "verified"
                    or output.get("operationName") != recorded[0].get("operationName")
                    or not self.provider.valid_operation(output.get("operationName"))
                    or output.get("sourceSha256") != self.manifest["sources"][scene["role"]]["sha256"]
                    or output.get("seedSha256") != scene["seed"]["sha256"]
                    or output.get("verification", {}).get("technicalVerified") is not True
                    or output.get("sha256") != output.get("verification", {}).get("sha256")
                    or not output["object"].startswith(recorded[0]["storagePrefix"])):
                raise CloudError("verified_scene_evidence_changed")
            return self._download(scene["output"], target)
        while not scene.get("output"):
            attempts = scene.setdefault("attempts", [])
            attempt = attempts[-1] if attempts else None
            if attempt and attempt["status"] in {"submission_intent", "submission_unknown"}:
                self._recover_intent(scene, attempt)
            if attempt and attempt["status"] == "request_rejected" and not attempt.get("safeRetryBeforeAcceptance"):
                raise CloudError(str(attempt.get("errorCode", "provider_request_rejected")))
            if attempt and attempt.get("retryAllowed") is False:
                raise CloudError("provider_required_capability_or_access_unavailable")
            if attempt is None or attempt["status"] in {"terminal_failed", "not_submitted", "request_validation_failed", "request_rejected"}:
                attempt = self._new_attempt(scene)
            if attempt["status"] in {"not_submitted", "request_validation_failed", "request_rejected"}:
                continue
            if not attempt.get("providerOutput"):
                self._poll(scene, attempt)
            if attempt["status"] in {"terminal_failed", "request_validation_failed"}:
                continue
            binding = attempt["providerOutput"]
            receipt = self.storage.download(binding["object"], target, generation=str(binding["generation"]),
                                             expected_sha256=binding.get("sha256"))
            self._save("scene_verification")
            proof = self.media.verify_scene(target)
            if (proof.get("technicalVerified") is not True or proof.get("sha256") != receipt["sha256"]
                    or any(proof.get(k) != v for k, v in {"width": 1920, "height": 1080, "fps": 24, "frames": 96}.items())):
                raise CloudError("native_scene_verification_failed")
            if any(other.get("output", {}).get("sha256") == receipt["sha256"] for other in self.manifest["scenes"] if other is not scene and other.get("output")):
                raise CloudError("duplicate_provider_output_across_scenes")
            attempt.update(status="verified", verifiedAt=now(), clipSha256=receipt["sha256"])
            scene["output"] = {**receipt, "verification": proof, "attemptId": attempt["attemptId"],
                               "operationName": attempt["operationName"], "sourceSha256": attempt["sourceSha256"],
                               "seedSha256": attempt["seedSha256"]}
            self._save("scene_verification")
        return target

    def _finish(self, clips: list[Path]) -> None:
        records = [{"sceneId": s["sceneId"], "sourceSha256": s["output"]["sourceSha256"],
                    "seedSha256": s["output"]["seedSha256"], "clipSha256": s["output"]["sha256"],
                    "operationName": s["output"]["operationName"], "provider": "veo", "state": "verified"}
                   for s in self.manifest["scenes"]]
        intent = sha256_json({"planDigest": self.manifest["planDigest"], "scenes": records,
                              "arrangement": self.manifest["arrangement"], "soundtrack": self.manifest["soundtrack"]["sha256"]})
        finishing = self.manifest.setdefault("finishing", {"intentDigest": intent, "startedAt": now()})
        if finishing["intentDigest"] != intent:
            raise CloudError("cloud_finishing_inputs_changed")
        self._save("finishing")
        package_object = self._object(f"finishing/package-{intent}.tar")
        package_path = self.root / "finishing" / "verified-package.tar"
        directory = self.root / "finishing" / "delivery"
        directory.mkdir(parents=True, exist_ok=True)
        meta = self.storage.metadata(package_object)
        if meta:
            package = self.storage.receipt(meta)
            if not package.get("sha256"):
                raise CloudError("cloud_finishing_package_unbound")
            self._download(package, package_path)
        else:
            audio = self._download(self.manifest["soundtrack"], self.root / "inputs" / "original-soundtrack")
            arrangement = {**self.manifest["arrangement"], "sourceSha256": self.manifest["soundtrack"]["sha256"]}
            result = self.media.finish(clips, audio, directory / DELIVERY_NAMES[0], arrangement,
                {"runId": self.manifest["runId"], "planDigest": self.manifest["planDigest"], "sceneRecords": records,
                 "sourceSha256": self.manifest["soundtrack"]["sha256"], "cost": self.manifest.get("cost", {})},
                 progress=self._save)
            if result.get("technicalVerified") is not True:
                raise CloudError("cloud_delivery_verification_failed")
            # A single immutable package contains the verified film and receipts.
            # Publishing can resume without encoding another version after a crash.
            with tarfile.open(package_path, "w") as archive:
                for name in DELIVERY_NAMES:
                    archive.add(directory / name, arcname=name, recursive=False)
            package = self.storage.upload_file(package_object, package_path, if_generation_match=0, content_type="application/x-tar")
        finishing["package"] = package
        self._save("final_verification")
        with tarfile.open(package_path, "r:") as archive:
            if {member.name for member in archive.getmembers()} != set(DELIVERY_NAMES) or len(archive.getmembers()) != 3:
                raise CloudError("cloud_finishing_package_invalid")
            for member in archive.getmembers():
                if not member.isfile() or member.size <= 0 or member.size > 2_000_000_000:
                    raise CloudError("cloud_finishing_package_invalid")
                source = archive.extractfile(member)
                if source is None:
                    raise CloudError("cloud_finishing_package_invalid")
                with source, (directory / member.name).open("wb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
        verification = json.loads((directory / DELIVERY_NAMES[1]).read_text())
        film_sha = sha256_file(directory / DELIVERY_NAMES[0])
        if (verification.get("technicalVerified") is not True or verification.get("sha256") != film_sha
                or verification.get("runId") != self.manifest["runId"] or verification.get("planDigest") != self.manifest["planDigest"]
                or any(verification.get(k) != v for k, v in {"width": 1920, "height": 1080, "fps": 24, "frames": 1536}.items())):
            raise CloudError("cloud_delivery_package_verification_mismatch")
        delivery = self.manifest.setdefault("delivery", {})
        self._save("delivery")
        for name, content_type in zip(DELIVERY_NAMES, ("video/mp4", "application/json", "text/markdown"), strict=True):
            delivery[name] = self.storage.upload_file(self._object("delivery/" + name), directory / name,
                                                     if_generation_match=0, content_type=content_type)
            self._save("delivery")
        self.manifest["verifiedAt"] = now()
        self.manifest["finalSha256"] = film_sha
        self._save("cloud_verified")

    def run(self) -> dict[str, Any]:
        if self.manifest.get("status") == "cloud_verified":
            delivery = self.manifest.get("delivery", {})
            if set(delivery) != set(DELIVERY_NAMES):
                raise CloudError("cloud_verified_delivery_incomplete")
            for receipt in delivery.values():
                meta = self.storage.metadata(receipt["object"], str(receipt["generation"]))
                if not meta or meta.get("metadata", {}).get("sha256") != receipt["sha256"]:
                    raise CloudError("cloud_verified_delivery_changed")
            verification, _ = self.storage.get_json(delivery[DELIVERY_NAMES[1]]["object"], delivery[DELIVERY_NAMES[1]]["generation"])
            if (verification.get("technicalVerified") is not True or verification.get("runId") != self.manifest["runId"]
                    or verification.get("planDigest") != self.manifest["planDigest"]
                    or verification.get("sha256") != delivery[DELIVERY_NAMES[0]]["sha256"]):
                raise CloudError("cloud_verified_delivery_changed")
            return self.manifest
        self._save("preparation")
        for scene in self.manifest["scenes"]:
            self._seed(scene)
        clips = [self._scene(scene) for scene in self.manifest["scenes"]]
        self._finish(clips)
        return self.manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Hayale GCP-only autonomous production worker")
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--project", default="nexus-core-481713")
    parser.add_argument("--manifest")
    parser.add_argument("--runtime-service-account", required=True)
    parser.add_argument("--check-runtime", action="store_true")
    parser.add_argument("--smoke-prefix")
    args = parser.parse_args()
    worker: CloudWorker | None = None
    try:
        identity = AttachedIdentity(args.project, args.runtime_service_account)
        storage = GcsClient(args.bucket, identity.token)
        if args.check_runtime:
            from PIL import Image
            if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
                raise CloudError("cloud_media_tools_missing")
            Image.init()
            if not args.smoke_prefix:
                raise CloudError("cloud_smoke_prefix_required")
            result = object_io_smoke(storage, args.smoke_prefix, Path("/tmp/hayale-object-io-smoke"),
                project=args.project, service_account=args.runtime_service_account, identity_verified=True)
            print(json.dumps({**result, "ffmpegAvailable": True, "pillowAvailable": True}))
            return 0
        if not args.manifest:
            raise CloudError("cloud_manifest_required")
        from . import media
        manifest, _ = storage.get_json(args.manifest)
        if manifest.get("runtimeServiceAccount") != args.runtime_service_account or manifest.get("project") != args.project:
            raise CloudError("cloud_runtime_scope_mismatch")
        provider = VertexClient(manifest["project"], manifest["region"], manifest["model"], identity.token)
        with media.cloud_execution(project_id=args.project, job_uid=os.environ["BATCH_JOB_UID"],
                                    task_index=os.environ["BATCH_TASK_INDEX"], identity_verified=True):
            worker = CloudWorker(storage, args.manifest, provider, media, Path("/work"),
                                 progress=lambda value: print(json.dumps(value), flush=True))
            worker.run()
        return 0
    except Exception as exc:
        code = exc.code if isinstance(exc, (CloudError, MediaError)) else "cloud_worker_stage_failed"
        details = exc.to_dict() if isinstance(exc, (CloudError, MediaError)) else {"code": code, "retryable": False}
        if worker is not None and not isinstance(exc, StateConflict):
            try:
                worker.manifest["lastError"] = {**details, "observedAt": now()}
                worker._save("retrying" if bool(getattr(exc, "retryable", False)) else "blocked")
            except Exception:
                pass
        print(json.dumps({"ok": False, "error": code, "errorDetails": details}), flush=True)
        return 75 if isinstance(exc, CloudError) and exc.retryable else 1


if __name__ == "__main__":
    raise SystemExit(main())
