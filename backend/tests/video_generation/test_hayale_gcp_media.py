from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
from PIL import Image

from app.video_generation.hayale_gcp import media


@pytest.fixture
def synthetic(monkeypatch):
    execution = {'provider': 'synthetic-local-test', 'production': False, 'identityVerified': False}
    monkeypatch.setattr(media, '_require_cloud_execution', lambda: execution)
    return execution


def test_production_media_refuses_unbound_local_execution(tmp_path):
    with pytest.raises(media.MediaError, match='cloud execution'):
        media.prepare_seed(tmp_path / 'absent.png', tmp_path / 'seed.png', 'logo', {})
    with pytest.raises(media.MediaError, match='cloud execution'):
        media.verify_scene(tmp_path / 'absent.mp4')
    with pytest.raises(media.MediaError, match='cloud execution'):
        media.finish([], tmp_path / 'absent.wav', tmp_path / 'out.mp4', {}, {})
    assert not list(tmp_path.iterdir())


def test_cloud_execution_requires_attested_batch_identity(monkeypatch):
    monkeypatch.setattr(media.platform, 'system', lambda: 'Linux')
    monkeypatch.setenv('BATCH_JOB_UID', 'batch-uid')
    monkeypatch.setenv('BATCH_TASK_INDEX', '0')
    with pytest.raises(media.MediaError):
        with media.cloud_execution(project_id='nexus-core-481713', job_uid='batch-uid', task_index='0', identity_verified=False):
            pass
    with pytest.raises(media.MediaError):
        with media.cloud_execution(project_id='nexus-core-481713', job_uid='different', task_index='0', identity_verified=True):
            pass
    with media.cloud_execution(project_id='nexus-core-481713', job_uid='batch-uid', task_index='0', identity_verified=True):
        assert media._require_cloud_execution()['jobUid'] == 'batch-uid'
    with pytest.raises(media.MediaError):
        media._require_cloud_execution()


def test_logo_seed_preserves_original_pixel_geometry_and_hash(tmp_path, synthetic):
    source = tmp_path / 'logo.png'
    original = Image.new('RGB', (600, 240), '#c8479c')
    original.putpixel((42, 71), (7, 29, 61))
    original.save(source)
    digest = media.sha256_file(source)
    target = tmp_path / 'seed.png'
    receipt = media.prepare_seed(source, target, 'logo', {'sourceSha256': digest})
    with Image.open(target) as output:
        assert output.size == (1920, 1080)
        assert output.crop((660, 420, 1260, 660)).tobytes() == original.tobytes()
    assert receipt['sourceSha256'] == digest == media.sha256_file(source)
    assert receipt['sha256'] == media.sha256_file(target)
    assert receipt['cloudExecution'] == synthetic
    assert receipt['transform']['scaleX'] == receipt['transform']['scaleY'] == 1


def test_customer_seed_keeps_full_portrait_and_never_stretches(tmp_path, synthetic):
    source = tmp_path / 'customer.png'
    image = Image.new('RGB', (300, 600), '#245ec4')
    image.paste('#ed3120', (0, 0, 30, 600))
    image.paste('#24ef9c', (270, 0, 300, 600))
    image.save(source)
    receipt = media.prepare_seed(source, tmp_path / 'seed.png', 'customer-01', {'sourceSha256': media.sha256_file(source)})
    transform = receipt['transform']
    assert transform['scaleX'] == transform['scaleY']
    assert transform['cropBox'] == [0, 0, 300, 600]
    assert transform['foregroundSize'][0] / transform['foregroundSize'][1] == .5
    assert receipt['width'] == 1920 and receipt['height'] == 1080
    with pytest.raises(media.MediaError, match='customer|protected'):
        media.prepare_seed(source, tmp_path / 'bad.png', 'customer-01', {
            'sourceSha256': media.sha256_file(source), 'cropBox': [0, 0, 250, 600], 'subjectSafe': True})


def test_oversized_square_logo_is_uniformly_resampled_with_its_original_edge_color(tmp_path, synthetic):
    source = tmp_path / 'logo.png'
    original = Image.new('RGB', (1254, 1254), (244, 237, 223))
    original.paste((18, 38, 26), (430, 420, 820, 830))
    original.save(source)
    target = tmp_path / 'seed.png'
    receipt = media.prepare_seed(source, target, 'logo', {'sourceSha256': media.sha256_file(source),
        'backgroundExtension': 'logo-edge-color', 'preserveExactArtwork': True})
    assert receipt['transform']['foregroundSize'] == [1080, 1080]
    assert receipt['transform']['scaleX'] == receipt['transform']['scaleY']
    assert receipt['transform']['logoResampled'] is True
    with Image.open(target) as seed:
        assert seed.getpixel((0, 0)) == (244, 237, 223)


def test_reviewed_normalized_tour_crop_is_converted_to_exact_source_rectangle(tmp_path, synthetic):
    source = tmp_path / 'interior.png'
    Image.new('RGB', (600, 400), '#385125').save(source)
    receipt = media.prepare_seed(source, tmp_path / 'seed.png', 'tour-03', {
        'sourceSha256': media.sha256_file(source), 'cropNormalized': [0, 0, 1, .84375],
        'subjectSafe': True, 'fit': 'cover', 'targetWidth': 1920, 'targetHeight': 1080})
    assert receipt['transform']['cropBox'] == [0, 0, 600, 338]


def test_seed_source_hash_and_unreviewed_crop_fail_closed(tmp_path, synthetic):
    source = tmp_path / 'shop.png'
    Image.new('RGB', (640, 480), '#94b627').save(source)
    for recipe in ({'sourceSha256': 'f' * 64},
                   {'sourceSha256': media.sha256_file(source), 'cropBox': [0, 0, 500, 400]}):
        with pytest.raises(media.MediaError):
            media.prepare_seed(source, tmp_path / 'bad.png', 'exterior-01', recipe)
    assert not (tmp_path / 'bad.png').exists()


def test_seed_replay_is_hash_bound_and_does_not_overwrite(tmp_path, synthetic):
    source = tmp_path / 'source.png'
    Image.new('RGB', (800, 450), '#3467ab').save(source)
    target = tmp_path / 'seed.png'
    recipe = {'sourceSha256': media.sha256_file(source)}
    first = media.prepare_seed(source, target, 'product-01', recipe)
    assert media.prepare_seed(source, target, 'product-01', recipe)['sha256'] == first['sha256']
    target.write_bytes(b'changed-seed')
    with pytest.raises(media.MediaError, match='retained|changed'):
        media.prepare_seed(source, target, 'product-01', recipe)


def test_production_cannot_adopt_a_synthetic_local_seed_receipt(tmp_path, synthetic, monkeypatch):
    source = tmp_path / 'source.png'
    Image.new('RGB', (300, 300), '#512476').save(source)
    recipe = {'sourceSha256': media.sha256_file(source)}
    target = tmp_path / 'seed.png'
    media.prepare_seed(source, target, 'product-01', recipe)
    monkeypatch.setattr(media, '_require_cloud_execution', lambda: {
        'provider': 'gcp-batch', 'production': True, 'identityVerified': True,
        'projectId': 'nexus-core-481713', 'jobUid': 'test-attested-batch', 'taskIndex': '0'})
    with pytest.raises(media.MediaError, match='cloud|retained'):
        media.prepare_seed(source, target, 'product-01', recipe)


def test_cfr_verification_checks_each_exact_presentation_timestamp():
    timestamps = [index * 512 for index in range(96)]
    media._validate_pts(timestamps, time_base='1/12288', frames=96)
    timestamps[40] += 1
    with pytest.raises(media.MediaError, match='timestamp|CFR'):
        media._validate_pts(timestamps, time_base='1/12288', frames=96)
    with pytest.raises(media.MediaError, match='frame'):
        media._validate_pts(timestamps[:-1], time_base='1/12288', frames=96)


@pytest.mark.parametrize('changed', [{'width': 1280, 'height': 720}, {'nb_read_frames': '95'},
                                    {'avg_frame_rate': '24000/1001'}, {'duration': '4.1'},
                                    {'start_time': '.041666667'}])
def test_scene_verification_rejects_upscale_candidates_and_timeline_drift(tmp_path, synthetic, monkeypatch, changed):
    stream = {'codec_type': 'video', 'codec_name': 'h264', 'width': 1920, 'height': 1080,
        'avg_frame_rate': '24/1', 'r_frame_rate': '24/1', 'nb_read_frames': '96',
        'duration': '4.000000', 'start_time': '0.000000', 'sample_aspect_ratio': '1:1'}
    monkeypatch.setattr(media, '_probe', lambda path: {'streams': [{**stream, **changed}]})
    monkeypatch.setattr(media, '_run', lambda *a, **k: pytest.fail('invalid native source reached processing'))
    with pytest.raises(media.MediaError, match='native 1080p'):
        media.verify_scene(tmp_path / 'unqualified-source.mp4')


def _provenance(clips):
    sources = ['a' * 64, *[f'{index:064x}' for index in range(1, 15)], 'a' * 64]
    return {'runId': 'synthetic-cloud-contract', 'planDigest': 'c' * 64, 'sceneRecords': [
        {'sceneId': f'scene-{index + 1:03}', 'sourceSha256': sources[index],
         'seedSha256': sources[index], 'clipSha256': media.sha256_file(path),
         'operationName': f'projects/nexus-core-481713/locations/us-central1/publishers/google/models/veo-3.1-fast-generate-001/operations/synthetic-{index}',
         'provider': 'veo', 'state': 'verified'} for index, path in enumerate(clips)]}


def test_finishing_rejects_duplicate_clips_even_logo_slots_before_ffmpeg(tmp_path, synthetic, monkeypatch):
    clips = []
    for index in range(16):
        path = tmp_path / f'scene-{index:03}.mp4'
        path.write_bytes(f'clip-{index}'.encode())
        clips.append(path)
    clips[-1].write_bytes(clips[0].read_bytes())
    monkeypatch.setattr(media, '_run', lambda *args, **kwargs: pytest.fail('duplicate output reached FFmpeg'))
    with pytest.raises(media.MediaError, match='duplicate'):
        media.finish(clips, tmp_path / 'audio.wav', tmp_path / 'final.mp4', {}, _provenance(clips))


@pytest.mark.parametrize('mutation', ['order', 'clip-hash', 'same-operation', 'local-composition', 'wrong-source'])
def test_scene_provenance_requires_all_sixteen_distinct_provider_outputs(tmp_path, mutation):
    clips = []
    for index in range(16):
        path = tmp_path / f'scene-{index:03}.mp4'
        path.write_bytes(f'clip-{index}'.encode())
        clips.append(path)
    provenance = _provenance(clips)
    records = provenance['sceneRecords']
    if mutation == 'order':
        records[3]['sceneId'] = 'scene-005'
    elif mutation == 'clip-hash':
        records[3]['clipSha256'] = 'f' * 64
    elif mutation == 'same-operation':
        records[-1]['operationName'] = records[0]['operationName']
    elif mutation == 'local-composition':
        records[3]['provider'] = 'local-composition'
    else:
        records[3]['sourceSha256'] = records[4]['sourceSha256']
    with pytest.raises(media.MediaError):
        media._sources(clips, provenance)


def test_arrangement_requires_approved_original_explicit_segments():
    valid = {'sourceSha256': 'a' * 64, 'segments': [
        {'sourceIn': 0, 'duration': 43.24}, {'sourceIn': 38.76, 'duration': 4.48},
        {'sourceIn': 43.24, 'duration': 16.28}], 'joinFadeSeconds': .005}
    assert media._arrangement_samples(valid, source_seconds=60)[-1]['durationSamples'] == 781440
    for change in ({'segments': [{'sourceIn': 0, 'duration': 64}]}, {'joinFadeSeconds': 0},
                   {'sourceSha256': ''}):
        with pytest.raises(media.MediaError):
            media._arrangement_samples({**valid, **change}, source_seconds=60)


def test_cloud_manifest_arrangement_uses_explicit_canonical_time_keys():
    canonical = {'sourceSha256': 'a' * 64, 'segments': [
        {'sourceStartSeconds': 0, 'durationSeconds': 43.24},
        {'sourceStartSeconds': 38.76, 'durationSeconds': 4.48},
        {'sourceStartSeconds': 43.24, 'durationSeconds': 16.28}], 'joinFadeSeconds': .005}
    assert sum(item['durationSamples'] for item in media._arrangement_samples(canonical, source_seconds=60)) == 3072000
    canonical['segments'][0]['sourceIn'] = 3
    with pytest.raises(media.MediaError, match='conflicting|differ'):
        media._arrangement_samples(canonical, source_seconds=60)


@pytest.mark.parametrize('reuse_encode', [False, True])
def test_finishing_stages_and_verified_encode_reuse(tmp_path, synthetic, monkeypatch, reuse_encode):
    clips = []
    for index in range(16):
        path = tmp_path / f'scene-{index:03}.mp4'
        path.write_bytes(f'clip-{index}'.encode())
        clips.append(path)
    audio = tmp_path / 'source.wav'
    audio.write_bytes(b'original audio identity')
    arrangement = {'sourceSha256': media.sha256_file(audio), 'segments': [
        {'sourceIn': 0, 'duration': 43.24}, {'sourceIn': 38.76, 'duration': 4.48},
        {'sourceIn': 43.24, 'duration': 16.28}], 'joinFadeSeconds': .005}
    provenance = _provenance(clips)
    sources = media._sources(clips, provenance)
    identity = media._digest({'sources': sources, 'arrangement': arrangement,
                             'runId': provenance['runId'], 'planDigest': provenance['planDigest']})
    output = tmp_path / 'delivery/final.mp4'
    work = output.parent / 'finishing' / identity
    work.mkdir(parents=True)
    arranged = work / 'arranged-audio.wav'
    arranged.write_bytes(b'completed arranged audio')
    media._write_json(work / 'arranged-audio.json', {'arrangement': arrangement,
        'sourceSha256': arrangement['sourceSha256'], 'arrangedSha256': media.sha256_file(arranged)})
    candidate = work / 'retained.partial.mp4'
    candidate.write_bytes(b'completed synthetic encode candidate')
    if reuse_encode:
        media._write_json(work / 'encoded-candidate.json', {'candidate': candidate.name,
            'sha256': media.sha256_file(candidate), 'finishingRecipeSha256': identity})
    monkeypatch.setattr(media, 'verify_scene', lambda path: {'technicalVerified': True})
    monkeypatch.setattr(media, 'executable', lambda name: name)
    monkeypatch.setattr(media, '_verify', lambda path, **kwargs: {'sha256': media.sha256_file(path),
        'technicalVerified': True, 'cloudExecution': synthetic})
    commands = []
    def encode(command, **kwargs):
        if reuse_encode:
            pytest.fail('completed encode was repeated')
        commands.append(command)
        Path(command[-1]).write_bytes(b'completed synthetic encode candidate')
        return b''
    monkeypatch.setattr(media, '_run', encode)
    stages = []
    result = media.finish(clips, audio, output, arrangement, provenance, progress=stages.append)
    assert result['sha256'] == media.sha256_file(output)
    assert output.read_bytes() == b'completed synthetic encode candidate'
    assert stages == ['scene_verification', 'audio_preparation', 'finishing',
                      *([] if reuse_encode else ['mux']), 'final_verification']
    assert len(commands) == (0 if reuse_encode else 1)


@pytest.mark.skipif(os.environ.get('HAYALE_GCP_RUN_MEDIA_INTEGRATION') != '1',
                    reason='explicit real 1080p synthetic encode/decode integration')
def test_real_synthetic_sixteen_scene_cloud_media_contract(tmp_path, synthetic):
    assert shutil.which('ffmpeg') and shutil.which('ffprobe')
    clips = []
    for index in range(16):
        path = tmp_path / f'scene-{index + 1:03}.mp4'
        color = f'0x{(index * 13 + 20):02x}5982'
        media._run([media.executable('ffmpeg'), '-nostdin', '-v', 'error', '-f', 'lavfi',
            '-i', f'color=c={color}:s=1920x1080:r=24:d=4', '-an', '-c:v', 'libx264', '-preset',
            'ultrafast', '-threads', '1', '-pix_fmt', 'yuv420p', '-frames:v', '96', str(path)])
        clips.append(path)
    audio = tmp_path / 'original.wav'
    media._run([media.executable('ffmpeg'), '-nostdin', '-v', 'error', '-f', 'lavfi',
        '-i', 'sine=frequency=440:sample_rate=48000:duration=60', '-ac', '2', '-c:a', 'pcm_s16le', str(audio)])
    arrangement = {'sourceSha256': media.sha256_file(audio), 'segments': [
        {'sourceIn': 0, 'duration': 43.24}, {'sourceIn': 38.76, 'duration': 4.48},
        {'sourceIn': 43.24, 'duration': 16.28}], 'joinFadeSeconds': .005}
    output = tmp_path / 'delivery/Hayale-Flowers-Promo-1080p.mp4'
    result = media.finish(clips, audio, output, arrangement, _provenance(clips))
    assert (result['width'], result['height'], result['fps'], result['frames'], result['durationSeconds']) == (1920, 1080, 24, 1536, 64)
    assert result['ptsVerified'] and result['technicalVerified'] and result['audioStartOffsetSeconds'] == 0
    assert result['sha256'] == media.sha256_file(output)
    assert result['cloudExecution']['production'] is False
    assert media.sha256_file(audio) == arrangement['sourceSha256']
    assert len(result['sources']) == 16
    saved = json.loads(Path(result['verificationPath']).read_text())
    assert saved['sha256'] == result['sha256']
    assert Path(result['reportPath']).is_file()
