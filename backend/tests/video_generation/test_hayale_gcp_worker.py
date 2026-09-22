from __future__ import annotations

import copy
import hashlib
import json

import pytest

from app.video_generation.hayale_gcp.cloud import (
    CloudError,
    StateConflict,
    SubmissionRejected,
    SubmissionUnknown,
)
from app.video_generation.hayale_gcp.state import ROLES, ManifestStore
from app.video_generation.hayale_gcp.worker import CloudWorker, generation_request


class FakeStorage:
    bucket = "nexus-brain-481713"

    def __init__(self):
        self.objects = {}
        self.generation = 0
        self.download_failures = 0

    def write(self, name, data, content_type="application/octet-stream"):
        self.generation += 1
        sha = hashlib.sha256(data).hexdigest()
        meta = {"name": name, "generation": str(self.generation), "size": str(len(data)),
                "contentType": content_type, "metadata": {"sha256": sha}}
        self.objects[name] = (data, meta)
        return dict(meta)

    def metadata(self, name, generation=None):
        value = self.objects.get(name.removeprefix(f"gs://{self.bucket}/"))
        if not value or (generation and str(generation) != value[1]["generation"]):
            return None
        return copy.deepcopy(value[1])

    def get_json(self, name, generation=None):
        meta = self.metadata(name, generation)
        if not meta:
            raise CloudError("missing", status=404)
        return json.loads(self.objects[name][0]), meta

    def put_json(self, name, value, if_generation_match):
        prior = self.metadata(name)
        if str(if_generation_match) != (prior["generation"] if prior else "0"):
            raise StateConflict()
        return self.write(name, json.dumps(value, sort_keys=True).encode(), "application/json")

    def receipt(self, meta, sha256=None):
        return {"object": meta["name"], "generation": meta["generation"], "size": int(meta["size"]),
                "sha256": sha256 or meta["metadata"]["sha256"], "mimeType": meta["contentType"]}

    def upload_file(self, name, source, *, if_generation_match=0, content_type="application/octet-stream"):
        data = source.read_bytes()
        prior = self.metadata(name)
        if prior:
            if hashlib.sha256(data).hexdigest() != prior["metadata"]["sha256"]:
                raise StateConflict()
            return self.receipt(prior)
        return self.receipt(self.write(name, data, content_type))

    def download(self, name, destination, *, generation, expected_sha256=None):
        name = name.removeprefix(f"gs://{self.bucket}/")
        if self.download_failures and "/generation/" in name:
            self.download_failures -= 1
            raise CloudError("transfer_failed", retryable=True)
        meta = self.metadata(name, generation)
        assert meta
        data = self.objects[name][0]
        if expected_sha256:
            assert hashlib.sha256(data).hexdigest() == expected_sha256
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        return self.receipt(meta)

    def list_objects(self, prefix):
        return [copy.deepcopy(meta) for name, (_, meta) in self.objects.items() if name.startswith(prefix)]


class FakeProvider:
    def __init__(self, storage):
        self.storage, self.submissions, self.polls = storage, [], []
        self.unknown = False
        self.failures = 0
        self.poll_failures = 0

    def valid_operation(self, name):
        return isinstance(name, str) and name.startswith("projects/nexus-core-481713/locations/us-central1/publishers/google/models/veo-3.1-fast-generate-001/operations/")

    def submit_once(self, payload):
        self.submissions.append(copy.deepcopy(payload))
        if self.unknown:
            raise SubmissionUnknown()
        return {"name": "projects/nexus-core-481713/locations/us-central1/publishers/google/models/veo-3.1-fast-generate-001/operations/" + str(len(self.submissions))}

    def poll(self, operation):
        self.polls.append(operation)
        if self.poll_failures:
            self.poll_failures -= 1
            raise CloudError("poll_temporarily_unavailable", retryable=True)
        if self.failures:
            self.failures -= 1
            return {"name": operation, "done": True, "error": {"code": 13, "message": "Terminal failure"}}
        index = int(operation.rsplit("/", 1)[1]) - 1
        prefix = self.submissions[index]["parameters"]["storageUri"]
        name = prefix.removeprefix(f"gs://{self.storage.bucket}/") + "result.mp4"
        if self.storage.metadata(name) is None:
            self.storage.write(name, f"generated-original-scene-{index}".encode(), "video/mp4")
        return {"name": operation, "done": True, "response": {"videos": [{"gcsUri": f"gs://{self.storage.bucket}/{name}"}]}}


class FakeMedia:
    def __init__(self):
        self.finished = 0

    def prepare_seed(self, source, target, role, recipe):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"seed-" + source.read_bytes())
        return {"sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "sourceSha256": recipe["sourceSha256"],
                "width": 1920, "height": 1080, "role": role, "cloudExecution": {"verified": True}}

    def verify_scene(self, path):
        return {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "width": 1920, "height": 1080,
                "fps": 24, "frames": 96, "durationSeconds": 4, "technicalVerified": True}

    def finish(self, clips, audio, output, arrangement, provenance, *, progress=None):
        self.finished += 1
        if progress:
            progress("audio_preparation")
            progress("mux")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"final-" + b"".join(p.read_bytes() for p in clips))
        verification = {"technicalVerified": True, "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                        "frames": 1536, "durationSeconds": 64, "width": 1920, "height": 1080, "fps": 24,
                        "runId": provenance["runId"], "planDigest": provenance["planDigest"]}
        vp = output.parent / "Hayale-Flowers-Promo-verification.json"
        rp = output.parent / "Hayale-Flowers-Promo-delivery-report.md"
        vp.write_text(json.dumps(verification))
        rp.write_text("Synthetic cloud-only verification report")
        return {**verification, "deliveryPath": str(output), "verificationPath": str(vp), "reportPath": str(rp)}


def fixture(tmp_path):
    storage = FakeStorage()
    root = "hayale-final/run-001/"
    sources = {}
    for role in dict.fromkeys(ROLES):
        meta = storage.write(root + "inputs/" + role + ".png", role.encode(), "image/png")
        sources[role] = storage.receipt(meta)
    audio = storage.receipt(storage.write(root + "inputs/audio/original.wav", b"soundtrack"))
    manifest = {"schemaVersion": "1.0.0", "runId": "run-001", "planDigest": "a" * 64,
        "project": "nexus-core-481713", "bucket": storage.bucket, "region": "us-central1",
        "model": "veo-3.1-fast-generate-001", "gcsRoot": root, "sources": sources, "soundtrack": audio,
        "scenes": [{"sceneId": f"scene-{n+1:03d}", "role": role, "attempts": [], "seed": None, "output": None} for n, role in enumerate(ROLES)],
        "arrangement": {"segments": [{"sourceStartSeconds": 0, "durationSeconds": 64}]},
        "cost": {"generationRateUsdPerSecond": .1, "estimatedGenerationUsd": 6.4}, "status": "prepared"}
    plan = {key: copy.deepcopy(manifest[key]) for key in ("project", "bucket", "region", "model", "arrangement")}
    plan.update(sources={role: {key: source[key] for key in ("sha256", "mimeType")} for role, source in sources.items()},
                soundtrack={"sha256": audio["sha256"]}, sceneOrder=list(ROLES))
    manifest["immutablePlan"] = plan
    manifest["planDigest"] = hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
    name = root + "state/run-manifest.json"
    storage.put_json(name, manifest, 0)
    provider, media = FakeProvider(storage), FakeMedia()
    return storage, name, provider, media


def test_all_sixteen_native_generated_then_resume_never_regenerates(tmp_path):
    storage, name, provider, media = fixture(tmp_path)
    CloudWorker(storage, name, provider, media, tmp_path, sleep=lambda _: None).run()
    assert len(provider.submissions) == 16 and media.finished == 1
    for payload in provider.submissions:
        assert payload["parameters"]["resolution"] == "1080p"
        assert payload["parameters"]["durationSeconds"] == 4
        assert payload["parameters"]["enhancePrompt"] is True
        assert payload["parameters"]["generateAudio"] is False
        assert payload["instances"][0]["image"]["gcsUri"]
    assert sum(p["parameters"]["personGeneration"] == "allow_adult" for p in provider.submissions) == 3
    final = storage.get_json(name)[0]
    assert final["status"] == "cloud_verified" and len(final["delivery"]) == 3
    CloudWorker(storage, name, provider, media, tmp_path, sleep=lambda _: None).run()
    assert len(provider.submissions) == 16 and media.finished == 1


def test_unknown_submission_persists_intent_and_never_reposts(tmp_path):
    storage, name, provider, media = fixture(tmp_path)
    provider.unknown = True
    for _ in range(2):
        with pytest.raises(CloudError, match="submission_outcome_unknown"):
            CloudWorker(storage, name, provider, media, tmp_path, sleep=lambda _: None).run()
    assert len(provider.submissions) == 1
    assert storage.get_json(name)[0]["scenes"][0]["attempts"][0]["requestDigest"]


def test_accepted_operation_and_transfer_resume_without_new_submission(tmp_path):
    storage, name, provider, media = fixture(tmp_path)
    storage.download_failures = 1
    with pytest.raises(CloudError, match="transfer_failed"):
        CloudWorker(storage, name, provider, media, tmp_path, sleep=lambda _: None).run()
    assert len(provider.submissions) == 1
    polled = len(provider.polls)
    CloudWorker(storage, name, provider, media, tmp_path, sleep=lambda _: None).run()
    assert len(provider.submissions) == 16 and len(provider.polls) == polled + 15


def test_terminal_generation_retries_are_bounded(tmp_path):
    storage, name, provider, media = fixture(tmp_path)
    provider.failures = 10
    with pytest.raises(CloudError, match="scene_generation_attempt_limit"):
        CloudWorker(storage, name, provider, media, tmp_path, sleep=lambda _: None).run()
    assert len(provider.submissions) == 3
    assert len({p["instances"][0]["image"]["gcsUri"] for p in provider.submissions}) == 1
    with pytest.raises(CloudError, match="scene_generation_attempt_limit"):
        CloudWorker(storage, name, provider, media, tmp_path, sleep=lambda _: None).run()
    assert len(provider.submissions) == 3


def test_cas_conflict_prevents_second_worker_submission(tmp_path):
    storage, name, provider, media = fixture(tmp_path)
    stale = CloudWorker(storage, name, provider, media, tmp_path, sleep=lambda _: None)
    current = ManifestStore(storage, name)
    current.value["status"] = "another-worker"
    current.save()
    with pytest.raises(StateConflict):
        stale.run()
    assert not provider.submissions


def test_generation_request_never_uses_old_local_composition(tmp_path):
    storage, name, _, _ = fixture(tmp_path)
    manifest = storage.get_json(name)[0]
    scene = manifest["scenes"][0]
    scene["seed"] = {"object": manifest["gcsRoot"] + "seeds/scene-001.png"}
    request = generation_request(manifest, scene, 1)
    assert request["parameters"]["sampleCount"] == 1
    assert request["parameters"]["aspectRatio"] == "16:9"
    assert "logo" in request["instances"][0]["prompt"].lower()


def test_transient_poll_failure_retries_same_operation(tmp_path):
    storage, name, provider, media = fixture(tmp_path)
    provider.poll_failures = 2
    CloudWorker(storage, name, provider, media, tmp_path, sleep=lambda _: None).run()
    assert len(provider.submissions) == 16
    assert provider.polls[:3] == [provider.polls[0]] * 3


def test_publication_failure_reuses_atomic_cloud_package(tmp_path, monkeypatch):
    storage, name, provider, media = fixture(tmp_path)
    original = storage.upload_file
    failed = False

    def fail_publication(object_name, *args, **kwargs):
        nonlocal failed
        if "/delivery/" in object_name and not failed:
            failed = True
            raise CloudError("publication_transfer_failed", retryable=True)
        return original(object_name, *args, **kwargs)

    monkeypatch.setattr(storage, "upload_file", fail_publication)
    with pytest.raises(CloudError, match="publication_transfer_failed"):
        CloudWorker(storage, name, provider, media, tmp_path, sleep=lambda _: None).run()
    assert len(provider.submissions) == 16 and media.finished == 1
    CloudWorker(storage, name, provider, media, tmp_path / "new-container", sleep=lambda _: None).run()
    assert len(provider.submissions) == 16 and media.finished == 1
    assert storage.get_json(name)[0]["status"] == "cloud_verified"


def test_crash_after_acceptance_receipt_recovers_without_submit(tmp_path, monkeypatch):
    storage, name, provider, media = fixture(tmp_path)
    worker = CloudWorker(storage, name, provider, media, tmp_path, sleep=lambda _: None)
    original = worker._save
    failed = False

    def interrupt(stage):
        nonlocal failed
        if stage == "provider_polling" and not failed:
            failed = True
            raise RuntimeError("crashed after receipt")
        original(stage)

    monkeypatch.setattr(worker, "_save", interrupt)
    with pytest.raises(RuntimeError, match="crashed"):
        worker.run()
    assert len(provider.submissions) == 1
    CloudWorker(storage, name, provider, media, tmp_path, sleep=lambda _: None).run()
    assert len(provider.submissions) == 16


def test_unknown_prefix_artifacts_are_preserved_without_inventing_operation(tmp_path):
    storage, name, provider, media = fixture(tmp_path)
    provider.unknown = True
    with pytest.raises(SubmissionUnknown):
        CloudWorker(storage, name, provider, media, tmp_path, sleep=lambda _: None).run()
    attempt = storage.get_json(name)[0]["scenes"][0]["attempts"][0]
    storage.write(attempt["storagePrefix"] + "candidate.mp4", b"retained output", "video/mp4")
    with pytest.raises(SubmissionUnknown):
        CloudWorker(storage, name, provider, media, tmp_path, sleep=lambda _: None).run()
    attempt = storage.get_json(name)[0]["scenes"][0]["attempts"][0]
    assert len(attempt["outputReconciliation"]["objects"]) == 1
    assert not attempt.get("operationName") and len(provider.submissions) == 1


def test_verified_status_cannot_hide_missing_delivery(tmp_path):
    storage, name, provider, media = fixture(tmp_path)
    manifest, meta = storage.get_json(name)
    manifest["status"] = "cloud_verified"
    storage.put_json(name, manifest, meta["generation"])
    with pytest.raises(CloudError, match="delivery_incomplete"):
        CloudWorker(storage, name, provider, media, tmp_path, sleep=lambda _: None).run()
    assert not provider.submissions


def test_provider_transport_never_retries_unknown_post(monkeypatch):
    import urllib.error

    from app.video_generation.hayale_gcp import cloud
    calls = []

    def unavailable(request, **kwargs):
        calls.append(request)
        raise urllib.error.URLError("connection ended")

    monkeypatch.setattr(cloud, "_open", unavailable)
    provider = cloud.VertexClient("nexus-core-481713", "us-central1", "veo-3.1-fast-generate-001", lambda: "private")
    with pytest.raises(SubmissionUnknown):
        provider.submit_once({"instances": [], "parameters": {}})
    assert len(calls) == 1


def test_provider_token_failure_proves_no_http(monkeypatch):
    from app.video_generation.hayale_gcp import cloud

    def token_failure():
        raise RuntimeError("metadata unavailable")

    monkeypatch.setattr(cloud, "_open", lambda *args, **kwargs: pytest.fail("HTTP must not start"))
    provider = cloud.VertexClient("p", "r", "m", token_failure)
    with pytest.raises(cloud.BeforeSubmission):
        provider.submit_once({})


def test_authenticated_http_redirects_are_not_followed():
    from app.video_generation.hayale_gcp.cloud import _NoRedirect
    assert _NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://untrusted.invalid") is None


@pytest.mark.parametrize("parameter", ["compressionQuality", "enhancePrompt"])
def test_exact_unsupported_optional_parameter_is_repaired_without_degrading_profile(tmp_path, parameter):
    storage, name, provider, media = fixture(tmp_path)
    original = provider.submit_once
    rejected = False

    def submit(payload):
        nonlocal rejected
        if not rejected:
            rejected = True
            raise SubmissionRejected("provider_request_http_400", status=400,
                evidence={"error": {"code": 400, "message": f"Parameter {parameter} is not supported."}})
        return original(payload)

    provider.submit_once = submit
    CloudWorker(storage, name, provider, media, tmp_path, sleep=lambda _: None).run()
    manifest = storage.get_json(name)[0]
    attempts = manifest["scenes"][0]["attempts"]
    assert len(attempts) == 2
    assert attempts[0]["rejectionEvidence"]["error"]["message"]
    assert parameter not in attempts[1]["request"]["parameters"]
    assert attempts[1]["request"]["parameters"]["durationSeconds"] == 4
    assert attempts[1]["request"]["parameters"]["resolution"] == "1080p"
    assert attempts[1]["request"]["parameters"]["generateAudio"] is False
    assert len(provider.submissions) == 16


def test_generic_invalid_argument_is_not_silently_repaired(tmp_path):
    storage, name, provider, media = fixture(tmp_path)

    def submit(payload):
        raise SubmissionRejected("provider_request_http_400", status=400,
                                 evidence={"error": {"code": 400, "message": "Invalid parameters"}})

    provider.submit_once = submit
    with pytest.raises(SubmissionRejected):
        CloudWorker(storage, name, provider, media, tmp_path, sleep=lambda _: None).run()
    assert len(storage.get_json(name)[0]["scenes"][0]["attempts"]) == 1


def test_terminal_rpc_optional_parameter_rejection_gets_one_exact_repair(tmp_path):
    storage, name, provider, media = fixture(tmp_path)
    original = provider.poll
    rejected = False

    def poll(operation):
        nonlocal rejected
        if not rejected:
            rejected = True
            return {"name": operation, "done": True, "error": {"code": 3,
                "message": "Unknown field compressionQuality is not supported."}}
        return original(operation)

    provider.poll = poll
    CloudWorker(storage, name, provider, media, tmp_path, sleep=lambda _: None).run()
    attempts = storage.get_json(name)[0]["scenes"][0]["attempts"]
    assert len(attempts) == 2 and attempts[0]["status"] == "request_validation_failed"
    assert "compressionQuality" not in attempts[1]["request"]["parameters"]


def test_observation_outage_does_not_interrupt_production(tmp_path):
    storage, name, provider, media = fixture(tmp_path)

    def broken_observer(value):
        raise OSError("Companion unavailable")

    CloudWorker(storage, name, provider, media, tmp_path, sleep=lambda _: None, progress=broken_observer).run()
    assert storage.get_json(name)[0]["status"] == "cloud_verified"


@pytest.mark.parametrize("status", [401, 429])
def test_positive_transient_http_rejection_retries_safely(tmp_path, status):
    storage, name, provider, media = fixture(tmp_path)
    original = provider.submit_once
    rejected = False
    refreshed = []
    provider.refresh_auth = lambda: refreshed.append(True)

    def submit(payload):
        nonlocal rejected
        if not rejected:
            rejected = True
            raise SubmissionRejected(f"provider_request_http_{status}", status=status, retryable=True,
                                     evidence={"error": {"message": "Temporarily unavailable"}})
        return original(payload)

    provider.submit_once = submit
    CloudWorker(storage, name, provider, media, tmp_path, sleep=lambda _: None).run()
    attempts = storage.get_json(name)[0]["scenes"][0]["attempts"]
    assert len(attempts) == 2 and attempts[0]["status"] == "request_rejected"
    assert len(provider.submissions) == 16
    assert len(refreshed) == (1 if status == 401 else 0)


def test_hard_daily_quota_http_rejection_stays_blocked(tmp_path):
    storage, name, provider, media = fixture(tmp_path)
    calls = []

    def submit(payload):
        calls.append(payload)
        raise SubmissionRejected("provider_request_http_429", status=429, retryable=True,
                                 evidence={"error": {"message": "Daily quota exceeded"}})

    provider.submit_once = submit
    with pytest.raises(SubmissionRejected):
        CloudWorker(storage, name, provider, media, tmp_path, sleep=lambda _: None).run()
    assert len(calls) == 1


def test_attached_identity_refresh_transport_failure_is_retryable():
    from app.video_generation.hayale_gcp.cloud import AttachedIdentity

    class Credentials:
        valid = False

        def refresh(self, request):
            raise TimeoutError("metadata unavailable")

    identity = AttachedIdentity.__new__(AttachedIdentity)
    identity.credentials = Credentials()
    identity.request = object()
    with pytest.raises(CloudError) as error:
        identity.token()
    assert error.value.retryable and error.value.code == "runtime_identity_refresh_unavailable"


def test_missing_immutable_plan_fails_closed_before_any_generation(tmp_path):
    storage, name, provider, media = fixture(tmp_path)
    manifest, meta = storage.get_json(name)
    del manifest["immutablePlan"]
    storage.put_json(name, manifest, meta["generation"])
    with pytest.raises(CloudError, match="immutable_plan_missing"):
        CloudWorker(storage, name, provider, media, tmp_path, sleep=lambda _: None).run()
    assert not provider.submissions


def test_scene_cannot_override_the_hash_bound_source_preparation(tmp_path):
    storage, name, provider, media = fixture(tmp_path)
    value, meta = storage.get_json(name)
    value['scenes'][0]['seedRecipe'] = {'cropNormalized': [0, 0, 0.5, 0.5]}
    storage.put_json(name, value, meta['generation'])
    with pytest.raises(CloudError, match='cloud_manifest_seed_recipe_changed'):
        CloudWorker(storage, name, provider, media, tmp_path)
    assert not provider.submissions


def test_latest_seed_generation_must_match_before_new_provider_post(tmp_path, monkeypatch):
    storage, name, provider, media = fixture(tmp_path)
    worker = CloudWorker(storage, name, provider, media, tmp_path, sleep=lambda _: None)
    scene = worker.manifest["scenes"][0]
    worker._seed(scene)
    seed = scene["seed"]
    old = storage.metadata(seed["object"])
    storage.write(seed["object"], storage.objects[seed["object"]][0], "image/png")
    original = storage.metadata

    def versioned_metadata(object_name, generation=None):
        if object_name == seed["object"] and generation == seed["generation"]:
            return old
        return original(object_name, generation)

    monkeypatch.setattr(storage, "metadata", versioned_metadata)
    with pytest.raises(CloudError, match="latest_seed_generation_changed"):
        worker._new_attempt(scene)
    assert not provider.submissions
