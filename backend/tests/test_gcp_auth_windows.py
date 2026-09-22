"""Native Windows AST and behavioral tests; all CLI/provider traffic is fake."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from app.video_generation import gcp_auth as auth

pytestmark = pytest.mark.skipif(os.name != "nt", reason="native Windows checks require Windows")
REPOSITORY = Path(__file__).resolve().parents[2]
SCRIPT = REPOSITORY / "tools" / "reauth-gcp.ps1"
SHELLS = [name for name in ("powershell.exe", "pwsh") if shutil.which(name)]


@pytest.fixture
def private_binding(tmp_path: Path) -> auth.AuthBinding:
    bound = auth.AuthBinding("operator@example.test", "test-video-project", "test-video-bucket",
                             "us-central1", "veo-3.1-fast-generate-001", tmp_path / "private" / "auth.json")
    auth._create_private_directory(bound.config_path.parent)
    auth._create_private_directory(bound.gcloud_directory)
    bound.config_path.write_text(json.dumps(bound.to_dict()), encoding="utf-8-sig")
    return bound


@pytest.fixture(params=[".cmd", ".ps1"])
def fake_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> Path:
    directory = tmp_path / "gcloud שלום spaced"
    directory.mkdir()
    implementation = directory / "fake_cli.py"
    implementation.write_text('''
import json, os, sys, time
mode = os.environ.get("TRACKPROMPT_TEST_MODE", "valid")
if mode == "nonzero":
    print("private-secret", file=sys.stderr)
    sys.exit(7)
if mode == "noise":
    print("noise on stdout")
if mode == "timeout":
    time.sleep(10)
if mode == "overflow":
    print("x" * 70000)
    sys.exit(0)
if mode == "environment":
    print(json.dumps({"args":sys.argv[1:], "config":os.environ.get("CLOUDSDK_CONFIG"),
        "account":os.environ.get("CLOUDSDK_CORE_ACCOUNT"), "project":os.environ.get("CLOUDSDK_CORE_PROJECT"),
        "adc":os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"),
        "tokenOverride":os.environ.get("CLOUDSDK_AUTH_ACCESS_TOKEN")}))
elif sys.argv[1:3] == ["auth", "list"]:
    print("not-json" if mode == "malformed" else '[{"account":"operator@example.test","status":"ACTIVE"}]')
elif sys.argv[1:3] == ["auth", "print-access-token"]:
    print("fake-token-do-not-expose")
elif sys.argv[1:3] == ["config", "configurations"]:
    print("created")
elif sys.argv[1:3] == ["auth", "login"]:
    print("fake-private-authorization-code")
else:
    print("{}")
''', encoding="utf-8")
    cli = directory / ("gcloud" + str(request.param))
    if request.param == ".cmd":
        cli.write_text('@"' + sys.executable + '" "%~dp0fake_cli.py" %*\r\n@exit /b %errorlevel%\r\n', encoding="ascii")
    else:
        cli.write_text("& " + auth._ps_literal(sys.executable) + " (Join-Path $PSScriptRoot 'fake_cli.py') @args\nexit $LASTEXITCODE\n", encoding="utf-8-sig")
    monkeypatch.setenv("TRACKPROMPT_GCLOUD_EXECUTABLE", str(cli))
    return cli


@pytest.mark.parametrize("shell", SHELLS)
def test_entrypoint_parses_on_supported_powershell(shell: str) -> None:
    command = ("$errors=$null; $tokens=$null; $null=[System.Management.Automation.Language.Parser]::ParseFile(" +
               auth._ps_literal(str(SCRIPT)) + ",[ref]$tokens,[ref]$errors); if($errors.Count){exit 1}; $PSVersionTable.PSVersion.ToString()")
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", command], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()


def test_launchers_preserve_unicode_spaces_binding_and_parent_environment(private_binding: auth.AuthBinding, fake_cli: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLOUDSDK_CONFIG", "unrelated-ga4")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "analytics.json")
    monkeypatch.setenv("CLOUDSDK_AUTH_ACCESS_TOKEN", "secret")
    monkeypatch.setenv("TRACKPROMPT_TEST_MODE", "environment")
    before = dict(os.environ)
    output = auth._run_gcloud(private_binding, ["inspect", "שלום value"], timeout=10)
    value = json.loads(output)
    assert value["args"][1] == "שלום value"
    assert value["config"] == str(private_binding.gcloud_directory)
    assert value["account"] == private_binding.account
    assert value["project"] == private_binding.project_id
    assert value["adc"] is None and value["tokenOverride"] is None
    assert dict(os.environ) == before


@pytest.mark.parametrize("mode,code", [("nonzero", "gcloud_failed"), ("overflow", "gcloud_output_limit"), ("timeout", "gcloud_timeout")])
def test_native_failures_are_bounded_and_private(private_binding: auth.AuthBinding, fake_cli: Path, monkeypatch: pytest.MonkeyPatch, mode: str, code: str) -> None:
    monkeypatch.setenv("TRACKPROMPT_TEST_MODE", mode)
    with pytest.raises(auth.GcpAuthError) as failure:
        auth._run_gcloud(private_binding, ["auth", "print-access-token"], timeout=1 if mode == "timeout" else 10)
    assert failure.value.code == code
    assert "private-secret" not in str(failure.value)


@pytest.mark.parametrize("mode,code", [("noise", "auth_token_invalid"), ("malformed", "gcloud_output_invalid")])
def test_noisy_or_malformed_output_fails_closed(private_binding: auth.AuthBinding, fake_cli: Path, monkeypatch: pytest.MonkeyPatch, mode: str, code: str) -> None:
    monkeypatch.setenv("TRACKPROMPT_TEST_MODE", mode)
    if mode == "noise":
        with pytest.raises(auth.GcpAuthError) as failure:
            auth.access_token(binding=private_binding)
        assert failure.value.code == code
    else:
        result = auth.check_access(mode="cloud", binding=private_binding)
        assert not result["ok"]
        assert result["error"]["code"] == code


def test_acl_rejects_unapproved_inherited_permission_without_rewriting_it(private_binding: auth.AuthBinding) -> None:
    # Do not broaden an ACL to create a negative fixture. The workspace's inherited
    # OWNER RIGHTS/Users ACL is deliberately not the explicit private SID binding.
    directory = private_binding.config_path.parent.parent / "unapproved inherited acl"
    directory.mkdir()
    command = "$ErrorActionPreference='Stop'; (Get-Acl -LiteralPath " + auth._ps_literal(str(directory)) + ").Sddl"
    environment = {key: value for key, value in os.environ.items() if key.upper() != "PSMODULEPATH"}
    before = subprocess.run(auth._powershell_command(command), capture_output=True, text=True, timeout=20, env=environment)
    assert before.returncode == 0
    with pytest.raises(auth.GcpAuthError) as failure:
        auth.validate_private_directory(directory)
    assert failure.value.code == "auth_acl_denied"
    after = subprocess.run(auth._powershell_command("(Get-Acl -LiteralPath " + auth._ps_literal(str(directory)) + ").Sddl"), capture_output=True, text=True, timeout=20, env=environment)
    assert before.stdout == after.stdout


def test_batch_launcher_rejects_shell_metacharacters(fake_cli: Path) -> None:
    if fake_cli.suffix != ".cmd":
        pytest.skip("PowerShell scripts use literal arguments; this is the batch guard")
    with pytest.raises(auth.GcpAuthError) as failure:
        auth.executable_command(str(fake_cli), ["file&echo-private"])
    assert failure.value.code == "gcloud_launcher_argument_invalid"


@pytest.mark.parametrize("shell", SHELLS)
def test_script_repeated_local_checks_restore_environment_and_keep_json_clean(private_binding: auth.AuthBinding, fake_cli: Path, shell: str) -> None:
    command = ("$env:PYTHONPATH='before-path'; $env:PYTHONIOENCODING='before-encoding'; "
               "$env:TRACKPROMPT_GCLOUD_EXECUTABLE='before-cli'; "
               "$beforeLocation=(Get-Location).Path; "
               "1..2 | ForEach-Object { & " + auth._ps_literal(str(SCRIPT)) +
               " -Mode Local -NonInteractive -ConfigPath " + auth._ps_literal(str(private_binding.config_path)) +
               " -GcloudExecutable " + auth._ps_literal(str(fake_cli)) +
               " -PythonExecutable " + auth._ps_literal(sys.executable) +
               "; if($LASTEXITCODE -ne 0){exit 8} }; "
               "if($env:PYTHONPATH -ne 'before-path' -or $env:PYTHONIOENCODING -ne 'before-encoding' -or "
               "$env:TRACKPROMPT_GCLOUD_EXECUTABLE -ne 'before-cli' -or (Get-Location).Path -ne $beforeLocation){exit 9}")
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command],
                            capture_output=True, text=True, encoding="utf-8", timeout=45)
    assert result.returncode == 0, result.stderr
    values = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    assert len(values) == 2 and all(value["ok"] and not value["networkContacted"] for value in values)


def test_reauthentication_verifies_token_and_never_submits(private_binding: auth.AuthBinding, fake_cli: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Real fake processes exercise config creation/login/token commands without opening Google.
    monkeypatch.setenv("TRACKPROMPT_TEST_MODE", "valid")
    auth.reauthenticate(private_binding)
    assert auth.load_binding(config_path=private_binding.config_path).identity_digest == private_binding.identity_digest
    assert "fake-token" not in private_binding.config_path.read_text(encoding="utf-8")


@pytest.mark.parametrize("shell", SHELLS)
def test_noninteractive_script_never_opens_login(shell: str) -> None:
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT),
                             "-Mode", "Reauth", "-NonInteractive", "-PythonExecutable", sys.executable],
                            capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 2
    assert json.loads(result.stdout)["error"]["code"] == "interactive_login_required"
