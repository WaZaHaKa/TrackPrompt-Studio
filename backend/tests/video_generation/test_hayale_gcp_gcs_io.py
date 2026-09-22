from __future__ import annotations

import io
import json
import urllib.error
import urllib.parse

import pytest

from app.video_generation.hayale_gcp import cloud


class Response(io.BytesIO):
    status = 200

    def __init__(self, value):
        super().__init__(json.dumps(value).encode())


def test_mutable_manifest_replaced_between_metadata_and_media_get_recovers(monkeypatch):
    """Real incident: a non-versioned bucket no longer serves generation 100."""
    current = '100'
    calls = []

    def open_request(request, **kwargs):
        nonlocal current
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
        calls.append((request.method, query))
        if query.get('alt') == ['media']:
            if query['generation'] != [current]:
                raise urllib.error.HTTPError(request.full_url, 404, 'No such object generation', {}, io.BytesIO())
            return Response({'stage': 'generation'})
        result = Response({'name': 'hayale-final/run/state/run-manifest.json', 'generation': current})
        current = '101'  # The worker's conditional overwrite commits before the reader's media GET.
        return result

    monkeypatch.setattr(cloud, '_open', open_request)
    value, metadata = cloud.GcsClient('bucket', lambda: 'token').get_json('hayale-final/run/state/run-manifest.json')
    assert value == {'stage': 'generation'}
    assert metadata['generation'] == '101'
    assert all(method == 'GET' for method, _ in calls)
    assert [q['generation'] for _, q in calls if q.get('alt') == ['media']] == [['100'], ['101']]


def test_explicit_generation_never_switches_to_latest(monkeypatch):
    calls = []

    def open_request(request, **kwargs):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
        calls.append(query)
        if query.get('alt') == ['media']:
            raise urllib.error.HTTPError(request.full_url, 404, 'No such object generation', {}, io.BytesIO())
        return Response({'name': 'original', 'generation': '100'})

    monkeypatch.setattr(cloud, '_open', open_request)
    with pytest.raises(cloud.CloudError) as failure:
        cloud.GcsClient('bucket', lambda: 'token').get_json('original', generation='100')
    assert failure.value.status == 404
    assert len(calls) == 2
    assert all(query.get('generation') == ['100'] for query in calls)


def test_missing_generation_with_unchanged_metadata_is_not_a_race(monkeypatch):
    def open_request(request, **kwargs):
        if 'alt=media' in request.full_url:
            raise urllib.error.HTTPError(request.full_url, 404, 'Missing', {}, io.BytesIO())
        return Response({'name': 'manifest', 'generation': '100'})

    monkeypatch.setattr(cloud, '_open', open_request)
    with pytest.raises(cloud.CloudError) as failure:
        cloud.GcsClient('bucket', lambda: 'token').get_json('manifest')
    assert failure.value.status == 404


def test_continuous_replacement_has_bounded_read_retry(monkeypatch):
    revision, media_calls = 0, 0
    def open_request(request, **kwargs):
        nonlocal revision, media_calls
        if 'alt=media' in request.full_url:
            media_calls += 1
            raise urllib.error.HTTPError(request.full_url, 404, 'Replaced', {}, io.BytesIO())
        revision += 1
        return Response({'name': 'manifest', 'generation': str(revision)})

    monkeypatch.setattr(cloud, '_open', open_request)
    with pytest.raises(cloud.CloudError, match='storage_snapshot_changed') as failure:
        cloud.GcsClient('bucket', lambda: 'token').get_json('manifest')
    assert failure.value.retryable
    assert media_calls == 5


def test_storage_error_has_bounded_context_without_object_or_token(monkeypatch):
    private_name = 'hayale-final/run/inputs/private person photo.png'
    def open_request(request, **kwargs):
        raise urllib.error.HTTPError(request.full_url, 403, 'private provider text', {}, io.BytesIO())

    monkeypatch.setattr(cloud, '_open', open_request)
    with pytest.raises(cloud.CloudError) as failure:
        cloud.GcsClient('bucket', lambda: 'private-access-token').metadata(private_name, '100')
    error = failure.value.to_dict()
    assert error['context']['operation'] == 'object_metadata_get'
    assert error['context']['method'] == 'GET'
    assert len(error['context']['objectHash']) == 64
    assert error['context']['generation'] == '100'
    assert private_name not in json.dumps(error)
    assert 'private-access-token' not in json.dumps(error)


def test_invalid_json_keeps_safe_operation_context(monkeypatch):
    monkeypatch.setattr(cloud, '_open', lambda *args, **kwargs: io.BytesIO(b'private malformed state'))
    with pytest.raises(cloud.CloudError, match='cloud_response_invalid') as failure:
        cloud.GcsClient('bucket', lambda: 'token').metadata('state/manifest.json')
    assert failure.value.context['operation'] == 'object_metadata_get'
    assert 'private malformed state' not in json.dumps(failure.value.to_dict())


def test_disposable_mutations_are_prefix_and_generation_bound(monkeypatch):
    prefix = 'hayale-final/run/smoke-object-io/image/' + 'a' * 32 + '/'
    name = prefix + 'tiny-state.json'
    requests = []

    def open_request(request, **kwargs):
        requests.append(request)
        if request.method == 'DELETE':
            response = Response({})
            response.status = 204
            return response
        return Response({'name': name, 'generation': '100', 'metageneration': '3',
                         'metadata': {'sha256': 'original-hash'}})

    monkeypatch.setattr(cloud, '_open', open_request)
    client = cloud.GcsClient('bucket', lambda: 'token')
    client.update_metadata(name, {'smoke': 'updated'}, generation='100', required_prefix=prefix)
    patch = next(r for r in requests if r.method == 'PATCH')
    assert json.loads(patch.data)['metadata'] == {'sha256': 'original-hash', 'smoke': 'updated'}
    assert urllib.parse.parse_qs(urllib.parse.urlsplit(patch.full_url).query) == {
        'generation': ['100'], 'ifGenerationMatch': ['100'], 'ifMetagenerationMatch': ['3']}
    client.delete_object(name, generation='100', required_prefix=prefix)
    assert 'generation=100&ifGenerationMatch=100' in requests[-1].full_url
    with pytest.raises(cloud.CloudError, match='disposable_smoke_prefix_required'):
        client.delete_object('hayale-final/run/state/run-manifest.json', generation='100', required_prefix=prefix)
