# TrackPrompt GCP authentication

The supported Windows controller obtains short-lived tokens with `gcloud auth print-access-token`. It uses **CLI credentials**, not ADC. `tools/reauth-gcp.ps1` and the provider share `backend/app/video_generation/gcp_auth.py`; login does not restart a worker or submit video work.

## Commands

Run from the repository root in Windows PowerShell **5.1** or PowerShell **7+**:

```powershell
# No Google calls, token refresh, login, install, or configuration changes.
.\tools\reauth-gcp.ps1 -Mode Local -NonInteractive

# Non-generative cloud checks; may refresh the bound account's CLI token.
.\tools\reauth-gcp.ps1 -Mode Cloud -NonInteractive

# First binding: substitute the selected video identity/project/private bucket.
.\tools\reauth-gcp.ps1 -Mode Reauth -Account 'operator@example.com' -ProjectId 'video-project-id' -Bucket 'private-video-bucket'

# Later: reauthenticate the existing binding through Google's browser flow.
.\tools\reauth-gcp.ps1 -Mode Reauth
```

Reauth opens Google's browser login only when explicitly requested, allows five minutes, verifies token acquisition internally, saves the private binding, and runs cloud checks. It does not print OAuth URLs/codes, CLI output, tokens, or credential JSON. If browser authentication cannot complete automatically, the bounded invocation fails; it never falls back to printing an authorization code. `Reauth -NonInteractive` fails before any login/configuration action. Missing first-use values are requested together once in an operator terminal; a noninteractive caller must supply them.

Exit code `0` means the selected checks passed; `2` means action is required. Stdout is one safe JSON object, including `checks`, a safe error `code`, `retryable`, and `remedy`. A Local pass is **not** a valid-token or cloud-access claim. Cloud mode tests account presence, token acquisition, exact project access, Vertex API enablement, project permissions (`aiplatform.endpoints.predict`, `serviceusage.services.use`), bucket metadata, and bucket object permissions (`storage.objects.create`, `storage.objects.get`). Permission tests do not upload an object. They do not prove that Vertex's separate service identity can write outputs, sufficient billing/quota, regional model allowlisting, or a successful video request. Region/model configuration is checked against the supported local contract and labeled accordingly.

## One private binding

The default binding is `<TRACKPROMPT_DATA_DIR>/gcp-auth/auth.json`; the default data directory is the repository's `.trackprompt-data`. Its private sibling `gcloud/` directory contains the isolated `trackprompt` CLI configuration. `TRACKPROMPT_GCP_AUTH_CONFIG` selects an alternate binding for both controller and script. `-ConfigPath` selects it for one script invocation only; set the environment variable identically for the controller when choosing an alternate path.

The file records the intended account, project ID, bucket, region, and model. Existing controller requests must match that binding. The identity fingerprint includes the account, project, configuration name, and credential directory; changing it requires reconciliation before resuming a retained run. The tool refuses to rewrite an existing binding to another account/project/bucket/model. A new identity needs a separate private binding and explicit run reconciliation. No first account/project is guessed from global gcloud, and the Analytics project is not a fallback.

When Local mode finds no binding, it also reports read-only discovery hints from previous `operator-target.json` video records and account names in ordinary gcloud configuration INI files. These are candidates, not verified credentials. No credential database is read, no global CLI command runs, and no hint is adopted automatically. Supply the intended account/project/bucket explicitly to Reauth after selecting the matching prior video target.

Only the child CLI process receives temporary `CLOUDSDK_*` settings. Global defaults, unrelated gcloud directories, GA4 ADC, Google refresh-token bundles, and Analytics OAuth clients are untouched. Child environments remove inherited ADC/token/impersonation overrides and inherited PowerShell module paths; the script restores temporary Python/CLI environment settings and leaves the current directory unchanged. Credentials are never copied to a worker. A genuinely remote worker needs a separately selected attached/federated/impersonated identity; this workstation entry point deliberately does not configure that environment or create service-account keys.

New credential directories are protected by current-user SID access on Windows. The script proves replacement access before dropping inherited access and verifies SID permissions, including existing child files. Existing unapproved ACLs fail closed without permission weakening. POSIX module use requires owner-only directories. Do not place the binding or credential directories in Git, WNP, artifact exports, or public diagnostics.

## Runtime and executable selection

`-PythonExecutable` or `TRACKPROMPT_PYTHON_EXECUTABLE` selects a stable Python 3.11–3.13 interpreter. Windows defaults to the installed `%LOCALAPPDATA%\Programs\Python\Python312\python.exe`; it does not silently use a disposable Codex runtime. The module itself uses the standard library and the repository's bounded subprocess helper. The controller still needs its declared backend dependencies.

`-GcloudExecutable` or `TRACKPROMPT_GCLOUD_EXECUTABLE` selects the Google CLI launcher. Discovery supports `gcloud`, `.cmd`, `.ps1`, and standard Windows SDK installation locations. Spaces and Hebrew paths are supported. Batch launchers reject shell metacharacters in arguments; select the SDK's `.ps1` launcher for such paths. No command installs or updates the SDK. If missing, choose an installation from [Google's official instructions](https://cloud.google.com/sdk/docs/install).

Every native exit code is checked. Output is drained into fixed 64 KiB buffers, commands have deadlines, and child process trees are stopped on timeout/output overflow. Background checks hide process windows. Errors expose categories and remedies rather than raw CLI responses. Access tokens remain only in process memory and request authorization headers.

## Recovery

After successful reauthentication, resume the **existing run**. Recorded accepted operations are polled/downloaded; authentication repair never creates a replacement paid request. A 403/permission error needs a specific IAM correction, not repeated login. Disabled APIs, insufficient quota, network faults, malformed CLI output, binding mismatches, and ACL errors remain distinct action states. The tool does not enable APIs, create buckets, change IAM, relax the no-service-account-keys policy, raise quota, or alter billing.

Google references verified 2026-09-22: [CLI login](https://docs.cloud.google.com/sdk/gcloud/reference/auth/login), [isolated configurations](https://docs.cloud.google.com/sdk/docs/configurations), [ADC separation](https://docs.cloud.google.com/docs/authentication/application-default-credentials), [bucket permission tests](https://docs.cloud.google.com/storage/docs/json_api/v1/buckets/testIamPermissions), and [project permission tests](https://docs.cloud.google.com/resource-manager/reference/rest/v1/projects/testIamPermissions).

## Verification

`backend/tests/test_gcp_auth.py` covers binding validation/BOM, identity changes and overrides, GA4/ADC isolation, local-only behavior, multiple-account selection, safe errors, noisy token rejection, and permission semantics. `backend/tests/test_gcp_auth_windows.py` uses fake native CLI processes for PowerShell 5.1/7 parsing and execution, `.cmd`/`.ps1`, spaces/Hebrew, nonzero exits, noise/malformed JSON, deadlines/output limits, SID ACL rejection, repeated checks/environment restoration, and reauthentication without submission. These tests make no Google calls and do not establish live provider acceptance.
