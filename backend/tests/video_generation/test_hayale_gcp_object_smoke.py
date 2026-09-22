from __future__ import annotations

import hashlib
import json

import pytest

from app.video_generation.hayale_gcp import worker
from app.video_generation.hayale_gcp.cloud import CloudError, StateConflict
from app.video_generation.hayale_gcp.infrastructure import batch_spec

PREFIX = 'hayale-final/hayale-gcp16-fbbaa46568b1a30d0e760e76/smoke-object-io/' + 'a' * 24 + '/'
ACCOUNT = 'trackprompt-hayale-worker@nexus-core-481713.iam.gserviceaccount.com'


class FakeStorage:
    bucket = 'nexus-brain-481713'

    def __init__(self, *, forbid_delete=False):
        self.objects = {}
        self.generation = 0
        self.operations = []
        self.forbid_delete = forbid_delete

    def bucket_metadata(self):
        self.operations.append(('bucket_metadata', self.bucket))
        return {'name': self.bucket}

    def _write(self, name, data, expected):
        current = self.objects.get(name)
        if (expected == 0 and current) or (expected != 0 and (not current or str(current[1]['generation']) != str(expected))):
            raise StateConflict()
        self.generation += 1
        meta = {'name': name, 'generation': str(self.generation), 'metageneration': '1',
                'size': str(len(data)), 'metadata': {'sha256': hashlib.sha256(data).hexdigest()},
                'contentType': 'application/octet-stream'}
        self.objects[name] = (data, meta)
        return json.loads(json.dumps(meta))

    def metadata(self, name, generation=None):
        self.operations.append(('metadata', name))
        value = self.objects.get(name)
        if not value or (generation is not None and str(generation) != value[1]['generation']):
            return None
        return json.loads(json.dumps(value[1]))

    def upload_file(self, name, path, *, if_generation_match=0, **kwargs):
        self.operations.append(('upload_file', name))
        return self.receipt(self._write(name, path.read_bytes(), if_generation_match))

    @staticmethod
    def receipt(meta):
        return {'object': meta['name'], 'generation': meta['generation'], 'size': int(meta['size']),
                'sha256': meta['metadata']['sha256']}

    def download(self, name, path, *, generation, expected_sha256=None):
        self.operations.append(('download', name))
        meta = self.metadata(name, generation)
        if meta is None:
            raise CloudError('storage_generation_missing', status=404)
        data = self.objects[name][0]
        assert expected_sha256 is None or expected_sha256 == hashlib.sha256(data).hexdigest()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return self.receipt(meta)

    def list_objects(self, prefix):
        self.operations.append(('list_objects', prefix))
        return [json.loads(json.dumps(meta)) for name, (_, meta) in self.objects.items() if name.startswith(prefix)]

    def put_json(self, name, value, if_generation_match):
        self.operations.append(('put_json', name))
        return self._write(name, json.dumps(value, sort_keys=True).encode(), if_generation_match)

    def get_json(self, name, generation=None):
        self.operations.append(('get_json', name))
        meta = self.metadata(name, generation)
        if meta is None:
            raise CloudError('storage_object_missing', status=404)
        return json.loads(self.objects[name][0]), meta

    def update_metadata(self, name, metadata, *, generation, required_prefix):
        self.operations.append(('update_metadata', name))
        assert name.startswith(required_prefix) and len(required_prefix.removesuffix('/').rsplit('/', 1)[-1]) == 32
        current = self.metadata(name, generation)
        assert current
        current['metadata'].update(metadata)
        current['metageneration'] = '2'
        self.objects[name] = (self.objects[name][0], current)
        return current

    def delete_object(self, name, *, generation, required_prefix):
        self.operations.append(('delete_object', name))
        assert name.startswith(required_prefix) and len(required_prefix.removesuffix('/').rsplit('/', 1)[-1]) == 32
        if self.forbid_delete:
            raise CloudError('storage_http_403', status=403)
        if self.metadata(name, generation):
            del self.objects[name]


@pytest.fixture
def cloud_runtime(monkeypatch):
    monkeypatch.setattr(worker, '_smoke_runtime_proof', lambda project, account, verified: {
        'provider': 'gcp-batch', 'projectId': project, 'serviceAccount': account,
        'jobUid': 'synthetic-unit-test-only', 'taskIndex': '0', 'identityVerified': verified,
        'syntheticTest': True})


def test_cloud_smoke_exercises_real_client_operations_without_provider(tmp_path, cloud_runtime):
    storage = FakeStorage()
    result = worker.object_io_smoke(storage, PREFIX, tmp_path, project='nexus-core-481713',
                                    service_account=ACCOUNT, identity_verified=True)
    assert result['ok'] and result['generationSubmitted'] is False
    expected = {'bucket_metadata', 'object_create', 'object_metadata', 'object_download', 'object_sha256',
                'object_list', 'generation_bound_read', 'json_create', 'json_cas_update',
                'json_read_after_update', 'object_metadata_update', 'object_delete'}
    assert expected <= set(result['categories'])
    assert all(result['categories'][name]['ok'] for name in expected)
    assert set(storage.objects) == {PREFIX + 'result.json'}
    assert storage.get_json(PREFIX + 'result.json')[0]['runtime']['syntheticTest'] is True
    assert all(name.startswith(PREFIX) for operation, name in storage.operations if operation != 'bucket_metadata')


def test_smoke_forbidden_cleanup_retains_only_disposable_objects(tmp_path, cloud_runtime):
    storage = FakeStorage(forbid_delete=True)
    result = worker.object_io_smoke(storage, PREFIX, tmp_path, project='nexus-core-481713',
                                    service_account=ACCOUNT, identity_verified=True)
    assert result['ok'] is True
    assert result['categories']['object_delete']['ok'] is False
    assert result['cleanup']['status'] == 'retained-permission-denied'
    assert len(storage.objects) == 3
    assert result['cleanup']['retentionEnforced'] is False


def test_corrupt_roundtrip_is_failed_and_never_claims_object_io_success(tmp_path, cloud_runtime):
    class CorruptStorage(FakeStorage):
        def download(self, name, path, **kwargs):
            receipt = super().download(name, path, **kwargs)
            path.write_bytes(b'corrupt-disposable-test-object')
            return receipt

    storage = CorruptStorage()
    with pytest.raises(CloudError, match='object_download_failed'):
        worker.object_io_smoke(storage, PREFIX, tmp_path, project='nexus-core-481713',
                               service_account=ACCOUNT, identity_verified=True)
    result, _ = storage.get_json(PREFIX + 'result.json')
    assert result['ok'] is False and result['categories']['object_download']['ok'] is False
    assert result['generationSubmitted'] is False


def test_failed_smoke_retains_safe_operation_context(tmp_path, cloud_runtime):
    context = {'operation': 'object_metadata_get', 'method': 'GET', 'objectHash': 'a' * 64, 'generation': '42'}

    class FailedStorage(FakeStorage):
        def metadata(self, name, generation=None):
            if name.endswith('/payload.bin'):
                raise CloudError('storage_http_404', status=404, context=context)
            return super().metadata(name, generation)

    storage = FailedStorage()
    with pytest.raises(CloudError, match='storage_http_404'):
        worker.object_io_smoke(storage, PREFIX, tmp_path, project='nexus-core-481713',
                               service_account=ACCOUNT, identity_verified=True)
    report, _ = storage.get_json(PREFIX + 'result.json')
    assert report['error']['context'] == context
    assert report['error']['httpStatus'] == 404


def test_smoke_rejects_unverified_runtime_before_any_io(tmp_path):
    storage = FakeStorage()
    with pytest.raises(CloudError, match='verified_attached_smoke_runtime_required'):
        worker.object_io_smoke(storage, PREFIX, tmp_path, project='nexus-core-481713',
                               service_account=ACCOUNT, identity_verified=False)
    assert storage.operations == []


@pytest.mark.parametrize('prefix', ['hayale-final/production/', PREFIX + '../', 'other/smoke-object-io/'])
def test_smoke_rejects_unsafe_prefix_before_object_io(tmp_path, cloud_runtime, prefix):
    storage = FakeStorage()
    with pytest.raises(CloudError, match='smoke_prefix'):
        worker.object_io_smoke(storage, prefix, tmp_path, project='nexus-core-481713',
                               service_account=ACCOUNT, identity_verified=True)
    assert storage.operations == []


def test_batch_smoke_passes_canonical_object_io_prefix():
    spec = batch_spec('bounded-smoke-job', 'image@sha256:' + 'a' * 64, smoke=True, smoke_prefix=PREFIX)
    command = spec['taskGroups'][0]['taskSpec']['runnables'][0]['container']['commands']
    assert command[command.index('--smoke-prefix') + 1] == PREFIX
    assert '--check-runtime' in command and '--manifest' not in command
