"""Local frame-exact finishing. No provider, padding, looping or time stretch."""
from __future__ import annotations

import json
import math
import os
import shutil
from fractions import Fraction
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.subprocess_utils import run_process_bounded
from app.wnp import emit_lifecycle

from .audio import probe_audio
from .contracts import ContractError
from .davinci import export_edl, export_fcp7_xml, export_fcpxml
from .hayale_plan import AssetSelection, MusicEdit, validate_music
from .jsonio import atomic_write_json, read_json, sha256_file, sha256_json


def executable(name: str) -> str:
    result = os.getenv(f'TRACKPROMPT_MC_{name.upper()}_PATH') or shutil.which(name)
    if not result:
        raise ContractError(f'{name} is required; configure its existing installation')
    return result


def run(command: list[str], *, timeout: int = 600) -> bytes:
    result = run_process_bounded(command, timeout_seconds=timeout,
                                 stdout_limit=2_000_000, stderr_limit=65_536, hide_window=True)
    if result.returncode or result.stdout_exceeded or result.stderr_exceeded:
        raise ContractError('local media command failed; source and prior outputs are retained')
    return result.stdout


def probe_streams(path: Path) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(run([
        executable('ffprobe'), '-v', 'error', '-count_frames', '-show_streams',
        '-show_format', '-of', 'json', '--', str(path),
    ]))
    return value


def verify_delivery(path: Path, *, width: int, height: int, fps: int, frames: int,
                    require_audio: bool, decode: bool = True) -> dict[str, Any]:
    value = probe_streams(path)
    video = [s for s in value['streams'] if s['codec_type'] == 'video']
    audio = [s for s in value['streams'] if s['codec_type'] == 'audio']
    if len(video) != 1 or len(audio) != int(require_audio):
        raise ContractError('delivery video/audio stream layout differs from the plan')
    stream = video[0]
    duration = frames / fps
    if (int(stream['width']) != width or int(stream['height']) != height
        or Fraction(stream['avg_frame_rate']) != fps or int(stream.get('nb_read_frames', -1)) != frames
        or abs(float(stream['duration']) - duration) > .5 / fps):
        raise ContractError('delivery dimensions, frame rate, frame count or duration differs from the plan')
    if audio and (abs(float(audio[0]['duration']) - duration) > .05
                  or abs(float(audio[0].get('start_time', 0)) - float(stream.get('start_time', 0))) > .05
                  or int(audio[0]['sample_rate']) != 48000 or int(audio[0]['channels']) != 2):
        raise ContractError('delivery music duration or A/V alignment differs from the plan')
    if decode:
        run([executable('ffmpeg'), '-nostdin', '-v', 'error', '-xerror', '-i', str(path),
             '-map', '0:v:0', *(['-map', '0:a:0'] if require_audio else []), '-f', 'null', '-'])
    return {'technicalVerified': True, 'creativeApproval': 'pending', 'frames': frames,
            'fps': fps, 'durationSeconds': duration, 'width': width, 'height': height,
            'audioPresent': bool(audio), 'sha256': sha256_file(path)}


def validate_source_trim(*, source_duration: float, source_in: float, frames: int, fps: int) -> None:
    if not math.isfinite(source_duration) or not math.isfinite(source_in) or frames <= 0 or fps <= 0:
        raise ContractError('source trim requires finite durations and positive frame counts/rate')
    if source_in < 0 or source_in + frames / fps > source_duration + .001:
        raise ContractError('source is too short for the exact trim; padding and retiming are prohibited')


def _encode() -> list[str]:
    return ['-an', '-c:v', 'libx264', '-preset', 'fast', '-crf', '20', '-pix_fmt', 'yuv420p',
            '-map_metadata', '-1', '-movflags', '+faststart']


def image_command(ffmpeg: str, source: Path, target: Path, *, width: int, height: int,
                  frames: int, fps: int, motion: str, role: str, outro: bool) -> list[str]:
    # A inset fits the entire source; small photo-plane moves keep faces in view.
    inset_width, inset_height = (width * 9 // 10) // 2 * 2, (height * 9 // 10) // 2 * 2
    filters = [f'scale={inset_width}:{inset_height}:force_original_aspect_ratio=decrease',
               f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x10251c', 'setsar=1']
    if role == 'logo':
        filters.append(f'fade=t=in:st=0:d={0.4 if outro else 0.8}')
    elif motion != 'hold':
        denominator = max(1, frames - 1)
        zoom = {'push': f'1+0.02*on/{denominator}', 'pull': f'1.02-0.02*on/{denominator}'}.get(motion, '1.02')
        x = {'pan-right': f'(iw-iw/zoom)*on/{denominator}', 'pan-left': f'(iw-iw/zoom)*(1-on/{denominator})'}.get(motion, '(iw-iw/zoom)/2')
        filters.append(f"zoompan=z='{zoom}':x='{x}':y='(ih-ih/zoom)/2':d=1:s={width}x{height}:fps={fps}")
    filters.extend([f'trim=end_frame={frames}', f'setpts=N/({fps}*TB)', 'format=yuv420p'])
    return [ffmpeg, '-nostdin', '-v', 'error', '-n', '-loop', '1', '-framerate', str(fps),
            '-t', str(frames / fps), '-i', str(source), '-vf', ','.join(filters),
            '-frames:v', str(frames), *_encode(), str(target)]


def render_local_scene(asset: AssetSelection, slot: dict[str, Any], target: Path, *,
                       width: int, height: int, fps: int = 24) -> dict[str, Any]:
    source = asset.verify()
    target.parent.mkdir(parents=True, exist_ok=True)
    receipt_path = target.with_suffix('.verified.json')
    recipe = {'sourceSha256': asset.sha256, 'slotDigest': sha256_json(slot),
              'width': width, 'height': height, 'fps': fps}
    if target.exists():
        if not receipt_path.is_file():
            raise ContractError('local artifact has no verification receipt; reconcile before reuse')
        receipt = read_json(receipt_path)
        if receipt.get('recipe') != recipe or receipt.get('sha256') != sha256_file(target):
            raise ContractError('local artifact differs from its source-bound verification receipt')
        return verify_delivery(target, width=width, height=height, fps=fps,
                               frames=int(slot['timelineFrames']), require_audio=False)
    partial = target.with_name(f'.{target.stem}-{uuid4().hex}.partial.mp4')
    if asset.kind == 'image':
        command = image_command(executable('ffmpeg'), source, partial, width=width, height=height,
                                frames=int(slot['timelineFrames']), fps=fps, motion=slot['localMotion'],
                                role=slot['role'], outro=slot['shotId'] == 'shot-016')
    else:
        value = probe_streams(source)
        stream = next(s for s in value['streams'] if s['codec_type'] == 'video')
        validate_source_trim(source_duration=float(stream['duration']), source_in=asset.source_in_seconds,
                             frames=int(slot['timelineFrames']), fps=fps)
        command = [executable('ffmpeg'), '-nostdin', '-v', 'error', '-n', '-i', str(source),
                   '-vf', f"trim=start={asset.source_in_seconds}:duration=4,setpts=PTS-STARTPTS,"
                   f'scale={width}:{height}:force_original_aspect_ratio=decrease,'
                   f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps={fps},setsar=1',
                   '-frames:v', str(slot['timelineFrames']), *_encode(), str(partial)]
    run(command)
    evidence = verify_delivery(partial, width=width, height=height, fps=fps,
                                frames=int(slot['timelineFrames']), require_audio=False)
    if sha256_file(source) != asset.sha256:
        raise ContractError('source changed during local composition')
    atomic_write_json(receipt_path, {'recipe': recipe, 'sha256': evidence['sha256']})
    os.replace(partial, target)
    return evidence


def arrange_music(source: Path, edit: MusicEdit, target: Path, *, target_seconds: float) -> dict[str, Any]:
    evidence = probe_audio(source, ffprobe=executable('ffprobe'))
    if evidence.sha256 != edit.source_sha256:
        raise ContractError('original soundtrack changed; reselect and review the arrangement')
    validate_music(edit, source_duration=evidence.duration_seconds, target_seconds=target_seconds)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f'.music-{uuid4().hex}.partial.wav')
    graph = []
    for i, segment in enumerate(edit.segments):
        graph.append(f'[0:a]atrim=start={segment.source_in}:duration={segment.duration},asetpts=PTS-STARTPTS[a{i}]')
    parts = ''.join(f'[a{i}]' for i in range(len(edit.segments)))
    graph.append(f'{parts}concat=n={len(edit.segments)}:v=0:a=1,aresample=48000,'
                 f'afade=t=in:d={edit.fade_in_seconds},afade=t=out:st={target_seconds-edit.fade_out_seconds}'
                 f':d={edit.fade_out_seconds}[music]')
    run([executable('ffmpeg'), '-nostdin', '-v', 'error', '-n', '-i', str(source),
         '-filter_complex', ';'.join(graph), '-map', '[music]', '-ac', '2', '-ar', '48000',
         '-c:a', 'pcm_s24le', '-map_metadata', '-1', str(partial)])
    output = probe_audio(partial, ffprobe=executable('ffprobe'))
    if abs(output.duration_seconds-target_seconds) > 1/48000 or sha256_file(source) != edit.source_sha256:
        raise ContractError('music arrangement failed duration or original-byte verification')
    os.replace(partial, target)
    receipt = {'sourceSha256': edit.source_sha256, 'arrangement': edit.model_dump(by_alias=True),
               'arrangedSha256': output.sha256, 'durationSeconds': target_seconds, 'originalPreserved': True}
    atomic_write_json(target.with_suffix('.json'), receipt)
    return receipt


def finish_edit(plan: dict[str, Any], clips: dict[str, Path], *, music_source: Path,
                music_edit: MusicEdit, output_root: Path, wnp_run: str | None = None) -> dict[str, Any]:
    edit = plan['continuity']['hayaleEdit']
    fps, frames = int(edit['fps']), int(edit['totalFrames'])
    width, height = (1920, 1080) if plan['profile']['resolution'] == '1080p' else (1280, 720)
    if set(clips) != {s['shotId'] for s in edit['scenes']}:
        raise ContractError('all selected scenes are required before finishing')
    output_root.mkdir(parents=True, exist_ok=True)
    clip_hashes = {key: sha256_file(path) for key, path in clips.items()}
    if sha256_file(music_source) != music_edit.source_sha256:
        raise ContractError('original soundtrack changed before finishing')
    recipe = {'plan': sha256_json(plan), 'clips': clip_hashes, 'music': music_edit.model_dump(mode='json')}
    attempt_root = output_root / f'finish-{sha256_json(recipe)[:32]}'
    attempt_root.mkdir(exist_ok=True)
    recipe_path = attempt_root / 'recipe.json'
    if recipe_path.exists() and read_json(recipe_path) != recipe:
        raise ContractError('finishing recipe identity conflict')
    atomic_write_json(recipe_path, recipe)

    def retained(path: Path, receipt: Path, key: str = 'sha256') -> dict[str, Any] | None:
        if not path.exists():
            return None
        if not receipt.is_file():
            raise ContractError('retained finishing artifact has no verified receipt')
        value: dict[str, Any] = read_json(receipt)
        if value.get(key) != sha256_file(path):
            raise ContractError('retained finishing artifact changed; reconcile this local stage')
        return value
    segments: list[dict[str, Any]] = []
    tasks: list[dict[str, object]] = [
        {'task': f'scene-{i+1:02}', 'phase': 'queued'} for i in range(len(edit['scenes']))
    ]

    def notify(stage: str, event: str, manifest: Path | None = None) -> None:
        if wnp_run is not None:
            emit_lifecycle(run=wnp_run, source_id=f'hayale-finishing:{attempt_root.name}:{event}',
                           stage=stage, state='waiting' if manifest else 'running', tasks=tasks,
                           technical_verified=manifest is not None,
                           final_manifest_sha256=sha256_file(manifest) if manifest else None,
                           creative_approval='pending')

    notify('finishing', 'started')
    ffmpeg = executable('ffmpeg')
    for i, slot in enumerate(edit['scenes']):
        source = clips[slot['shotId']]
        source_in = float(slot['sourceInSeconds']) if slot['mode'] == 'gcp-first-frame' else 0.0
        target = attempt_root / f'segment-{i+1:03}.mp4'
        segment_receipt = target.with_suffix('.verification.json')
        segment_evidence = retained(target, segment_receipt)
        if segment_evidence is None:
            data = probe_streams(source)
            stream = next(s for s in data['streams'] if s['codec_type'] == 'video')
            validate_source_trim(source_duration=float(stream['duration']), source_in=source_in,
                                 frames=int(slot['timelineFrames']), fps=fps)
            candidate = attempt_root / f'.segment-{i+1:03}-{uuid4().hex}.partial.mp4'
            run([ffmpeg, '-nostdin', '-v', 'error', '-n', '-i', str(source), '-vf',
                 f"trim=start={source_in}:duration={int(slot['timelineFrames'])/fps},setpts=PTS-STARTPTS,fps={fps},"
                 f'scale={width}:{height},setsar=1', '-frames:v', str(slot['timelineFrames']),
                 *_encode(), str(candidate)])
            segment_evidence = verify_delivery(candidate, width=width, height=height, fps=fps,
                                              frames=int(slot['timelineFrames']), require_audio=False)
            atomic_write_json(segment_receipt, segment_evidence)
            os.replace(candidate, target)
        tasks[i] = {'task': f'scene-{i+1:02}', 'phase': 'final_verified', 'final_verified': True,
                    'final_manifest_sha256': sha256_file(segment_receipt),
                    'frames_verified': int(slot['timelineFrames']), 'expected_frames': int(slot['timelineFrames'])}
        notify('finishing', f'segment-{i+1:02}-verified')
        segments.append({'segmentId': f'event-{i+1:04}', 'shotId': slot['shotId'],
                         'chapterId': f'scene-{i+1:02}', 'clipPath': str(target),
                         'sourceInFrames': 0, 'durationFrames': slot['timelineFrames'],
                         'timelineStartFrames': slot['startFrame'], 'treatment': 'verified cut',
                         'title': f'Scene {i+1:02}'})
    music = attempt_root / 'music-64s.wav'
    notify('music-mux', 'arrangement-started')
    music_receipt = retained(music, music.with_suffix('.json'), 'arrangedSha256')
    if music_receipt is None:
        music_receipt = arrange_music(music_source, music_edit, music, target_seconds=frames/fps)
    notify('music-mux', 'arrangement-verified')
    concat = attempt_root / 'segments.ffconcat'
    concat.write_text('ffconcat version 1.0\n' + ''.join(
        f"file 'segment-{i+1:03}.mp4'\n" for i in range(len(segments))), encoding='ascii')
    final = attempt_root / 'hayale-64s.mp4'
    media_receipt = attempt_root / 'media-verification.json'
    evidence = retained(final, media_receipt)
    if evidence is None:
        mux_receipt = attempt_root / 'mux-completed.json'
        mux = read_json(mux_receipt) if mux_receipt.exists() else None
        if mux:
            partial = attempt_root / mux['candidate']
            if partial.parent != attempt_root or retained(partial, mux_receipt) is None:
                raise ContractError('completed mux candidate changed; reconcile the retained local stage')
        else:
            partial = attempt_root / f'.delivery-{uuid4().hex}.partial.mp4'
            notify('music-mux', 'mux-started')
            run([ffmpeg, '-nostdin', '-v', 'error', '-n', '-f', 'concat', '-safe', '1', '-i', str(concat),
                 '-i', str(music), '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy', '-c:a', 'aac',
                 '-b:a', '192k', '-ar', '48000', '-ac', '2', '-map_metadata', '-1', '-movflags', '+faststart', str(partial)])
            atomic_write_json(mux_receipt, {'candidate': partial.name, 'sha256': sha256_file(partial)})
        notify('verification', 'final-media-check')
        evidence = verify_delivery(partial, width=width, height=height, fps=fps, frames=frames, require_audio=True)
        atomic_write_json(media_receipt, evidence)
        os.replace(partial, final)
    timeline: dict[str, Any] = {'schemaVersion': '1.0.0', 'title': plan['title'],
        'projectId': plan['projectId'], 'editorial': {'exportFiles': {
            'fcpxml': 'hayale-64s.fcpxml', 'fcp7': 'hayale-64s.xml', 'edl': 'hayale-64s.edl',
            'preview1080p': 'hayale-64s.mp4', 'preview4k': 'hayale-64s.mp4'}},
        'timeline': {'fps': fps, 'width': width, 'height': height,
          'durationFrames': frames, 'audioPath': str(music)}, 'segments': segments, 'markers': []}
    atomic_write_json(attempt_root / 'resolved-timeline.json', timeline)
    notify('export', 'interchange-started')
    export_fcpxml(timeline, attempt_root / 'hayale-64s.fcpxml')
    export_fcp7_xml(timeline, attempt_root / 'hayale-64s.xml')
    export_edl(timeline, attempt_root / 'hayale-64s.edl')
    result = {**evidence, 'planDigest': plan['planDigest'], 'music': music_receipt,
              'sceneVerification': tasks,
              'deliveryPath': str(final), 'exportRoot': str(attempt_root),
              'sourceClipHashes': clip_hashes}
    atomic_write_json(attempt_root / 'verification.json', result)
    atomic_write_json(attempt_root / 'verification-report.json', result)
    notify('export', 'delivery-verified', attempt_root / 'verification.json')
    return result
