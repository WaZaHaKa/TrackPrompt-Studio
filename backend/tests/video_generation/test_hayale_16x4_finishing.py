from pathlib import Path

import pytest

from app.video_generation.contracts import ContractError
from app.video_generation.hayale_finishing import image_command, validate_source_trim, verify_delivery


def test_local_logo_customer_commands_never_redraw_or_pad():
    command = image_command('ffmpeg', Path('portrait.png'), Path('out.mp4'),
                            width=1920, height=1080, frames=96, fps=24,
                            motion='pan-right', role='customer', outro=False)
    assert '-an' in command
    assert '96' in command
    assert 'tpad' not in ' '.join(command)
    assert 'force_original_aspect_ratio=decrease' in ' '.join(command)
    logo = image_command('ffmpeg', Path('logo.png'), Path('out.mp4'), width=1920,
                         height=1080, frames=96, fps=24, motion='push', role='logo', outro=True)
    assert 'zoompan' not in ' '.join(logo)


def test_corrupt_short_sources_refuse_padding():
    with pytest.raises(ContractError, match='short'):
        validate_source_trim(source_duration=3.9, source_in=0, frames=96, fps=24)
    validate_source_trim(source_duration=8, source_in=2, frames=96, fps=24)
    for bad in (float('nan'), float('inf'), -float('inf')):
        with pytest.raises(ContractError):
            validate_source_trim(source_duration=bad, source_in=0, frames=96, fps=24)
        with pytest.raises(ContractError):
            validate_source_trim(source_duration=8, source_in=bad, frames=96, fps=24)


def test_motion_denominator_is_derived_from_slot_frame_count():
    command = image_command('ffmpeg', Path('portrait.png'), Path('out.mp4'),
                            width=1280, height=720, frames=49, fps=24,
                            motion='push', role='customer', outro=False)
    assert 'on/48' in ' '.join(command)


def test_delivery_requires_audio_stream_and_exact_frames(tmp_path, monkeypatch):
    from app.video_generation import hayale_finishing
    fake = tmp_path / 'film.mp4'
    fake.write_bytes(b'fake')
    monkeypatch.setattr(hayale_finishing, 'probe_streams', lambda *a, **k: {
        'streams': [{'codec_type': 'video', 'width': 1920, 'height': 1080,
                     'avg_frame_rate': '24/1', 'nb_read_frames': '1536',
                     'duration': '64', 'start_time': '0'}]})
    with pytest.raises(ContractError, match='audio'):
        verify_delivery(fake, width=1920, height=1080, fps=24, frames=1536,
                        require_audio=True, decode=False)


def test_export_retry_reuses_verified_segments_music_and_final(tmp_path, monkeypatch):
    from app.video_generation import hayale_finishing as module
    from app.video_generation.hayale_plan import MusicEdit, MusicSegment
    from app.video_generation.jsonio import atomic_write_json, sha256_file
    source = tmp_path/'source.mp4'
    source.write_bytes(b'synthetic clip')
    audio = tmp_path/'audio.wav'
    audio.write_bytes(b'synthetic music')
    calls = []
    monkeypatch.setattr(module, 'executable', lambda name: name)
    monkeypatch.setattr(module, 'probe_streams', lambda path: {'streams': [{'codec_type':'video','duration':'4'}]})
    def run(command, **kwargs):
        calls.append(command)
        Path(command[-1]).write_bytes(b'encoded')
        return b''
    monkeypatch.setattr(module, 'run', run)
    monkeypatch.setattr(module, 'verify_delivery', lambda path, **kwargs: {
        'technicalVerified':True, 'sha256':sha256_file(path), 'frames':kwargs['frames']})
    def music(source, edit, target, **kwargs):
        calls.append(['arrange'])
        target.write_bytes(b'arranged')
        receipt={'arrangedSha256':sha256_file(target), 'sourceSha256':sha256_file(source)}
        atomic_write_json(target.with_suffix('.json'), receipt)
        return receipt
    monkeypatch.setattr(module, 'arrange_music', music)
    plan={'planDigest':'a'*64,'title':'synthetic','projectId':'hayale-flowers-promo',
          'profile':{'resolution':'1080p'}, 'continuity':{'hayaleEdit':{'fps':24,'totalFrames':1536,
          'scenes':[{'shotId':f'shot-{i:03}','mode':'local-composition','sourceInSeconds':0,
                    'timelineFrames':96,'startFrame':(i-1)*96} for i in range(1,17)]}}}
    clips={f'shot-{i:03}':source for i in range(1,17)}
    edit=MusicEdit(source_sha256=sha256_file(audio),approved=True,
                   segments=(MusicSegment(source_in=0,duration=64),))
    def fail_once(*args):
        raise RuntimeError('interchange interrupted')
    monkeypatch.setattr(module, 'export_fcpxml', fail_once)
    with pytest.raises(RuntimeError, match='interrupted'):
        module.finish_edit(plan, clips, music_source=audio, music_edit=edit, output_root=tmp_path/'out')
    count=len(calls)
    monkeypatch.setattr(module, 'export_fcpxml', lambda *a: None)
    monkeypatch.setattr(module, 'export_fcp7_xml', lambda *a: None)
    monkeypatch.setattr(module, 'export_edl', lambda *a: None)
    result=module.finish_edit(plan, clips, music_source=audio, music_edit=edit, output_root=tmp_path/'out')
    assert len(calls)==count
    assert result['frames']==1536


def finishing_fixture(tmp_path, monkeypatch):
    from app.video_generation import hayale_finishing as finishing
    from app.video_generation.hayale_plan import MusicEdit, MusicSegment
    from app.video_generation.jsonio import atomic_write_json, sha256_file
    clips = {}
    scenes = []
    for i in range(16):
        shot_id = f'shot-{i+1:03}'
        clips[shot_id] = tmp_path / f'{shot_id}.mp4'
        clips[shot_id].write_bytes(f'source-{i}'.encode())
        scenes.append({'shotId': shot_id, 'mode': 'local-composition', 'sourceInSeconds': 0,
                       'timelineFrames': 96, 'startFrame': i*96})
    plan = {'planDigest': 'a'*64, 'projectId': 'hayale-flowers-promo', 'title': 'Synthetic',
            'profile': {'resolution': '1080p'},
            'continuity': {'hayaleEdit': {'fps': 24, 'totalFrames': 1536, 'scenes': scenes}}}
    music = tmp_path / 'music.wav'
    music.write_bytes(b'synthetic music source')
    music_edit = MusicEdit(source_sha256=sha256_file(music), approved=True,
                           segments=(MusicSegment(source_in=0, duration=64),))
    calls = {'media': [], 'music': [], 'verified': []}
    monkeypatch.setattr(finishing, 'executable', lambda name: name)
    monkeypatch.setattr(finishing, 'probe_streams', lambda path: {'streams': [{'codec_type': 'video', 'duration': 8}]})
    def run(command, **kwargs):
        calls['media'].append(command)
        Path(command[-1]).write_bytes(b'encoded synthetic media')
        return b''
    monkeypatch.setattr(finishing, 'run', run)
    def arrange(source, edit, target, **kwargs):
        calls['music'].append(str(target))
        target.write_bytes(b'arranged synthetic music')
        receipt = {'sourceSha256': edit.source_sha256, 'arrangement': edit.model_dump(by_alias=True),
                   'arrangedSha256': sha256_file(target), 'originalPreserved': True, 'durationSeconds': 64}
        atomic_write_json(target.with_suffix('.json'), receipt)
        return receipt
    monkeypatch.setattr(finishing, 'arrange_music', arrange)
    def verify(path, **kwargs):
        calls['verified'].append(path)
        return {'technicalVerified': True, 'creativeApproval': 'pending', 'frames': kwargs['frames'],
                'fps': 24, 'durationSeconds': kwargs['frames']/24,
                'width': 1920, 'height': 1080, 'audioPresent': kwargs['require_audio'],
                'sha256': sha256_file(path)}
    monkeypatch.setattr(finishing, 'verify_delivery', verify)
    for name in ('export_fcpxml', 'export_fcp7_xml', 'export_edl'):
        monkeypatch.setattr(finishing, name, lambda timeline, target: target.write_text('synthetic interchange'))
    return finishing, plan, clips, music, music_edit, calls


def test_interchange_retry_reuses_all_verified_media_without_ffmpeg(tmp_path, monkeypatch):
    finishing, plan, clips, music, edit, calls = finishing_fixture(tmp_path, monkeypatch)
    failure = [True]
    def export(timeline, target):
        if failure.pop() if failure else False:
            raise ContractError('injected interchange failure')
        target.write_text('synthetic fcpxml')
    monkeypatch.setattr(finishing, 'export_fcpxml', export)
    kwargs = dict(music_source=music, music_edit=edit, output_root=tmp_path/'export')
    with pytest.raises(ContractError, match='injected'):
        finishing.finish_edit(plan, clips, **kwargs)
    before = {key: list(value) for key, value in calls.items()}
    result = finishing.finish_edit(plan, clips, **kwargs)
    assert calls == before
    assert len(list((tmp_path/'export').glob('finish-*'))) == 1
    assert Path(result['exportRoot'], 'verification-report.json').is_file()
    assert Path(result['deliveryPath']).is_file()


def test_final_verification_retry_does_not_repeat_mux_segments_or_music(tmp_path, monkeypatch):
    finishing, plan, clips, music, edit, calls = finishing_fixture(tmp_path, monkeypatch)
    original = finishing.verify_delivery
    failure = [True]
    def verify(path, **kwargs):
        if kwargs['require_audio'] and failure:
            failure.pop()
            raise ContractError('injected verifier failure')
        return original(path, **kwargs)
    monkeypatch.setattr(finishing, 'verify_delivery', verify)
    kwargs = dict(music_source=music, music_edit=edit, output_root=tmp_path/'export')
    with pytest.raises(ContractError, match='injected'):
        finishing.finish_edit(plan, clips, **kwargs)
    before = len(calls['media']), len(calls['music'])
    result = finishing.finish_edit(plan, clips, **kwargs)
    assert (len(calls['media']), len(calls['music'])) == before
    assert result['technicalVerified']


def test_changed_verified_segment_blocks_without_overwriting_evidence(tmp_path, monkeypatch):
    finishing, plan, clips, music, edit, calls = finishing_fixture(tmp_path, monkeypatch)
    kwargs = dict(music_source=music, music_edit=edit, output_root=tmp_path/'export')
    result = finishing.finish_edit(plan, clips, **kwargs)
    segment = Path(result['exportRoot'])/'segment-001.mp4'
    segment.write_bytes(b'conflicting retained content')
    before = len(calls['media'])
    with pytest.raises(ContractError, match='conflict|changed'):
        finishing.finish_edit(plan, clips, **kwargs)
    assert segment.read_bytes() == b'conflicting retained content'
    assert len(calls['media']) == before
