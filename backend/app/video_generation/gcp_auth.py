"""TrackPrompt CLI credentials: private binding, bounded processes, no ADC fallback."""
from __future__ import annotations

import argparse
import base64
import configparser
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.subprocess_utils import ProcessTimedOut, run_process_bounded

_LIMIT = 64 * 1024
_MODELS = {"veo-3.1-generate-001", "veo-3.1-fast-generate-001"}
_TOKEN = re.compile(r"[A-Za-z0-9._~+/=-]{8,8192}")


class GcpAuthError(RuntimeError):
    def __init__(self, code: str, message: str, remedy: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.remedy = remedy
        self.retryable = retryable
        self.network_contacted = False

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self), "remedy": self.remedy, "retryable": self.retryable}


def _invalid() -> GcpAuthError:
    return GcpAuthError("auth_configuration_invalid", "TrackPrompt authentication binding is invalid.",
                        "Use tools/reauth-gcp.ps1 -Mode Reauth with the intended account, project and bucket.")


def default_config_path() -> Path:
    override = os.environ.get("TRACKPROMPT_GCP_AUTH_CONFIG")
    data = Path(os.environ.get("TRACKPROMPT_DATA_DIR", str(Path(__file__).resolve().parents[3] / ".trackprompt-data")))
    return Path(override).expanduser().resolve() if override else (data / "gcp-auth" / "auth.json").resolve()


def discover_binding_hints(*, data_directory: Path | None = None, gcloud_directory: Path | None = None) -> dict[str, Any]:
    """Read local configuration hints only. Never adopt them as a credential binding."""
    data = data_directory or Path(os.environ.get("TRACKPROMPT_DATA_DIR", str(Path(__file__).resolve().parents[3] / ".trackprompt-data")))
    default_gcloud = Path(os.environ.get("APPDATA", str(Path.home() / ".config"))) / "gcloud"
    cli_root = gcloud_directory or default_gcloud
    targets: set[tuple[str, str]] = set()
    accounts: set[str] = set()
    for path in sorted((data / "video-generation").glob("*/*/operator-target.json"))[:32]:
        try:
            if path.stat().st_size > _LIMIT:
                continue
            value = json.loads(path.read_text(encoding="utf-8-sig"))
            project = value.get("gcpProjectId", "") if isinstance(value, dict) else ""
            bucket = value.get("gcsBucket", "") if isinstance(value, dict) else ""
            if (isinstance(project, str) and isinstance(bucket, str)
                    and re.fullmatch(r"[a-z][a-z0-9-]{4,61}[a-z0-9]", project)
                    and re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]", bucket)):
                targets.add((project, bucket))
        except (OSError, ValueError):
            continue
    for path in sorted((cli_root / "configurations").glob("config_*"))[:32]:
        try:
            if path.stat().st_size > _LIMIT:
                continue
            config = configparser.RawConfigParser()
            config.read_string(path.read_text(encoding="utf-8-sig"))
            account = config.get("core", "account", fallback="").strip()
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+@-]{2,253}", account):
                accounts.add(account)
        except (OSError, ValueError, configparser.Error):
            continue
    return {"adopted": False, "source": "local-configuration-hints-only",
            "providerTargets": [{"projectId": project, "bucket": bucket} for project, bucket in sorted(targets)],
            "accountCandidates": sorted(accounts), "accountCredentialsVerified": False}


@dataclass(frozen=True)
class AuthBinding:
    account: str
    project_id: str
    bucket: str
    region: str
    model_id: str
    config_path: Path
    configuration: str = "trackprompt"

    def __post_init__(self) -> None:
        if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+@-]{2,253}", self.account)
                or not re.fullmatch(r"[a-z][a-z0-9-]{4,61}[a-z0-9]", self.project_id)
                or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]", self.bucket)
                or self.region != "us-central1" or self.model_id not in _MODELS
                or self.configuration != "trackprompt"):
            raise _invalid()

    @property
    def gcloud_directory(self) -> Path:
        return self.config_path.parent / "gcloud"

    @property
    def identity_digest(self) -> str:
        value = [self.account, self.project_id, self.configuration, str(self.gcloud_directory.resolve())]
        return hashlib.sha256(json.dumps(value, ensure_ascii=True).encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, str]:
        return {"schemaVersion": "1", "account": self.account, "projectId": self.project_id,
                "bucket": self.bucket, "region": self.region, "modelId": self.model_id,
                "configuration": self.configuration}


def load_binding(*, project_id: str | None = None, config_path: Path | None = None) -> AuthBinding:
    path = (config_path or default_config_path()).resolve()
    try:
        if path.stat().st_size > _LIMIT:
            raise _invalid()
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict) or value.get("schemaVersion") != "1":
            raise _invalid()
        keys = ("account", "projectId", "bucket", "region", "modelId", "configuration")
        if any(not isinstance(value.get(key), str) for key in keys):
            raise _invalid()
        bound = AuthBinding(value["account"], value["projectId"], value["bucket"], value["region"],
                            value["modelId"], path, value["configuration"])
    except FileNotFoundError as exc:
        raise GcpAuthError("auth_configuration_missing", "No TrackPrompt authentication binding exists.",
                           "Run tools/reauth-gcp.ps1 -Mode Reauth with the intended account, project and bucket.") from exc
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise _invalid() from exc
    if project_id is not None and bound.project_id != project_id:
        raise GcpAuthError("auth_identity_mismatch", "The run project differs from the credential binding; reconcile before resuming.",
                           "Restore the run's original TrackPrompt binding or reconcile the run explicitly.")
    return bound


def gcloud_environment(binding: AuthBinding, *, interactive: bool = False) -> dict[str, str]:
    # Never inherit GA4 ADC, token overrides, impersonation, alternate universes, or CLI HTTP logging.
    environment = {key: value for key, value in os.environ.items()
                   if not key.upper().startswith("CLOUDSDK_") and key.upper() not in {
                       "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT", "GCLOUD_PROJECT", "PSMODULEPATH"}}
    environment.update({"CLOUDSDK_CONFIG": str(binding.gcloud_directory),
                        "CLOUDSDK_ACTIVE_CONFIG_NAME": binding.configuration,
                        "CLOUDSDK_CORE_ACCOUNT": binding.account,
                        "CLOUDSDK_CORE_PROJECT": binding.project_id,
                        "CLOUDSDK_CORE_DISABLE_USAGE_REPORTING": "true",
                        "CLOUDSDK_COMPONENT_MANAGER_DISABLE_UPDATE_CHECK": "true",
                        "CLOUDSDK_CORE_DISABLE_PROMPTS": "false" if interactive else "true",
                        "CLOUDSDK_CORE_LOG_HTTP": "false", "PYTHONIOENCODING": "utf-8"})
    return environment


def resolve_gcloud() -> str:
    override = os.environ.get("TRACKPROMPT_GCLOUD_EXECUTABLE")
    candidates = [override] if override else [shutil.which(name) for name in ("gcloud", "gcloud.cmd", "gcloud.ps1")]
    if os.name == "nt" and not override:
        for variable in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
            base = os.environ.get(variable)
            if base:
                candidates.append(str(Path(base) / "Google/Cloud SDK/google-cloud-sdk/bin/gcloud.cmd"))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate).resolve())
    raise GcpAuthError("gcloud_missing", "Google Cloud CLI is not available.",
                       "Choose an official installation at https://cloud.google.com/sdk/docs/install or pass -GcloudExecutable.")


def _powershell_command(script: str) -> list[str]:
    executable = shutil.which("powershell.exe") or shutil.which("pwsh")
    if not executable:
        raise GcpAuthError("powershell_missing", "PowerShell 5.1 or newer is required for this Windows launcher.",
                           "Run from Windows PowerShell 5.1 or PowerShell 7.")
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded]


def _ps_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def executable_command(executable: str, arguments: list[str]) -> list[str]:
    if os.name == "nt" and Path(executable).suffix.lower() in {".cmd", ".bat"}:
        if any(re.search(r'[&|<>^%!"\r\n]', value) for value in [executable, *arguments]):
            raise GcpAuthError("gcloud_launcher_argument_invalid", "A Windows batch launcher argument contains unsupported shell characters.",
                               "Use the SDK's gcloud.ps1 launcher or a path without shell metacharacters.")
    if os.name == "nt" and Path(executable).suffix.lower() in {".cmd", ".bat", ".ps1"}:
        # PowerShell invokes native .cmd launchers without losing Unicode or space-containing paths.
        # Values are literal strings; no operator input is evaluated as PowerShell source.
        script = ("$ErrorActionPreference='Stop'; Set-StrictMode -Version 2.0; "
                  "$global:LASTEXITCODE=0; [Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false); "
                  "$OutputEncoding=[Console]::OutputEncoding; try { & " + _ps_literal(executable) + " @(" +
                  ",".join(_ps_literal(argument) for argument in arguments) +
                  "); exit $LASTEXITCODE } catch { exit 1 }")
        return _powershell_command(script)
    return [executable, *arguments]


def classify_cli_error(output: str) -> GcpAuthError:
    lowered = output.lower()
    if any(text in lowered for text in ("invalid_grant", "reauth", "login", "revoked", "expired", "unauthenticated", "401")):
        return GcpAuthError("auth_required", "The bound Google identity needs reauthentication.",
                           "Run tools/reauth-gcp.ps1 -Mode Reauth, then resume the existing run.")
    if "billing" in lowered and any(text in lowered for text in ("disabled", "not enabled", "inactive")):
        return GcpAuthError("billing_disabled", "Billing is not active for the bound project.",
                           "Ask the project billing administrator to inspect the selected project's billing status.")
    if any(text in lowered for text in ("disabled", "has not been used", "service_disabled")):
        return GcpAuthError("api_disabled", "A required Google API is not confirmed enabled.",
                           "Ask the project administrator to enable the required API in the bound video project.")
    if "quota" in lowered or "resource_exhausted" in lowered or "429" in lowered:
        return GcpAuthError("provider_quota_exhausted", "The bound project has insufficient quota.",
                           "Inspect the project's Vertex AI quota; do not resubmit uncertain work.")
    if any(text in lowered for text in ("permission", "forbidden", "403", "denied")):
        return GcpAuthError("permission_denied", "The bound identity lacks a required permission.",
                           "Ask the project administrator for the specific missing permission; repeated login will not repair IAM.")
    if any(text in lowered for text in ("connection", "network", "timed out", "unreachable", "resolve")):
        return GcpAuthError("network_unavailable", "Google Cloud could not be reached.",
                           "Restore network access and repeat the read-only check.", retryable=True)
    return GcpAuthError("gcloud_failed", "The Google Cloud CLI command failed.",
                       "Check the bound CLI installation and rerun Local, then Cloud mode.")


def _run_gcloud(binding: AuthBinding, arguments: list[str], *, timeout: int = 60, interactive: bool = False) -> str:
    _validate_configuration(binding)
    argv = executable_command(resolve_gcloud(), [*arguments, f"--configuration={binding.configuration}",
                                               f"--account={binding.account}", f"--project={binding.project_id}",
                                               "--verbosity=error"])
    try:
        result = run_process_bounded(argv, timeout_seconds=timeout, environment=gcloud_environment(binding, interactive=interactive),
                                     stdout_limit=_LIMIT, stderr_limit=_LIMIT, hide_window=not interactive)
    except ProcessTimedOut as exc:
        error = GcpAuthError("gcloud_timeout", "Google Cloud CLI exceeded the command timeout.",
                             "Check connectivity; retry a check or explicitly request Reauth again.", retryable=not interactive)
        error.network_contacted = arguments[:2] != ["config", "configurations"]
        raise error from exc
    except OSError as exc:
        raise GcpAuthError("gcloud_launch_failed", "Google Cloud CLI could not be started.",
                           "Check the executable override and its local execution permissions.") from exc
    if result.stdout_exceeded or result.stderr_exceeded:
        raise GcpAuthError("gcloud_output_limit", "Google Cloud CLI exceeded its bounded output limit.",
                           "Inspect the local CLI installation; no command output has been exposed.")
    if result.returncode != 0:
        error = classify_cli_error(result.stderr.decode("utf-8-sig", errors="replace"))
        error.network_contacted = arguments[:2] != ["config", "configurations"]
        raise error
    return result.stdout.decode("utf-8-sig", errors="replace").strip()


def _validate_configuration(binding: AuthBinding) -> None:
    path = binding.gcloud_directory / "configurations" / ("config_" + binding.configuration)
    if not path.exists():
        return
    try:
        if path.stat().st_size > _LIMIT:
            raise _invalid()
        config = configparser.RawConfigParser()
        config.read_string(path.read_text(encoding="utf-8-sig"))
        forbidden = ("credential_file_override", "access_token_file", "impersonate_service_account", "login_config_file")
        if (any(config.get("auth", key, fallback="").strip() for key in forbidden)
                or config.has_section("api_endpoint_overrides")
                or config.get("core", "log_http", fallback="false").strip().lower() == "true"):
            raise GcpAuthError("auth_identity_mismatch", "The private CLI configuration overrides the bound user identity or endpoint.",
                               "Restore the TrackPrompt user-login configuration; workload identity requires a separately reconciled worker binding.")
    except (OSError, ValueError, configparser.Error) as exc:
        raise _invalid() from exc


def _windows_acl(path: Path, *, create: bool = False) -> None:
    script = r"""
$ErrorActionPreference='Stop'; Set-StrictMode -Version 2.0
$target=TARGET; $create=CREATE
$sid=[System.Security.Principal.WindowsIdentity]::GetCurrent().User
try {
  if (-not (Test-Path -LiteralPath $target)) {
    if (-not $create) { exit 3 }
    $null=New-Item -ItemType Directory -Path $target -Force
    $acl=Get-Acl -LiteralPath $target
    $rule=New-Object System.Security.AccessControl.FileSystemAccessRule($sid,'FullControl','ContainerInherit,ObjectInherit','None','Allow')
    $acl.SetAccessRule($rule); Set-Acl -LiteralPath $target -AclObject $acl
    # Prove replacement access before removing inherited access.
    $probe=Join-Path $target ([Guid]::NewGuid().ToString()+'.acl-probe')
    [IO.File]::WriteAllText($probe,''); Remove-Item -LiteralPath $probe
    $acl=Get-Acl -LiteralPath $target; $acl.SetAccessRuleProtection($true,$false)
    Set-Acl -LiteralPath $target -AclObject $acl
    [IO.File]::WriteAllText($probe,''); Remove-Item -LiteralPath $probe
  }
  $items=@(Get-Item -LiteralPath $target -Force)+@(Get-ChildItem -LiteralPath $target -Force -Recurse)
  if ($items.Count -gt 4096) { exit 9 }
  foreach ($item in $items) {
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { exit 4 }
    $acl=Get-Acl -LiteralPath $item.FullName
    $allowed=@($sid.Value,'S-1-5-18','S-1-5-32-544'); $own=$false
    foreach ($rule in $acl.Access) {
      $id=$rule.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value
      if ($rule.AccessControlType -eq 'Allow' -and $id -notin $allowed) { exit 5 }
      if ($id -eq $sid.Value -and $rule.AccessControlType -eq 'Deny') { exit 6 }
      if ($id -eq $sid.Value -and $rule.AccessControlType -eq 'Allow' -and ($rule.FileSystemRights -band [Security.AccessControl.FileSystemRights]::FullControl) -eq [Security.AccessControl.FileSystemRights]::FullControl) { $own=$true }
    }
    if (-not $own) { exit 7 }
  }
} catch { exit 8 }
exit 0
""".replace("CREATE", "$true" if create else "$false").replace("TARGET", _ps_literal(str(path)))
    try:
        environment = {key: value for key, value in os.environ.items() if key.upper() != "PSMODULEPATH"}
        result = run_process_bounded(_powershell_command(script), timeout_seconds=20, stdout_limit=1024, stderr_limit=1024, hide_window=True, environment=environment)
    except (ProcessTimedOut, OSError) as exc:
        raise GcpAuthError("auth_acl_denied", "Private credential directory permissions could not be verified.",
                           "Choose a new private TrackPrompt auth directory owned by the current Windows SID.") from exc
    if result.returncode != 0:
        raise GcpAuthError("auth_acl_denied", "Private credential directory permissions failed the SID check.",
                           "Choose a new private TrackPrompt auth directory; existing permissions were not weakened.")


def validate_private_directory(path: Path) -> None:
    if os.name == "nt":
        _windows_acl(path)
    elif not path.is_dir() or path.is_symlink() or path.stat().st_uid != getattr(os, "getuid", lambda: -1)() or path.stat().st_mode & 0o077:
        raise GcpAuthError("auth_acl_denied", "Credential directory must be private and owned by the current user.",
                           "Use an owner-only directory (mode 0700) for TrackPrompt authentication.")


def _create_private_directory(path: Path) -> None:
    if os.name == "nt":
        _windows_acl(path, create=True)
    else:
        path.mkdir(parents=True, mode=0o700, exist_ok=True)
        validate_private_directory(path)


def access_token(*, project_id: str | None = None, binding: AuthBinding | None = None) -> str:
    bound = binding or load_binding(project_id=project_id)
    if project_id is not None and bound.project_id != project_id:
        raise _invalid()
    validate_private_directory(bound.config_path.parent)
    validate_private_directory(bound.gcloud_directory)
    token = _run_gcloud(bound, ["auth", "print-access-token"], timeout=30)
    if not _TOKEN.fullmatch(token):
        raise GcpAuthError("auth_token_invalid", "Google Cloud CLI did not return one valid access token.",
                           "Run tools/reauth-gcp.ps1 -Mode Reauth and repeat Cloud mode.")
    return token


def run_bound_gcloud(arguments: list[str], *, project_id: str | None = None, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    bound = load_binding(project_id=project_id)
    validate_private_directory(bound.config_path.parent)
    validate_private_directory(bound.gcloud_directory)
    output = _run_gcloud(bound, arguments, timeout=timeout)
    return subprocess.CompletedProcess(arguments, 0, output, "")


def _json(output: str) -> Any:
    try:
        return json.loads(output)
    except ValueError as exc:
        raise GcpAuthError("gcloud_output_invalid", "Google Cloud CLI returned malformed structured output.",
                           "Check the CLI installation; interactive output must not enter machine-readable results.") from exc


def _permission_response(request: urllib.request.Request) -> list[str]:
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read(_LIMIT + 1)
        if len(raw) > _LIMIT:
            raise _invalid()
        value = _json(raw.decode("utf-8"))
        permissions = value.get("permissions", []) if isinstance(value, dict) else None
        if not isinstance(permissions, list) or any(not isinstance(item, str) for item in permissions):
            raise _invalid()
        return permissions
    except urllib.error.HTTPError as exc:
        raise classify_cli_error(str(exc.code)) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise classify_cli_error("network unavailable") from exc


def _storage_permissions(binding: AuthBinding, token: str) -> list[str]:
    query = urllib.parse.urlencode([("permissions", value) for value in ("storage.objects.create", "storage.objects.get")])
    url = "https://storage.googleapis.com/storage/v1/b/" + urllib.parse.quote(binding.bucket, safe="") + "/iam/testPermissions?" + query
    return _permission_response(urllib.request.Request(url, headers={"Authorization": "Bearer " + token}))


def _project_permissions(binding: AuthBinding, token: str) -> list[str]:
    url = "https://cloudresourcemanager.googleapis.com/v1/projects/" + binding.project_id + ":testIamPermissions"
    body = json.dumps({"permissions": ["aiplatform.endpoints.predict", "serviceusage.services.use"]}).encode("utf-8")
    return _permission_response(urllib.request.Request(url, data=body, method="POST",
                                headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"}))


def check_access(*, mode: str = "local", project_id: str | None = None, bucket: str | None = None,
                 region: str | None = None, binding: AuthBinding | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"ok": False, "networkContacted": False, "tokenAvailable": None, "checks": []}
    checks = result["checks"]

    def check(identifier: str, ok: bool, detail: str) -> None:
        checks.append({"id": identifier, "ok": ok, "detail": detail, "safeDetail": detail})

    try:
        if mode not in {"local", "cloud"}:
            raise _invalid()
        bound = binding or load_binding(project_id=project_id)
        if ((project_id is not None and bound.project_id != project_id)
                or (bucket is not None and bound.bucket != bucket.removeprefix("gs://").strip("/"))
                or (region is not None and bound.region != region)):
            raise GcpAuthError("auth_identity_mismatch", "The requested run does not match the private binding; reconcile before resuming.",
                               "Restore the intended project, bucket and region binding.")
        resolve_gcloud()
        check("gcloud-installed", True, "Google Cloud CLI is available.")
        validate_private_directory(bound.config_path.parent)
        validate_private_directory(bound.gcloud_directory)
        check("private-credentials", True, "Credential directory access is private.")
        check("region-model-configuration", True, "Configured region and model are supported by the local provider contract; live generation eligibility is unverified.")
        if mode == "cloud":
            # CLI token refresh may contact Google. No login or paid generation is initiated here.
            result["networkContacted"] = True
            accounts = _json(_run_gcloud(bound, ["auth", "list", "--format=json"], timeout=30))
            if (not isinstance(accounts, list) or not any(isinstance(account, dict) and account.get("account") == bound.account for account in accounts)):
                raise GcpAuthError("auth_identity_mismatch", "The bound Google identity is not credentialed in TrackPrompt.",
                                   "Run tools/reauth-gcp.ps1 -Mode Reauth for the bound account.")
            check("active-account", True, "The intended account exists in the isolated credential store.")
            token = access_token(binding=bound)
            result["tokenAvailable"] = True
            check("access-token", True, "A token for the bound account is available.")
            project = _json(_run_gcloud(bound, ["projects", "describe", bound.project_id, "--format=json"]))
            check("project-access", isinstance(project, dict) and project.get("projectId") == bound.project_id,
                  "Project access was checked against the exact binding.")
            services = _json(_run_gcloud(bound, ["services", "list", "--enabled", "--filter=config.name:aiplatform.googleapis.com", "--format=json"]))
            enabled = isinstance(services, list) and any(isinstance(item, dict) and isinstance(item.get("config"), dict) and item["config"].get("name") == "aiplatform.googleapis.com" for item in services)
            check("aiplatform-api-enabled", enabled, "Vertex AI API enablement was checked.")
            project_permissions = _project_permissions(bound, token)
            check("vertex-project-permissions", {"aiplatform.endpoints.predict", "serviceusage.services.use"}.issubset(project_permissions),
                  "Read-only IAM test for Vertex prediction and service usage; no generation request was submitted.")
            metadata = _json(_run_gcloud(bound, ["storage", "buckets", "describe", "gs://" + bound.bucket, "--format=json"]))
            check("gcs-bucket-access", isinstance(metadata, dict) and metadata.get("name") == bound.bucket,
                  "Bucket metadata access only; this does not establish write access.")
            permissions = _storage_permissions(bound, token)
            check("gcs-object-permissions", {"storage.objects.create", "storage.objects.get"}.issubset(permissions),
                  "Read-only IAM permission test for storage.objects.create and storage.objects.get; no object was uploaded.")
        result["ok"] = all(check["ok"] for check in checks)
        if not result["ok"]:
            result["error"] = GcpAuthError("cloud_prerequisite_missing", "One or more cloud prerequisites were not verified.",
                                            "Review the failed check and ask the project administrator for that specific prerequisite.").to_dict()
    except GcpAuthError as exc:
        check("authentication-action", False, str(exc))
        checks[-1].update({"code": exc.code, "remedy": exc.remedy, "retryable": exc.retryable})
        result["error"] = exc.to_dict()
    return result


def reauthenticate(binding: AuthBinding) -> None:
    if binding.config_path.exists():
        previous = load_binding(config_path=binding.config_path)
        if previous.identity_digest != binding.identity_digest or previous.to_dict() != binding.to_dict():
            raise GcpAuthError("auth_identity_mismatch", "Changing a stored binding requires explicit run reconciliation.",
                               "Use a separate TrackPrompt binding path for a new identity/project; reconcile existing runs before using it.")
    resolve_gcloud()
    _create_private_directory(binding.config_path.parent)
    _create_private_directory(binding.gcloud_directory)
    config_file = binding.gcloud_directory / "configurations" / ("config_" + binding.configuration)
    if not config_file.exists():
        _run_gcloud(binding, ["config", "configurations", "create", binding.configuration, "--no-activate", "--quiet"])
    _run_gcloud(binding, ["auth", "login", binding.account, "--no-activate", "--brief", "--force"], timeout=300, interactive=True)
    access_token(binding=binding)
    temporary = binding.config_path.with_suffix(".json.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(binding.to_dict(), stream, ensure_ascii=True, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(binding.config_path)
    except OSError as exc:
        raise GcpAuthError("auth_binding_write_failed", "Login completed but the binding could not be persisted.",
                           "Repair the private binding directory before resuming; do not start a new render.") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("local", "cloud", "reauth"), default="local")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--account")
    parser.add_argument("--project")
    parser.add_argument("--bucket")
    parser.add_argument("--region")
    parser.add_argument("--model")
    parser.add_argument("--noninteractive", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.mode == "reauth" and args.noninteractive:
            raise GcpAuthError("interactive_login_required", "Reauthentication requires an explicit interactive invocation.",
                               "Run tools/reauth-gcp.ps1 -Mode Reauth from an operator terminal without -NonInteractive.")
        path = (args.config or default_config_path()).resolve()
        previous = load_binding(config_path=path) if path.exists() else None
        if args.mode == "reauth":
            values = {"account": args.account or (previous.account if previous else None),
                      "project_id": args.project or (previous.project_id if previous else None),
                      "bucket": args.bucket or (previous.bucket if previous else None)}
            missing = [key for key, value in values.items() if not value]
            if missing:
                if not sys.stdin.isatty():
                    raise GcpAuthError("auth_choices_required", "The intended account, project and bucket must be explicit.",
                                       "Pass -Account, -ProjectId and -Bucket; no global account or Analytics project is selected automatically.")
                print("TrackPrompt " + ", ".join(missing) + " (space-separated, in that order): ", end="", file=sys.stderr, flush=True)
                try:
                    choices = input().strip().split()
                except EOFError as exc:
                    raise _invalid() from exc
                if len(choices) != len(missing):
                    raise _invalid()
                values.update(zip(missing, choices, strict=True))
            bound = AuthBinding(str(values["account"]), str(values["project_id"]), str(values["bucket"]).removeprefix("gs://").strip("/"),
                                args.region or (previous.region if previous else "us-central1"),
                                args.model or (previous.model_id if previous else "veo-3.1-fast-generate-001"), path)
            reauthenticate(bound)
            result = check_access(mode="cloud", binding=bound)
            result["reauthenticated"] = True
            result["resumeAction"] = "Resume the existing run to poll/download recorded operations. No render was started."
        else:
            bound = previous or load_binding(config_path=path)
            if args.account and args.account != bound.account:
                raise GcpAuthError("auth_identity_mismatch", "The requested account differs from the binding.", "Reconcile the intended identity before resuming.")
            result = check_access(mode=args.mode, binding=bound, project_id=args.project, bucket=args.bucket, region=args.region)
    except GcpAuthError as exc:
        result = {"ok": False, "networkContacted": exc.network_contacted, "checks": [], "error": exc.to_dict()}
        if args.mode == "local" and exc.code == "auth_configuration_missing":
            result["discovery"] = discover_binding_hints()
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
