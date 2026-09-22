"""Small, explicitly scoped Cloud Build/Batch setup using the bound user token."""
from __future__ import annotations

import gzip
import io
import json
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import ACCOUNT, BUCKET, BUILD_ACCOUNT, IMAGE, PROJECT, REGION, REPOSITORY, WORKER_ACCOUNT

SERVICES = ('batch.googleapis.com', 'compute.googleapis.com', 'cloudbuild.googleapis.com',
            'artifactregistry.googleapis.com', 'aiplatform.googleapis.com', 'iam.googleapis.com',
            'logging.googleapis.com', 'storage.googleapis.com')


class CloudSetupError(RuntimeError):
    """Safe operator-facing external boundary; raw API bodies are not printed."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class ApiError(CloudSetupError):
    def __init__(self, code: int, service: str) -> None:
        self.code = code
        super().__init__(f'{service} returned HTTP {code}. Check the bound project permissions, billing or quota; cloud state is preserved.',
                         retryable=code in {408, 429, 500, 502, 503, 504})


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class GoogleApi:
    def __init__(self, token: Callable[[], str]) -> None:
        self.token = token

    def request(self, method: str, url: str, value: dict[str, Any] | None = None,
                *, missing_ok: bool = False, retry_safe: bool = False) -> dict[str, Any] | None:
        service = urllib.parse.urlparse(url).hostname or ''
        if not service.endswith('.googleapis.com') or not url.startswith('https://'):
            raise ValueError('Google management endpoint required')
        for attempt in range(6):
            request = urllib.request.Request(url, method=method,
                data=json.dumps(value).encode() if value is not None else None,
                headers={'Authorization': 'Bearer ' + self.token(), 'Content-Type': 'application/json'})
            try:
                with urllib.request.build_opener(_NoRedirect).open(request, timeout=90) as response:
                    raw = response.read(4 * 1024 * 1024 + 1)
                if len(raw) > 4 * 1024 * 1024:
                    raise CloudSetupError('Google management response exceeded its bounded size.')
                result = json.loads(raw) if raw else {}
                if not isinstance(result, dict):
                    raise CloudSetupError('Google management response is malformed.')
                return result
            except urllib.error.HTTPError as exc:
                if exc.code == 404 and missing_ok:
                    return None
                if exc.code == 401 and attempt < 5:
                    invalidate = getattr(getattr(self.token, '__self__', self.token), 'invalidate', None)
                    if callable(invalidate):
                        invalidate()
                        continue
                if (method == 'GET' or retry_safe) and exc.code in {408, 429, 500, 502, 503, 504} and attempt < 5:
                    time.sleep(min(2 ** attempt, 16))
                    continue
                raise ApiError(exc.code, service) from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if (method == 'GET' or retry_safe) and attempt < 5:
                    time.sleep(min(2 ** attempt, 16))
                    continue
                raise CloudSetupError(f'{service} could not be reached. Resume the same command; accepted jobs will be reconciled.',
                                      retryable=method == 'GET' or retry_safe) from exc
        raise CloudSetupError('Google request retry limit reached.')

    def wait_operation(self, result: dict[str, Any], service: str) -> dict[str, Any]:
        for _ in range(120):
            if result.get('done'):
                if result.get('error'):
                    raise CloudSetupError(f'{service} setup operation failed; no production was started.')
                return result
            name = result.get('name')
            if not name:
                return result
            time.sleep(3)
            result = self.request('GET', f'https://{service}/v1/{name}') or {}
        raise CloudSetupError(f'{service} setup is still pending. Resume the same command.')


def _ensure_binding(api: GoogleApi, get_url: str, set_url: str, member: str, role: str,
                    *, condition: dict[str, str] | None = None, storage: bool = False,
                    get_method: str = 'GET') -> None:
    for _ in range(4):
        policy = api.request(get_method, get_url, retry_safe=get_method == 'POST') or {}
        bindings = policy.setdefault('bindings', [])
        matched = next((item for item in bindings if item.get('role') == role and item.get('condition') == condition), None)
        if matched and member in matched.get('members', []):
            return
        if matched:
            matched.setdefault('members', []).append(member)
        else:
            item: dict[str, Any] = {'role': role, 'members': [member]}
            if condition:
                item['condition'] = condition
            bindings.append(item)
        if condition:
            policy['version'] = 3
        try:
            api.request('PUT' if storage else 'POST', set_url, policy if storage else {'policy': policy})
            return
        except ApiError as exc:
            if exc.code not in {409, 412}:
                raise
    raise CloudSetupError('Concurrent IAM policy changes prevented scoped setup; retry the same command.')


def setup(api: GoogleApi, *, check_only: bool = False) -> dict[str, Any]:
    project = api.request('GET', f'https://cloudresourcemanager.googleapis.com/v1/projects/{PROJECT}') or {}
    if project.get('projectId') != PROJECT or project.get('lifecycleState') != 'ACTIVE':
        raise CloudSetupError('The exact Hayale GCP project is not active.')
    number = str(project['projectNumber'])
    absent = []
    for service in SERVICES:
        url = f'https://serviceusage.googleapis.com/v1/projects/{number}/services/{service}'
        if (api.request('GET', url) or {}).get('state') != 'ENABLED':
            absent.append(service)
            if not check_only:
                api.wait_operation(api.request('POST', url + ':enable', {}) or {}, 'serviceusage.googleapis.com')
    for account in (WORKER_ACCOUNT, BUILD_ACCOUNT):
        base = f'https://iam.googleapis.com/v1/projects/{PROJECT}/serviceAccounts'
        found = api.request('GET', base + '/' + account, missing_ok=True)
        if found is None:
            absent.append(account.split('@')[0])
            if not check_only:
                api.request('POST', base, {'accountId': account.split('@')[0],
                                           'serviceAccount': {'displayName': account.split('@')[0]}})
        elif found.get('disabled'):
            raise CloudSetupError('The dedicated Hayale runtime identity is disabled.')
    repo = f'projects/{PROJECT}/locations/{REGION}/repositories/{REPOSITORY}'
    repo_url = 'https://artifactregistry.googleapis.com/v1/' + repo
    found_repo = api.request('GET', repo_url, missing_ok=True)
    if found_repo is None:
        absent.append(REPOSITORY)
        if not check_only:
            parent = f'https://artifactregistry.googleapis.com/v1/projects/{PROJECT}/locations/{REGION}/repositories'
            api.wait_operation(api.request('POST', parent + '?repositoryId=' + REPOSITORY,
                                           {'format': 'DOCKER', 'description': 'Bounded Hayale cloud media worker'}) or {},
                               'artifactregistry.googleapis.com')
    elif found_repo.get('format') != 'DOCKER':
        raise CloudSetupError('The dedicated Hayale image repository has an incompatible format.')
    if check_only:
        return {'projectVerified': True, 'missingSetup': absent, 'changedCloud': False}
    project_policy = f'https://cloudresourcemanager.googleapis.com/v1/projects/{PROJECT}'
    # v1 projects getIamPolicy uses POST, whereas the common helper GETs its resource.
    for account, roles in ((WORKER_ACCOUNT, ('roles/aiplatform.user', 'roles/batch.agentReporter', 'roles/logging.logWriter')),
                           (BUILD_ACCOUNT, ('roles/logging.logWriter',))):
        for role in roles:
            _project_binding(api, project_policy, 'serviceAccount:' + account, role)
        sa = f'https://iam.googleapis.com/v1/projects/{PROJECT}/serviceAccounts/{account}'
        _ensure_binding(api, sa + ':getIamPolicy?options.requestedPolicyVersion=3', sa + ':setIamPolicy',
                        'user:' + ACCOUNT, 'roles/iam.serviceAccountUser', get_method='POST')
    for account, role in ((WORKER_ACCOUNT, 'roles/artifactregistry.reader'), (BUILD_ACCOUNT, 'roles/artifactregistry.writer')):
        _ensure_binding(api, repo_url + ':getIamPolicy?options.requestedPolicyVersion=3', repo_url + ':setIamPolicy',
                        'serviceAccount:' + account, role)
    bucket_iam = f'https://storage.googleapis.com/storage/v1/b/{BUCKET}/iam'
    for account, prefix, role in ((WORKER_ACCOUNT, 'hayale-final/', 'roles/storage.objectUser'),
                                   (BUILD_ACCOUNT, 'hayale-build/', 'roles/storage.objectViewer')):
        condition = {'title': 'trackprompt-hayale-' + account.split('@')[0],
                     'expression': f"resource.name.startsWith('projects/_/buckets/{BUCKET}/objects/{prefix}') || resource.name == 'projects/_/buckets/{BUCKET}'"}
        _ensure_binding(api, bucket_iam + '?optionsRequestedPolicyVersion=3', bucket_iam,
                        'serviceAccount:' + account, role, condition=condition, storage=True)
    _ensure_binding(api, bucket_iam + '?optionsRequestedPolicyVersion=3', bucket_iam,
                    'serviceAccount:' + WORKER_ACCOUNT, 'roles/storage.legacyBucketReader',
                    condition={'title': 'hayale-bucket-metadata',
                               'expression': f"resource.name == 'projects/_/buckets/{BUCKET}'"}, storage=True)
    return {'projectVerified': True, 'setupReady': True, 'createdOrEnabled': absent}


def _project_binding(api: GoogleApi, base: str, member: str, role: str) -> None:
    for _ in range(4):
        policy = api.request('POST', base + ':getIamPolicy', {'options': {'requestedPolicyVersion': 3}}, retry_safe=True) or {}
        bindings = policy.setdefault('bindings', [])
        match = next((b for b in bindings if b.get('role') == role and not b.get('condition')), None)
        if match and member in match.get('members', []):
            return
        if match:
            match.setdefault('members', []).append(member)
        else:
            bindings.append({'role': role, 'members': [member]})
        try:
            api.request('POST', base + ':setIamPolicy', {'policy': policy})
            return
        except ApiError as exc:
            if exc.code not in {409, 412}:
                raise
    raise CloudSetupError('Concurrent project IAM changes prevented scoped setup.')


def worker_archive(repository: Path, destination: Path) -> str:
    """Allowlisted source only; never upload the checkout or its runtime directories."""
    paths = ['cloud/hayale/Dockerfile', 'cloud/hayale/requirements.txt']
    paths += ['backend/app/video_generation/hayale_gcp/' + name for name in
              ('__init__.py', 'worker.py', 'state.py', 'cloud.py', 'media.py')]
    paths += ['backend/app/video_generation/jsonio.py']
    snapshot = {relative: (repository / relative).read_bytes() for relative in paths}
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('wb') as raw_archive, gzip.GzipFile(fileobj=raw_archive, mode='wb', filename='', mtime=0) as compressed, tarfile.open(fileobj=compressed, mode='w') as archive:
        for relative in paths:
            raw = snapshot[relative]
            info = tarfile.TarInfo(relative)
            info.size, info.mtime, info.mode = len(raw), 0, 0o644
            archive.addfile(info, io.BytesIO(raw))
        for relative in ('backend/app/__init__.py', 'backend/app/video_generation/__init__.py'):
            info = tarfile.TarInfo(relative)
            info.size, info.mode = 0, 0o644
            archive.addfile(info, io.BytesIO())
    # gzip header timestamp is intentionally excluded from the source identity.
    import hashlib
    identity = hashlib.sha256()
    for relative in paths:
        identity.update(relative.encode() + b'\0' + snapshot[relative])
    return identity.hexdigest()


def build_spec(source: dict[str, Any], digest: str) -> dict[str, Any]:
    return {'source': {'storageSource': {'bucket': BUCKET, 'object': source['object'], 'generation': source['generation']}},
            'serviceAccount': f'projects/{PROJECT}/serviceAccounts/{BUILD_ACCOUNT}',
            'steps': [{'name': 'gcr.io/cloud-builders/docker', 'args': ['build', '-f', 'cloud/hayale/Dockerfile',
                       '-t', IMAGE + ':' + digest, '.']}], 'images': [IMAGE + ':' + digest],
            'tags': ['trackprompt-hayale', 'source-' + digest], 'timeout': '1800s',
            'options': {'logging': 'CLOUD_LOGGING_ONLY'}}


def batch_spec(run_id: str, image: str, *, smoke: bool = False) -> dict[str, Any]:
    commands = ['-m', 'app.video_generation.hayale_gcp.worker', '--bucket', BUCKET,
                '--runtime-service-account', WORKER_ACCOUNT]
    commands += ['--check-runtime', '--project', PROJECT] if smoke else ['--manifest', f'hayale-final/{run_id}/state/run-manifest.json']
    return {'taskGroups': [{'taskCount': 1, 'parallelism': 1, 'taskSpec': {
        'runnables': [{'container': {'imageUri': image, 'entrypoint': 'python', 'commands': commands}}],
        'computeResource': {'cpuMilli': 4000, 'memoryMib': 8192},
        'maxRunDuration': '600s' if smoke else '43200s', 'maxRetryCount': 0 if smoke else 2}}],
        'allocationPolicy': {'instances': [{'policy': {'machineType': 'e2-standard-4',
                                                        'bootDisk': {'sizeGb': 50, 'type': 'pd-balanced'},
                                                        'provisioningModel': 'STANDARD'}}],
                             'serviceAccount': {'email': WORKER_ACCOUNT,
                                                'scopes': ['https://www.googleapis.com/auth/cloud-platform']}},
        'logsPolicy': {'destination': 'CLOUD_LOGGING'}, 'labels': {'workflow': 'trackprompt-hayale', 'run': run_id}}


def immutable_image(api: GoogleApi, digest: str) -> str | None:
    package = urllib.parse.quote('worker', safe='')
    tag = f'https://artifactregistry.googleapis.com/v1/projects/{PROJECT}/locations/{REGION}/repositories/{REPOSITORY}/packages/{package}/tags/{digest}'
    result = api.request('GET', tag, missing_ok=True)
    if result is None:
        return None
    version = str(result.get('version', '')).rsplit('/', 1)[-1]
    version = urllib.parse.unquote(version)
    if not version.startswith('sha256:') or len(version) != 71:
        raise CloudSetupError('Cloud Build image digest is missing or invalid.')
    return IMAGE + '@' + version
