from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.video_generation import gcp_auth as auth


def binding(tmp_path: Path) -> auth.AuthBinding:
    return auth.AuthBinding(
        account="operator@example.test", project_id="test-video-project", bucket="test-video-bucket",
        region="us-central1", model_id="veo-3.1-fast-generate-001",
        config_path=tmp_path / "private" / "auth.json",
    )


def test_binding_rejects_project_change_and_bom_is_supported(tmp_path: Path) -> None:
    bound = binding(tmp_path)
    bound.config_path.parent.mkdir()
    bound.config_path.write_text(json.dumps(bound.to_dict()), encoding="utf-8-sig")
    assert auth.load_binding(config_path=bound.config_path).identity_digest == bound.identity_digest
    with pytest.raises(auth.GcpAuthError, match="reconcil"):
        auth.load_binding(config_path=bound.config_path, project_id="different-project")


@pytest.mark.parametrize("value", [[], {"account": "bad"}, {"schemaVersion": "bad"}])
def test_malformed_binding_has_safe_error(tmp_path: Path, value: object) -> None:
    path = tmp_path / "auth.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(auth.GcpAuthError) as failure:
        auth.load_binding(config_path=path)
    assert failure.value.code == "auth_configuration_invalid"
    assert str(tmp_path) not in str(failure.value)


def test_environment_isolated_and_not_mutated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLOUDSDK_CONFIG", "unrelated-ga4")
    monkeypatch.setenv("CLOUDSDK_AUTH_ACCESS_TOKEN", "private-secret")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "analytics.json")
    original = dict(os.environ)
    result = auth.gcloud_environment(binding(tmp_path))
    assert result["CLOUDSDK_CONFIG"] == str(tmp_path / "private" / "gcloud")
    assert "CLOUDSDK_AUTH_ACCESS_TOKEN" not in result
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in result
    assert dict(os.environ) == original


def test_local_mode_does_not_run_cli_or_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "resolve_gcloud", lambda: "fake")
    monkeypatch.setattr(auth, "validate_private_directory", lambda path: None)
    monkeypatch.setattr(auth, "_run_gcloud", lambda *args, **kwargs: pytest.fail("local check ran CLI"))
    result = auth.check_access(mode="local", binding=binding(tmp_path))
    assert result["ok"] is True
    assert result["networkContacted"] is False
    assert result["tokenAvailable"] is None


@pytest.mark.parametrize("message,code", [
    ("invalid_grant token secret", "auth_required"),
    ("Credentials have been revoked", "auth_required"),
    ("The access token has expired", "auth_required"),
    ("PERMISSION_DENIED secret", "permission_denied"),
    ("Connection reset secret", "network_unavailable"),
    ("API has not been used or it is disabled", "api_disabled"),
    ("quota exceeded", "provider_quota_exhausted"),
])
def test_cli_errors_are_classified_without_exposing_output(message: str, code: str) -> None:
    error = auth.classify_cli_error(message)
    assert error.code == code
    assert "secret" not in str(error)


@pytest.mark.parametrize("output", ["", "noise\nsecret-token", "{\"access_token\":\"secret\"}"])
def test_token_noise_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output: str) -> None:
    monkeypatch.setattr(auth, "validate_private_directory", lambda path: None)
    monkeypatch.setattr(auth, "_run_gcloud", lambda *args, **kwargs: output)
    with pytest.raises(auth.GcpAuthError):
        auth.access_token(binding=binding(tmp_path))


def test_cloud_check_does_not_treat_metadata_as_write_permission(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_cli(bound: auth.AuthBinding, arguments: list[str], **kwargs: object) -> str:
        calls.append(arguments)
        if arguments[:2] == ["auth", "list"]:
            return '[{"account":"operator@example.test","status":"ACTIVE"}]'
        if arguments[:2] == ["auth", "print-access-token"]:
            return "test-token"
        if arguments[:2] == ["projects", "describe"]:
            return '{"projectId":"test-video-project"}'
        if arguments[:2] == ["services", "list"]:
            return '[{"config":{"name":"aiplatform.googleapis.com"}}]'
        if arguments[:3] == ["storage", "buckets", "describe"]:
            return '{"name":"test-video-bucket"}'
        pytest.fail(f"unexpected command {arguments}")

    monkeypatch.setattr(auth, "resolve_gcloud", lambda: "fake")
    monkeypatch.setattr(auth, "validate_private_directory", lambda path: None)
    monkeypatch.setattr(auth, "_run_gcloud", fake_cli)
    monkeypatch.setattr(auth, "_storage_permissions", lambda bound, token: ["storage.objects.get"])
    monkeypatch.setattr(auth, "_project_permissions", lambda bound, token: ["aiplatform.endpoints.predict", "serviceusage.services.use"])
    result = auth.check_access(mode="cloud", binding=binding(tmp_path))
    assert not result["ok"]
    checks = {item["id"]: item for item in result["checks"]}
    assert checks["gcs-bucket-access"]["ok"] is True
    assert checks["gcs-object-permissions"]["ok"] is False
    assert all("predict" not in str(call) and "login" not in call for call in calls)


def test_multiple_accounts_requires_bound_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "resolve_gcloud", lambda: "fake")
    monkeypatch.setattr(auth, "validate_private_directory", lambda path: None)
    monkeypatch.setattr(auth, "_run_gcloud", lambda *args, **kwargs: '[{"account":"other@example.test","status":"ACTIVE"}]')
    result = auth.check_access(mode="cloud", binding=binding(tmp_path))
    assert result["ok"] is False
    assert result["error"]["code"] == "auth_identity_mismatch"


def test_noninteractive_reauth_refuses_before_login(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(auth, "reauthenticate", lambda *args, **kwargs: pytest.fail("login attempted"))
    assert auth.main(["--mode", "reauth", "--noninteractive"]) == 2
    value = json.loads(capsys.readouterr().out)
    assert value["error"]["code"] == "interactive_login_required"


@pytest.mark.parametrize("property_name", ["credential_file_override", "access_token_file", "impersonate_service_account", "login_config_file"])
def test_named_configuration_cannot_override_identity(tmp_path: Path, property_name: str) -> None:
    bound = binding(tmp_path)
    directory = bound.gcloud_directory / "configurations"
    directory.mkdir(parents=True)
    (directory / "config_trackprompt").write_text("[auth]\n" + property_name + " = private-override\n", encoding="utf-8")
    with pytest.raises(auth.GcpAuthError) as failure:
        auth._validate_configuration(bound)
    assert failure.value.code == "auth_identity_mismatch"


def test_explicit_missing_cli_does_not_fall_back_to_global(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRACKPROMPT_GCLOUD_EXECUTABLE", str(tmp_path / "missing-gcloud.cmd"))
    with pytest.raises(auth.GcpAuthError) as failure:
        auth.resolve_gcloud()
    assert failure.value.code == "gcloud_missing"


def test_permission_requests_only_inspect_selected_resources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[object] = []
    monkeypatch.setattr(auth, "_permission_response", lambda request: requests.append(request) or [])
    bound = binding(tmp_path)
    auth._storage_permissions(bound, "private-test-token")
    auth._project_permissions(bound, "private-test-token")
    storage, project = requests
    assert storage.get_method() == "GET"  # type: ignore[attr-defined]
    assert "/test-video-bucket/iam/testPermissions" in storage.full_url  # type: ignore[attr-defined]
    assert project.get_method() == "POST"  # type: ignore[attr-defined]
    assert project.full_url.endswith("/test-video-project:testIamPermissions")  # type: ignore[attr-defined]
    assert b"aiplatform.endpoints.predict" in project.data  # type: ignore[attr-defined]
    assert "private-test-token" not in str(storage) + str(project)


def test_discovery_reads_existing_targets_and_accounts_without_adopting_them(tmp_path: Path) -> None:
    target = tmp_path / "video-generation" / "example" / "v2-preparation" / "operator-target.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"gcpProjectId": "video-project", "gcsBucket": "video-bucket", "private": "do-not-expose"}), encoding="utf-8-sig")
    configs = tmp_path / "global-cli" / "configurations"
    configs.mkdir(parents=True)
    for name, account in (("default", "first@example.test"), ("other", "second@example.test")):
        (configs / ("config_" + name)).write_text("[core]\naccount = " + account + "\nproject = analytics-project\n", encoding="utf-8")
    before = {path: path.read_bytes() for path in [target, *configs.iterdir()]}
    result = auth.discover_binding_hints(data_directory=tmp_path, gcloud_directory=tmp_path / "global-cli")
    assert result["adopted"] is False
    assert result["providerTargets"] == [{"projectId": "video-project", "bucket": "video-bucket"}]
    assert result["accountCandidates"] == ["first@example.test", "second@example.test"]
    assert result["accountCredentialsVerified"] is False
    assert "analytics-project" not in json.dumps(result)
    assert "do-not-expose" not in json.dumps(result)
    assert before == {path: path.read_bytes() for path in before}
