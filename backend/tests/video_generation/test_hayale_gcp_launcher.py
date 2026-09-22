from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


def test_cloud_plan_has_fifteen_sources_sixteen_generated_slots_and_new_identity(tmp_path: Path) -> None:
    from app.video_generation.hayale_gcp.config import ROLES, build_plan

    entries = {}
    for role in ROLES:
        path = tmp_path / (role + '.png')
        path.write_bytes(role.encode())
        entries[role] = {'relativePath': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                         'mimeType': 'image/png', 'seedRecipe': {'strategy': 'preserve-subject'}}
    audio = tmp_path / 'original.wav'
    audio.write_bytes(b'approved soundtrack')
    mapping = {'schemaVersion': 1, 'sources': entries,
               'soundtrack': {'relativePath': audio.name, 'sha256': hashlib.sha256(audio.read_bytes()).hexdigest()}}
    plan = build_plan(mapping, tmp_path)
    assert len(plan['sources']) == 15
    assert len(plan['scenes']) == 16
    assert plan['scenes'][0]['role'] == plan['scenes'][-1]['role'] == 'logo'
    assert all(scene['mode'] == 'gcp-image-to-video' for scene in plan['scenes'])
    assert plan['runId'].startswith('hayale-gcp16-')
    assert plan['profile']['resolution'] == '1080p'
    assert plan['profile']['durationSeconds'] == 4
    assert plan['policy']['maxGenerationsPerScene'] == 3
    assert 'maxSpendUsd' not in json.dumps(plan)
    assert build_plan(mapping, tmp_path)['planDigest'] == plan['planDigest']
    entries['tour-03']['sha256'] = '0' * 64
    with pytest.raises(ValueError, match='hash'):
        build_plan(mapping, tmp_path)


def test_mapping_rejects_duplicate_customers_and_outside_source_root(tmp_path: Path) -> None:
    from app.video_generation.hayale_gcp.config import validate_mapping

    with pytest.raises(ValueError):
        validate_mapping({'schemaVersion': 1, 'sources': {}, 'soundtrack': {}})


def test_batch_is_bounded_and_cloud_only() -> None:
    from app.video_generation.hayale_gcp.infrastructure import batch_spec

    value = batch_spec('hayale-gcp16-' + '1' * 24, 'us-central1-docker.pkg.dev/p/r/w@sha256:' + 'a' * 64)
    task = value['taskGroups'][0]['taskSpec']
    assert task['maxRetryCount'] == 2
    assert task['lifecyclePolicies'] == [{'action': 'FAIL_TASK', 'actionCondition': {'exitCodes': [1]}}]
    assert task['maxRunDuration'] == '43200s'
    assert value['taskGroups'][0]['taskCount'] == 1
    assert '--manifest' in task['runnables'][0]['container']['commands']
    assert value['allocationPolicy']['serviceAccount']['email'].startswith('trackprompt-hayale-worker@')
    assert value['allocationPolicy']['location']['allowedLocations'] == [
        'zones/us-central1-a', 'zones/us-central1-b', 'zones/us-central1-c']


def test_launchers_require_pwsh_and_set_absolute_fresh_environment() -> None:
    root = Path(__file__).resolve().parents[3]
    cmd = (root / 'FINISH-HAYALE-GCP.cmd').read_text()
    ps = (root / 'FINISH-HAYALE-GCP.ps1').read_text()
    assert 'pwsh.exe' in cmd
    assert 'powershell.exe' not in cmd.lower()
    assert '$PSScriptRoot' in ps
    assert 'TRACKPROMPT_GCP_AUTH_CONFIG' in ps
    assert 'hayale-gcp-20260922' in ps
    assert 'ffmpeg' not in ps.lower()


def test_delivery_preserves_previous_master_and_rejects_bad_transport(tmp_path: Path) -> None:
    from app.video_generation.hayale_gcp.config import NAMES
    from app.video_generation.hayale_gcp.delivery import deliver

    target = tmp_path / 'delivery'
    target.mkdir()
    (target / NAMES[0]).write_bytes(b'historical master')
    movie = b'new cloud master'
    sha = hashlib.sha256(movie).hexdigest()
    verification = json.dumps({'runId': 'run', 'planDigest': 'plan', 'sha256': sha,
        'technicalVerified': True, 'width': 1920, 'height': 1080, 'fps': 24, 'frames': 1536,
        'durationSeconds': 64, 'sceneCount': 16, 'codec': 'h264', 'audioCodec': 'aac',
        'audioStartOffsetSeconds': 0, 'audioPresent': True, 'ptsVerified': True,
        'fullDecodeVerified': True, 'cloudExecution': {'provider': 'gcp-batch',
            'projectId': 'nexus-core-481713', 'jobUid': 'test-cloud-job',
            'identityVerified': True, 'production': True}}).encode()
    objects = dict(zip(NAMES, [movie, verification, b'cloud report'], strict=True))
    manifest = {'runId': 'run', 'planDigest': 'plan', 'status': 'cloud_verified',
                'delivery': {name: {'object': name, 'generation': '1', 'sha256': hashlib.sha256(data).hexdigest(),
                                    'size': len(data)} for name, data in objects.items()}}

    class Storage:
        def download(self, name: str, path: Path, **kwargs: object) -> dict[str, object]:
            path.write_bytes(objects[name])
            return {}

    result = deliver(Storage(), manifest, target, open_file=False)
    assert result['transportVerified']
    assert (target / NAMES[0]).read_bytes() == movie
    assert any(p.read_bytes() == b'historical master' for p in (target / 'history').rglob('*.mp4'))
    objects[NAMES[0]] = b'corruption'
    (target / NAMES[0]).write_bytes(b'historical master')
    with pytest.raises(ValueError, match='hash'):
        deliver(Storage(), manifest, target, open_file=False)
    assert (target / NAMES[0]).read_bytes() == b'historical master'


@pytest.mark.skipif(os.name != 'nt', reason='Actual Windows fresh-shell launcher contract')
@pytest.mark.parametrize('wrapper', [False, True])
def test_actual_powershell_launcher_from_unrelated_cwd_and_space_path(tmp_path: Path, wrapper: bool) -> None:
    root = Path(__file__).resolve().parents[3]
    pwsh = shutil.which('pwsh.exe')
    assert pwsh, 'PowerShell 7 must be installed for the maintained launcher'
    test_repo = tmp_path / 'TrackPrompt Studio with spaces'
    (test_repo / 'tools').mkdir(parents=True)
    shutil.copyfile(root / 'FINISH-HAYALE-GCP.ps1', test_repo / 'FINISH-HAYALE-GCP.ps1')
    shutil.copyfile(root / 'FINISH-HAYALE-GCP.cmd', test_repo / 'FINISH-HAYALE-GCP.cmd')
    (test_repo / 'tools/finish-hayale-gcp.py').write_text(
        'import json, os, sys\nprint(json.dumps({"argv":sys.argv[1:],"auth":os.environ["TRACKPROMPT_GCP_AUTH_CONFIG"],"pythonpath":os.environ["PYTHONPATH"]}))\n')
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(('TRACKPROMPT_', 'CLOUDSDK_', 'PYTHON'))}
    environment['PATH'] = str(Path(os.environ['SystemRoot']) / 'System32')
    command = [pwsh, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(test_repo / 'FINISH-HAYALE-GCP.ps1'), '-Check']
    if wrapper:
        environment['PATH'] = str(Path(pwsh).parent) + os.pathsep + environment['PATH']
        command = [str(Path(os.environ['SystemRoot']) / 'System32/cmd.exe'), '/d', '/c',
                   str(test_repo / 'FINISH-HAYALE-GCP.cmd'), '-Check']
    result = subprocess.run(command, cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=45, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    actual = json.loads(result.stdout.strip())
    assert actual['argv'] == ['--repository', str(test_repo), '--check']
    assert actual['auth'] == str(Path(os.environ['LOCALAPPDATA']) / 'TrackPrompt/hayale-gcp-20260922/auth.json')
    assert actual['pythonpath'] == str(test_repo / 'backend')


def test_actual_source_map_role_counts_order_and_rejection_of_duplicates() -> None:
    from app.video_generation.hayale_gcp.config import ORDER, validate_mapping

    root = Path(__file__).resolve().parents[3]
    mapping = json.loads((root / 'video-projects/hayale-flowers-promo/v4-gcp-only/source-map.json').read_text())
    validate_mapping(mapping)
    assert tuple(mapping['sceneOrder']) == ORDER
    for prefix, expected in [('customer', 3), ('exterior', 2), ('product', 3), ('vibe', 3), ('tour', 3)]:
        assert sum(key.startswith(prefix + '-') for key in mapping['sources']) == expected
    mapping['sources']['customer-03']['sha256'] = mapping['sources']['customer-01']['sha256']
    with pytest.raises(ValueError, match='distinct'):
        validate_mapping(mapping)
    mapping['sources']['customer-03']['relativePath'] = '../private/unselected.jpg'
    with pytest.raises(ValueError, match='safe relative path'):
        validate_mapping(mapping)


def test_cloud_resume_does_not_require_the_original_source_drive(tmp_path: Path) -> None:
    import copy

    from app.video_generation.hayale_gcp.cloud import StateConflict
    from app.video_generation.hayale_gcp.config import build_plan
    from app.video_generation.hayale_gcp.launcher import bind_run

    root = Path(__file__).resolve().parents[3]
    mapping = json.loads((root / 'video-projects/hayale-flowers-promo/v4-gcp-only/source-map.json').read_text())
    plan = build_plan(mapping, tmp_path / 'disconnected-original-drive', verify_local_sources=False)
    retained = copy.deepcopy(plan)
    for role, source in retained['sources'].items():
        source.update(object=plan['gcsRoot'] + 'inputs/stills/' + role, generation='1', size=123)
    retained['soundtrack'].update(object=plan['gcsRoot'] + 'inputs/audio/original.wav', generation='1', size=123)
    manifest_name = plan['gcsRoot'] + 'state/run-manifest.json'
    objects = {manifest_name: retained, 'hayale-final/final-gcp16-binding.json':
               {'runId': plan['runId'], 'planDigest': plan['planDigest']}}

    class Storage:
        def put_json(self, name, value, if_generation_match):
            raise StateConflict()

        def metadata(self, name):
            return {'generation': '1'} if name in objects else None

        def get_json(self, name):
            return objects[name], {'generation': '1'}

        def upload_file(self, *args, **kwargs):
            pytest.fail('An already-bound original must not be re-uploaded on recovery')

    assert bind_run(Storage(), plan, mapping, tmp_path / 'disconnected-original-drive') == retained
