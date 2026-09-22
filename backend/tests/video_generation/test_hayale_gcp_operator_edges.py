from __future__ import annotations

import hashlib
import io
import json
import urllib.error
from pathlib import Path

import pytest

from app.video_generation.hayale_gcp import delivery, infrastructure
from app.video_generation.hayale_gcp.config import NAMES, PROJECT


class PolicyApi:
    def __init__(self):
        self.service_account_policy_reads = []

    def request(self, method, url, value=None, **kwargs):
        if ':getIamPolicy' in url:
            if 'iam.googleapis.com/' in url:
                self.service_account_policy_reads.append((method, url))
            return {'version': 3, 'etag': 'retained-etag', 'bindings': []}
        if ':setIamPolicy' in url or 'storage.googleapis.com/storage/v1/b/' in url:
            return {'version': 3, 'etag': 'retained-etag', 'bindings': []}
        if 'cloudresourcemanager.googleapis.com/' in url:
            return {'projectId': PROJECT, 'lifecycleState': 'ACTIVE', 'projectNumber': '12345'}
        if 'serviceusage.googleapis.com/' in url:
            return {'state': 'ENABLED'}
        if 'iam.googleapis.com/' in url:
            return {'name': url, 'disabled': False}
        if 'artifactregistry.googleapis.com/' in url:
            return {'format': 'DOCKER'}
        raise AssertionError((method, url))


def test_service_account_policy_reads_use_documented_post_contract():
    api = PolicyApi()
    infrastructure.setup(api)
    assert len(api.service_account_policy_reads) == 2
    assert all(method == 'POST' for method, _ in api.service_account_policy_reads)
    assert all('requestedPolicyVersion=3' in url for _, url in api.service_account_policy_reads)


@pytest.mark.parametrize(('method', 'retry_safe', 'expected'), [('GET', False, True), ('POST', True, True), ('POST', False, False)])
def test_only_safe_exhausted_network_reads_are_automatically_retryable(monkeypatch, method, retry_safe, expected):
    class Offline:
        def open(self, *args, **kwargs):
            raise urllib.error.URLError('synthetic offline test')

    monkeypatch.setattr(infrastructure.urllib.request, 'build_opener', lambda *args: Offline())
    monkeypatch.setattr(infrastructure.time, 'sleep', lambda seconds: None)
    api = infrastructure.GoogleApi(lambda: 'test-only-token')
    with pytest.raises(infrastructure.CloudSetupError) as caught:
        api.request(method, 'https://example.googleapis.com/read', retry_safe=retry_safe)
    assert caught.value.retryable is expected


@pytest.mark.parametrize('method', ['GET', 'POST'])
@pytest.mark.parametrize('bound_method', [False, True])
def test_management_401_invalidates_local_cached_token_and_refreshes_without_login(monkeypatch, method, bound_method):
    from app.video_generation.hayale_gcp import launcher

    token = launcher.BoundToken.__new__(launcher.BoundToken)
    token.binding = object()
    token.value, token.expires = 'cached-expired', float('inf')
    refreshes, observed = [], []
    monkeypatch.setattr(launcher, 'access_token', lambda **kwargs: refreshes.append(kwargs['binding']) or 'refreshed-token')

    class Opener:
        def open(self, request, **kwargs):
            observed.append(request.get_header('Authorization'))
            if len(observed) == 1:
                raise urllib.error.HTTPError(request.full_url, 401, 'expired', {}, io.BytesIO(b'{}'))
            return io.BytesIO(b'{"ok":true}')

    monkeypatch.setattr(infrastructure.urllib.request, 'build_opener', lambda *args: Opener())
    api = infrastructure.GoogleApi(token.__call__ if bound_method else token)
    assert api.request(method, 'https://example.googleapis.com/management', {}) == {'ok': True}
    assert observed == ['Bearer cached-expired', 'Bearer refreshed-token']
    assert refreshes == [token.binding]


def test_storage_401_refreshes_bound_local_controller_token(monkeypatch):
    from app.video_generation.hayale_gcp import cloud, launcher

    token = launcher.BoundToken.__new__(launcher.BoundToken)
    token.binding = object()
    token.value, token.expires = 'cached-expired', float('inf')
    refreshes, observed = [], []
    monkeypatch.setattr(launcher, 'access_token', lambda **kwargs: refreshes.append(True) or 'refreshed-token')

    def open_storage(request, **kwargs):
        observed.append(request.get_header('Authorization'))
        if len(observed) == 1:
            raise urllib.error.HTTPError(request.full_url, 401, 'expired', {}, io.BytesIO(b'{}'))
        return io.BytesIO(b'{"name":"approved-bucket"}')

    monkeypatch.setattr(cloud, '_open', open_storage)
    client = cloud.GcsClient('approved-bucket', token.__call__)
    assert client.bucket_metadata() == {'name': 'approved-bucket'}
    assert observed == ['Bearer cached-expired', 'Bearer refreshed-token']
    assert len(refreshes) == 1


def test_lost_build_acknowledgement_and_empty_listing_never_submit_again(tmp_path, monkeypatch):
    from app.video_generation.hayale_gcp import launcher

    class Storage:
        def __init__(self):
            self.objects = {}
            self.generation = 0

        def upload_file(self, name, *args, **kwargs):
            return {'object': name, 'generation': '1'}

        def metadata(self, name):
            return {'generation': str(self.generation)} if name in self.objects else None

        def get_json(self, name):
            return json.loads(self.objects[name]), self.metadata(name)

        def put_json(self, name, value, **kwargs):
            self.objects[name] = json.dumps(value)
            self.generation += 1
            return {'generation': str(self.generation)}

    class LostAcknowledgement:
        def __init__(self):
            self.submissions = 0

        def request(self, method, url, *args, **kwargs):
            if method == 'POST':
                self.submissions += 1
                raise infrastructure.CloudSetupError('synthetic lost acknowledgement')
            return {'builds': []}

    api, storage = LostAcknowledgement(), Storage()
    monkeypatch.setattr(launcher, 'worker_archive', lambda *args: 'a' * 64)
    monkeypatch.setattr(launcher, 'immutable_image', lambda *args: None)
    monkeypatch.setattr(launcher.time, 'sleep', lambda seconds: None)
    for _ in range(2):
        with pytest.raises(infrastructure.CloudSetupError, match='uncertain'):
            launcher.ensure_image(api, storage, tmp_path, tmp_path)
    assert api.submissions == 1


def artifact_fixture(tmp_path):
    verification = {'runId': 'test-hayale-run', 'planDigest': 'a' * 64,
        'technicalVerified': True, 'cloudExecution': {'verified': True, 'identityVerified': True,
            'production': True, 'provider': 'gcp-batch', 'projectId': PROJECT, 'jobUid': 'bound-cloud-job'},
        'width': 1920, 'height': 1080, 'fps': 24, 'frames': 1536,
        'durationSeconds': 64, 'sceneCount': 16, 'audioSampleRate': 48000,
        'audioChannels': 2, 'audioStartOffsetSeconds': 0, 'videoDecodePassed': True,
        'audioDecodePassed': True, 'sourceMappingVerified': True, 'codec': 'h264',
        'audioCodec': 'aac', 'audioPresent': True, 'ptsVerified': True, 'fullDecodeVerified': True}
    master = b'new-authoritatively-verified-master'
    verification['sha256'] = hashlib.sha256(master).hexdigest()
    artifacts = {NAMES[0]: master, NAMES[1]: json.dumps(verification).encode(),
                 NAMES[2]: b'new cloud delivery report'}
    receipts = {name: {'object': 'delivery/' + name, 'generation': '42',
                      'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)}
                for name, data in artifacts.items()}
    manifest = {'runId': verification['runId'], 'planDigest': verification['planDigest'],
                'status': 'cloud_verified', 'delivery': receipts}

    class Storage:
        def download(self, name, destination, **kwargs):
            destination.write_bytes(artifacts[name.removeprefix('delivery/')])
            return {}

    output = tmp_path / 'final'
    output.mkdir()
    old = {name: ('historical-' + name).encode() for name in NAMES}
    for name, data in old.items():
        (output / name).write_bytes(data)
    return Storage(), manifest, output, old, artifacts


@pytest.mark.parametrize(('field', 'bad_value'), [('frames', 1535), ('width', 1280),
    ('fps', 30), ('audioStartOffsetSeconds', .1), ('ptsVerified', False), ('fullDecodeVerified', False),
    ('cloudExecution', {'verified': True, 'identityVerified': False, 'production': False,
                        'provider': 'gcp-batch', 'projectId': PROJECT, 'jobUid': 'synthetic'})])
def test_transport_refuses_wrong_technical_contract_even_with_valid_object_hashes(tmp_path, field, bad_value):
    storage, manifest, output, old, artifacts = artifact_fixture(tmp_path)
    verification = json.loads(artifacts[NAMES[1]])
    verification[field] = bad_value
    artifacts[NAMES[1]] = json.dumps(verification).encode()
    manifest['delivery'][NAMES[1]].update(sha256=hashlib.sha256(artifacts[NAMES[1]]).hexdigest(),
                                        size=len(artifacts[NAMES[1]]))
    with pytest.raises(ValueError, match='verification|contract'):
        delivery.deliver(storage, manifest, output, open_file=False)
    assert all((output / name).read_bytes() == data for name, data in old.items())


@pytest.mark.parametrize('failed_name', [NAMES[2], NAMES[0]])
def test_interrupted_install_resumes_without_corrupting_preserved_history(tmp_path, monkeypatch, failed_name):
    storage, manifest, output, old, artifacts = artifact_fixture(tmp_path)
    original_replace = delivery.os.replace
    fail_once = True

    def interrupted(source, target):
        nonlocal fail_once
        if Path(target) == output / failed_name and fail_once:
            fail_once = False
            raise OSError('simulated interrupted final install')
        return original_replace(source, target)

    monkeypatch.setattr(delivery.os, 'replace', interrupted)
    with pytest.raises(OSError, match='interrupted'):
        delivery.deliver(storage, manifest, output, open_file=False)
    result = delivery.deliver(storage, manifest, output, open_file=False)
    assert result['transportVerified'] is True
    history = output / 'history' / hashlib.sha256(old[NAMES[0]]).hexdigest()
    for name in NAMES:
        assert (history / name).read_bytes() == old[name]
        assert (output / name).read_bytes() == artifacts[name]
