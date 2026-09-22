"""Cloud-only Hayale media primitives; standalone stdlib, Pillow and FFmpeg.

The Batch worker verifies its attached metadata identity before entering
``cloud_execution``. These functions never infer permission from local paths or
silently run production transforms on a workstation. Tests replace the private
guard explicitly and label every resulting receipt as synthetic local evidence.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import math
import os
import platform
import re
import shutil
import signal
import statistics
import subprocess
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from fractions import Fraction
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from PIL import Image, ImageFilter, ImageOps

WIDTH, HEIGHT, FPS, SCENE_FRAMES, TOTAL_FRAMES = 1920, 1080, 24, 96, 1536
SAMPLE_RATE = 48000
_SHA256 = re.compile(r'^[a-f0-9]{64}$')
_CLOUD: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar('hayale_cloud_media', default=None)
_EXPECTED_SEGMENTS = ((0.0, 43.24), (38.76, 4.48), (43.24, 16.28))


class MediaError(ValueError):
    """A safe, actionable media-contract failure; no source is modified."""


@contextmanager
def cloud_execution(*, project_id: str, job_uid: str, task_index: str,
                    identity_verified: bool = False) -> Iterator[None]:
    """Accept only the verified attached identity supplied by the Batch worker."""
    if (platform.system() != 'Linux' or identity_verified is not True
        or project_id != 'nexus-core-481713' or not job_uid or not task_index.isdigit()
        or os.environ.get('BATCH_JOB_UID') != job_uid
        or os.environ.get('BATCH_TASK_INDEX') != task_index):
        raise MediaError('verified GCP Batch cloud execution context is required')
    value = {'provider': 'gcp-batch', 'projectId': project_id, 'jobUid': job_uid,
             'taskIndex': task_index, 'identityVerified': True, 'verified': True, 'production': True}
    token = _CLOUD.set(value)
    try:
        yield
    finally:
        _CLOUD.reset(token)


def _require_cloud_execution() -> dict[str, Any]:
    value = _CLOUD.get()
    if value is None:
        raise MediaError('verified GCP Batch cloud execution context is required')
    return dict(value)


def _require_retained_execution(receipt: dict[str, Any], execution: dict[str, Any]) -> None:
    if not execution.get('production'):
        return
    prior = receipt.get('cloudExecution', {})
    if (not isinstance(prior, dict) or prior.get('provider') != 'gcp-batch'
        or prior.get('production') is not True or prior.get('identityVerified') is not True
        or prior.get('projectId') != execution.get('projectId') or not prior.get('jobUid')):
        raise MediaError('retained media lacks verified production cloud execution provenance')


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.name}-{uuid4().hex}.tmp')
    with temporary.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _read_json(path: Path) -> dict[str, Any]:
    if path.stat().st_size > 4_000_000:
        raise MediaError('retained media receipt exceeds the supported bound')
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise MediaError('retained media receipt is invalid')
    return value


def executable(name: str) -> str:
    value = os.environ.get(f'HAYALE_{name.upper()}') or shutil.which(name)
    if not value or not Path(value).is_file():
        raise MediaError(f'{name} must exist in the cloud worker image')
    return str(Path(value).resolve())


def _run(command: list[str], *, timeout: int = 900, stdout_limit: int = 4_000_000) -> bytes:
    """Bound time and both drained pipes, and kill the isolated process group."""
    buffers = [bytearray(), bytearray()]
    exceeded = threading.Event()
    options: dict[str, Any] = {'start_new_session': True} if os.name != 'nt' else {
        'creationflags': subprocess.CREATE_NO_WINDOW}
    with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, shell=False, close_fds=True, **options) as process:
        def stop() -> None:
            try:
                if os.name != 'nt':
                    kill_group = getattr(os, 'killpg', None)
                    if kill_group is not None:
                        kill_group(process.pid, getattr(signal, 'SIGKILL', 9))
                    else:
                        process.kill()
                else:
                    process.kill()
            except ProcessLookupError:
                pass

        def drain(pipe: Any, index: int, limit: int) -> None:
            while chunk := pipe.read(65536):
                remaining = max(0, limit - len(buffers[index]))
                buffers[index].extend(chunk[:remaining])
                if len(chunk) > remaining:
                    exceeded.set()
                    stop()

        readers = [threading.Thread(target=drain, args=(process.stdout, 0, stdout_limit), daemon=True),
                   threading.Thread(target=drain, args=(process.stderr, 1, 65536), daemon=True)]
        for thread in readers:
            thread.start()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            stop()
            process.wait(timeout=10)
            raise MediaError('cloud media command exceeded its deadline; original sources are retained') from exc
        finally:
            for thread in readers:
                thread.join(timeout=10)
        if exceeded.is_set() or process.returncode:
            raise MediaError('cloud media command failed or exceeded its output bound; original sources are retained')
    return bytes(buffers[0])


def prepare_seed(source: Path, target: Path, role: str, recipe: dict[str, Any]) -> dict[str, Any]:
    execution = _require_cloud_execution()
    if not re.fullmatch(r'logo|(?:customer|exterior|product|vibe|tour)-0[123]', role):
        raise MediaError('seed requires a defined Hayale source role')
    if recipe.get('targetWidth', WIDTH) != WIDTH or recipe.get('targetHeight', HEIGHT) != HEIGHT:
        raise MediaError('cloud seed dimensions are fixed at 1920x1080')
    expected = recipe.get('sourceSha256')
    if not isinstance(expected, str) or not _SHA256.fullmatch(expected) or sha256_file(source) != expected:
        raise MediaError('original seed source does not match its approved hash')
    identity = _digest({'sourceSha256': expected, 'role': role, 'recipe': recipe})
    receipt_path = target.with_suffix('.verification.json')
    if target.exists():
        try:
            retained = _read_json(receipt_path)
            _require_retained_execution(retained, execution)
            if retained.get('recipeSha256') != identity or retained.get('sha256') != sha256_file(target):
                raise MediaError('retained seed changed or belongs to another recipe')
            with Image.open(target) as check:
                check.load()
                if check.size != (WIDTH, HEIGHT):
                    raise MediaError('retained seed has incorrect dimensions')
            return retained
        except (OSError, ValueError) as exc:
            raise MediaError('retained seed or its receipt is missing or changed') from exc
    with Image.open(source) as opened:
        opened.load()
        image = ImageOps.exif_transpose(opened).convert('RGBA')
    original_size = image.size
    crop = recipe.get('cropBox', [0, 0, *image.size])
    if 'cropNormalized' in recipe:
        normalized = recipe['cropNormalized']
        if ('cropBox' in recipe or not isinstance(normalized, list) or len(normalized) != 4
            or not all(isinstance(value, (int, float)) and not isinstance(value, bool)
                       and math.isfinite(value) for value in normalized)
            or not (0 <= normalized[0] < normalized[2] <= 1 and 0 <= normalized[1] < normalized[3] <= 1)):
            raise MediaError('normalized seed crop must be one valid source-relative rectangle')
        crop = [round(normalized[0] * image.width), round(normalized[1] * image.height),
                round(normalized[2] * image.width), round(normalized[3] * image.height)]
    if (not isinstance(crop, list) or len(crop) != 4
        or not all(isinstance(value, int) and not isinstance(value, bool) for value in crop)
        or not (0 <= crop[0] < crop[2] <= image.width and 0 <= crop[1] < crop[3] <= image.height)):
        raise MediaError('seed crop must be a valid source-pixel rectangle')
    if crop != [0, 0, *image.size]:
        if role == 'logo' or role.startswith('customer'):
            raise MediaError('protected logo and customer seeds must preserve the complete source composition')
        if recipe.get('subjectSafe') is not True:
            raise MediaError('a crop requires explicit subject-safe composition review')
        image = image.crop(tuple(crop))
    if role == 'logo':
        foreground = ImageOps.contain(image, (WIDTH, HEIGHT), Image.Resampling.LANCZOS) if (
            image.width > WIDTH or image.height > HEIGHT) else image
        corners = [cast(tuple[int, int, int, int], image.getpixel(point)) for point in ((0, 0), (image.width - 1, 0),
                   (0, image.height - 1), (image.width - 1, image.height - 1))]
        visible = [pixel for pixel in corners if pixel[3] > 0]
        sampled = tuple(round(statistics.median(pixel[channel] for pixel in visible)) for channel in range(3)) if visible else (16, 37, 28)
        background = Image.new('RGBA', (WIDTH, HEIGHT), recipe.get('backgroundColor', sampled))
        method = 'original-logo-uniform-contain-on-source-edge-matte'
    else:
        # Full portrait/person content remains intact; only peripheral background
        # is extended. No geometry synthesis or face reconstruction occurs.
        foreground = ImageOps.contain(image, (WIDTH, HEIGHT), Image.Resampling.LANCZOS)
        background = ImageOps.fit(image.convert('RGB'), (WIDTH, HEIGHT), Image.Resampling.LANCZOS)
        background = background.filter(ImageFilter.GaussianBlur(48)).convert('RGBA')
        method = 'full-composition-with-soft-background-extension'
    offset = ((WIDTH - foreground.width) // 2, (HEIGHT - foreground.height) // 2)
    background.alpha_composite(foreground, offset)
    result = background.convert('RGB')
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f'.{target.stem}-{uuid4().hex}.partial.png')
    result.save(temporary, format='PNG', optimize=True)
    if sha256_file(source) != expected:
        raise MediaError('original seed source changed during cloud preparation')
    receipt: dict[str, Any] = {'schemaVersion': 'hayale-gcp-seed/1', 'path': str(target), 'sha256': sha256_file(temporary),
        'width': WIDTH, 'height': HEIGHT, 'sourceSha256': expected, 'role': role,
        'recipe': recipe, 'recipeSha256': identity, 'cloudExecution': execution,
        'transform': {'method': method, 'sourceSize': list(original_size), 'cropBox': crop,
            'foregroundSize': list(foreground.size), 'offset': list(offset),
            'scaleX': foreground.width / image.width, 'scaleY': foreground.height / image.height,
            'logoResampled': foreground.size != image.size if role == 'logo' else None}}
    # A single isotropic scale is used. Pixel rounding may differ by <1 pixel.
    if abs(receipt['transform']['scaleX'] - receipt['transform']['scaleY']) > 1 / min(image.size):
        raise MediaError('seed preparation would geometrically stretch the source')
    _write_json(receipt_path, receipt)
    os.replace(temporary, target)
    return receipt


def _probe(path: Path) -> dict[str, Any]:
    value = json.loads(_run([executable('ffprobe'), '-v', 'error', '-count_frames', '-show_streams',
                            '-show_format', '-of', 'json', str(path)]))
    if not isinstance(value, dict):
        raise MediaError('cloud media probe returned invalid data')
    return value


def _validate_pts(timestamps: list[int], *, time_base: str, frames: int) -> None:
    if len(timestamps) != frames:
        raise MediaError('decoded frame count differs from the frame timeline')
    base = Fraction(time_base)
    if any(Fraction(timestamp) * base != Fraction(index, FPS) for index, timestamp in enumerate(timestamps)):
        raise MediaError('presentation timestamps violate the exact zero-origin CFR timeline')


def _verify(path: Path, *, frames: int, audio_required: bool) -> dict[str, Any]:
    execution = _require_cloud_execution()
    probe = _probe(path)
    video = [stream for stream in probe['streams'] if stream.get('codec_type') == 'video']
    audio = [stream for stream in probe['streams'] if stream.get('codec_type') == 'audio']
    if len(video) != 1 or len(audio) != int(audio_required):
        raise MediaError('media must contain exactly the intended video and audio streams')
    stream = video[0]
    if (int(stream['width']) != WIDTH or int(stream['height']) != HEIGHT
        or Fraction(stream['avg_frame_rate']) != FPS or Fraction(stream['r_frame_rate']) != FPS
        or int(stream.get('nb_read_frames', -1)) != frames
        or Fraction(stream.get('sample_aspect_ratio', '1:1').replace(':', '/')) != 1
        or Fraction(stream.get('start_time', '0')) != 0
        or abs(float(stream['duration']) - frames / FPS) > 0.000001):
        raise MediaError('native 1080p dimensions, exact frames, duration or 24 fps contract failed')
    audio_offset: float | None = None
    if audio_required:
        sound = audio[0]
        audio_offset = float(sound.get('start_time', 'nan'))
        if (stream.get('codec_name') != 'h264' or sound.get('codec_name') != 'aac'
            or audio_offset != 0 or int(sound['sample_rate']) != SAMPLE_RATE or int(sound['channels']) != 2
            or abs(float(sound['duration']) - frames / FPS) > 1 / SAMPLE_RATE):
            raise MediaError('final H.264/AAC audio codec, exact zero offset or audio duration contract failed')
    frame_data = json.loads(_run([executable('ffprobe'), '-v', 'error', '-select_streams', 'v:0',
        '-show_entries', 'frame=best_effort_timestamp', '-of', 'json', str(path)]))
    _validate_pts([int(frame['best_effort_timestamp']) for frame in frame_data['frames']],
                  time_base=stream['time_base'], frames=frames)
    _run([executable('ffmpeg'), '-nostdin', '-v', 'error', '-xerror', '-i', str(path),
          '-map', '0:v:0', *(['-map', '0:a:0'] if audio_required else []), '-f', 'null', '-'])
    return {'path': str(path), 'sha256': sha256_file(path), 'width': WIDTH, 'height': HEIGHT,
        'fps': FPS, 'frames': frames, 'durationSeconds': frames / FPS, 'codec': stream['codec_name'],
        'audioCodec': audio[0]['codec_name'] if audio else None, 'audioPresent': bool(audio),
        'audioStartOffsetSeconds': audio_offset, 'ptsVerified': True, 'fullDecodeVerified': True,
        'technicalVerified': True, 'creativeApproval': 'pending', 'cloudExecution': execution}


def verify_scene(path: Path) -> dict[str, Any]:
    _require_cloud_execution()
    return _verify(path, frames=SCENE_FRAMES, audio_required=False)


def _arrangement_samples(arrangement: dict[str, Any], *, source_seconds: float) -> list[dict[str, int]]:
    if not _SHA256.fullmatch(str(arrangement.get('sourceSha256', ''))):
        raise MediaError('original music requires its approved source SHA-256')
    segments = arrangement.get('segments')
    if not isinstance(segments, list) or len(segments) != len(_EXPECTED_SEGMENTS):
        raise MediaError('music requires the approved three explicit source segments')
    if arrangement.get('joinFadeSeconds') != .005:
        raise MediaError('music joins require the approved five-millisecond length-preserving ramps')
    for name in ('fadeInSeconds', 'fadeOutSeconds'):
        value = arrangement.get(name, 0)
        if not isinstance(value, (float, int)) or not math.isfinite(value) or not 0 <= value <= 4:
            raise MediaError('music fades must be explicit finite values within four seconds')
    result = []
    for segment, expected in zip(segments, _EXPECTED_SEGMENTS, strict=True):
        if not isinstance(segment, dict):
            raise MediaError('music segment must contain explicit source time and duration')
        if (('sourceIn' in segment and 'sourceStartSeconds' in segment
             and segment['sourceIn'] != segment['sourceStartSeconds'])
            or ('duration' in segment and 'durationSeconds' in segment
                and segment['duration'] != segment['durationSeconds'])):
            raise MediaError('music segment has conflicting time aliases')
        start = segment.get('sourceStartSeconds', segment.get('sourceIn'))
        duration = segment.get('durationSeconds', segment.get('duration'))
        if (not isinstance(start, (int, float)) or not isinstance(duration, (int, float))
            or not math.isfinite(start) or not math.isfinite(duration)
            or abs(start - expected[0]) > 1e-8 or abs(duration - expected[1]) > 1e-8
            or start + duration > source_seconds + 1 / SAMPLE_RATE):
            raise MediaError('music segments differ from the approved arrangement or exceed the original source')
        result.append({'sourceInSamples': round(start * SAMPLE_RATE), 'durationSamples': round(duration * SAMPLE_RATE)})
    if sum(segment['durationSamples'] for segment in result) != 64 * SAMPLE_RATE:
        raise MediaError('music arrangement does not contain exactly 64 seconds of source samples')
    return result


def _arrange_audio(source: Path, target: Path, arrangement: dict[str, Any]) -> dict[str, Any]:
    original_hash = sha256_file(source)
    if original_hash != arrangement.get('sourceSha256'):
        raise MediaError('original soundtrack bytes differ from the approved source hash')
    streams = [stream for stream in _probe(source)['streams'] if stream.get('codec_type') == 'audio']
    if len(streams) != 1:
        raise MediaError('original soundtrack must have exactly one audio stream')
    samples = _arrangement_samples(arrangement, source_seconds=float(streams[0]['duration']))
    pieces = ['[0:a:0]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,asplit=3[a0][a1][a2]']
    for index, segment in enumerate(samples):
        start, count = segment['sourceInSamples'], segment['durationSamples']
        filters = [f'atrim=start_sample={start}:end_sample={start + count}', 'asetpts=N/SR/TB']
        fade_in = float(arrangement.get('fadeInSeconds', 0)) if index == 0 else .005
        fade_out = float(arrangement.get('fadeOutSeconds', 0)) if index == 2 else .005
        if fade_in:
            filters.append(f'afade=t=in:ss=0:ns={round(fade_in * SAMPLE_RATE)}')
        if fade_out:
            ramp = round(fade_out * SAMPLE_RATE)
            filters.append(f'afade=t=out:ss={count - ramp}:ns={ramp}')
        pieces.append(f'[a{index}]' + ','.join(filters) + f'[s{index}]')
    pieces.append('[s0][s1][s2]concat=n=3:v=0:a=1[out]')
    _run([executable('ffmpeg'), '-nostdin', '-v', 'error', '-n', '-i', str(source),
          '-filter_complex', ';'.join(pieces), '-map', '[out]', '-ar', '48000', '-ac', '2',
          '-c:a', 'pcm_s24le', '-map_metadata', '-1', str(target)])
    sound = next(stream for stream in _probe(target)['streams'] if stream['codec_type'] == 'audio')
    if (abs(float(sound['duration']) - 64) > 1 / SAMPLE_RATE or int(sound['sample_rate']) != SAMPLE_RATE
        or int(sound['channels']) != 2 or sha256_file(source) != original_hash):
        raise MediaError('arranged audio duration or original-byte preservation failed')
    return {'sourceSha256': original_hash, 'arrangedSha256': sha256_file(target), 'durationSeconds': 64,
        'arrangement': arrangement, 'sampleSegments': samples, 'originalPreserved': True,
        'path': str(target), 'cloudExecution': _require_cloud_execution()}


def _sources(clips: list[Path], provenance: dict[str, Any]) -> list[dict[str, Any]]:
    if len(clips) != 16:
        raise MediaError('exactly sixteen ordered provider clips are required')
    hashes = [sha256_file(path) for path in clips]
    if len(set(hashes)) != 16:
        raise MediaError('duplicate provider output across scene slots is prohibited, including both logo scenes')
    records = provenance.get('sceneRecords')
    if (not isinstance(provenance.get('runId'), str) or not provenance['runId']
        or not _SHA256.fullmatch(str(provenance.get('planDigest', '')))
        or not isinstance(records, list) or len(records) != 16):
        raise MediaError('cloud finishing requires the canonical run, plan and sixteen scene records')
    sources, operations = [], set()
    for index, (record, digest) in enumerate(zip(records, hashes, strict=True), 1):
        if (record.get('sceneId') != f'scene-{index:03}' or record.get('clipSha256') != digest
            or record.get('provider') != 'veo' or record.get('state') != 'verified'
            or not _SHA256.fullmatch(str(record.get('sourceSha256', '')))
            or not _SHA256.fullmatch(str(record.get('seedSha256', '')))
            or not re.fullmatch(r'projects/nexus-core-481713/locations/us-central1/publishers/google/models/veo-3\.1(?:-fast)?-generate-001/operations/[^\s/]+', str(record.get('operationName', '')))):
            raise MediaError('scene order, original source, prepared seed or accepted provider identity differs')
        operation = hashlib.sha256(record['operationName'].encode()).hexdigest()
        if operation in operations:
            raise MediaError('one accepted provider operation cannot stand for multiple generated scenes')
        operations.add(operation)
        sources.append({key: record[key] for key in ('sceneId', 'sourceSha256', 'seedSha256', 'clipSha256')})
        sources[-1].update(operationSha256=operation, provider='veo', startFrame=(index - 1) * SCENE_FRAMES,
                           frames=SCENE_FRAMES)
    if sources[0]['sourceSha256'] != sources[-1]['sourceSha256'] or len({item['sourceSha256'] for item in sources}) != 15:
        raise MediaError('source mapping must preserve one logo used twice and fourteen distinct real photos')
    return sources


def finish(clips: list[Path], audio: Path, output: Path, arrangement: dict[str, Any],
           provenance: dict[str, Any], *, progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    execution = _require_cloud_execution()

    def stage(name: str) -> None:
        if progress is not None:
            progress(name)

    sources = _sources(clips, provenance)
    if sha256_file(audio) != arrangement.get('sourceSha256'):
        raise MediaError('original soundtrack bytes differ from the approved source hash')
    identity = _digest({'sources': sources, 'arrangement': arrangement, 'runId': provenance['runId'],
                        'planDigest': provenance['planDigest']})
    verification = output.parent / 'Hayale-Flowers-Promo-verification.json'
    report = output.parent / 'Hayale-Flowers-Promo-delivery-report.md'
    if output.exists():
        saved = _read_json(verification)
        _require_retained_execution(saved, execution)
        if saved.get('finishingRecipeSha256') != identity or saved.get('sha256') != sha256_file(output):
            raise MediaError('retained final differs from its cloud verification recipe; prior delivery is preserved')
        stage('final_verification')
        _verify(output, frames=TOTAL_FRAMES, audio_required=True)
        if not report.is_file():
            _delivery_report(report, saved)
        return saved
    stage('scene_verification')
    for path in clips:
        verify_scene(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    work = output.parent / 'finishing' / identity
    work.mkdir(parents=True, exist_ok=True)
    arranged = work / 'arranged-audio.wav'
    audio_receipt = work / 'arranged-audio.json'
    stage('audio_preparation')
    if arranged.exists():
        music = _read_json(audio_receipt)
        _require_retained_execution(music, execution)
        if music.get('arrangedSha256') != sha256_file(arranged) or music.get('arrangement') != arrangement:
            raise MediaError('retained arranged audio differs from its approved recipe')
    else:
        candidate_audio = work / f'arranged-{uuid4().hex}.partial.wav'
        music = _arrange_audio(audio, candidate_audio, arrangement)
        music['path'] = str(arranged)
        _write_json(audio_receipt, music)
        os.replace(candidate_audio, arranged)
    stage('finishing')
    concat = work / 'scenes.ffconcat'
    lines = ['ffconcat version 1.0']
    for path in clips:
        name = path.resolve().as_posix()
        if any(char in name for char in '\r\n\0'):
            raise MediaError('cloud clip path contains an unsupported control character')
        lines.append("file '" + name.replace("'", "'\\''") + "'")
    concat.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    encoded_receipt = work / 'encoded-candidate.json'
    if encoded_receipt.exists():
        retained = _read_json(encoded_receipt)
        _require_retained_execution(retained, execution)
        candidate = work / retained['candidate']
        if (candidate.resolve().parent != work.resolve() or retained.get('finishingRecipeSha256') != identity
            or retained.get('sha256') != sha256_file(candidate)):
            raise MediaError('retained encoded candidate differs from the cloud finishing recipe')
    else:
        candidate = work / f'delivery-{uuid4().hex}.partial.mp4'
        stage('mux')
        _run([executable('ffmpeg'), '-nostdin', '-v', 'error', '-n', '-f', 'concat', '-safe', '0', '-i', str(concat),
              '-i', str(arranged), '-map', '0:v:0', '-map', '1:a:0', '-vf', 'setpts=N/(24*TB),setsar=1',
              '-frames:v', '1536', '-r', '24', '-fps_mode', 'cfr', '-c:v', 'libx264', '-preset', 'medium',
              '-crf', '18', '-pix_fmt', 'yuv420p', '-threads', '2', '-video_track_timescale', '12288',
              '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-ac', '2', '-map_metadata', '-1',
              '-movflags', '+faststart', str(candidate)])
        _write_json(encoded_receipt, {'candidate': candidate.name, 'sha256': sha256_file(candidate),
            'finishingRecipeSha256': identity, 'cloudExecution': execution})
    stage('final_verification')
    result = _verify(candidate, frames=TOTAL_FRAMES, audio_required=True)
    if [sha256_file(path) for path in clips] != [source['clipSha256'] for source in sources] or sha256_file(audio) != arrangement['sourceSha256']:
        raise MediaError('approved source bytes changed during cloud finishing')
    result.update(schemaVersion='hayale-gcp-verification/1', runId=provenance['runId'],
        planDigest=provenance['planDigest'], finishingRecipeSha256=identity, sources=sources,
        sceneCount=16, arrangement=music, cloudExecution=execution, path=str(output),
        deliveryPath=str(output), verificationPath=str(verification), reportPath=str(report))
    _write_json(verification, result)
    os.replace(candidate, output)
    if sha256_file(output) != result['sha256']:
        raise MediaError('published cloud delivery hash differs from verification')
    _delivery_report(report, result)
    return result


def _delivery_report(path: Path, result: dict[str, Any]) -> None:
    execution = result['cloudExecution']
    text = ('# Hayale Flowers cloud delivery\n\n'
        'Technical verification passed: 1920×1080, constant 24 fps, 1,536 frames, exactly 64 seconds.\n\n'
        'Sixteen distinct, ordered provider clips each contribute 96 frames. H.264 video and stereo 48 kHz AAC audio '
        'were fully decoded; every presentation timestamp and the zero audio start offset passed verification.\n\n'
        f"Final SHA-256: `{result['sha256']}`\n\n"
        f"Original soundtrack SHA-256: `{result['arrangement']['sourceSha256']}`\n\n"
        f"Arranged soundtrack SHA-256: `{result['arrangement']['arrangedSha256']}`\n\n"
        f"Execution: `{execution['provider']}`; production: `{execution.get('production', False)}`.\n\n"
        'The original soundtrack bytes were preserved. The explicit source segments and five-millisecond join ramps '
        'are recorded in the verification JSON. Creative acceptance remains pending human review.\n')
    temporary = path.with_name(f'.{path.name}-{uuid4().hex}.tmp')
    temporary.write_text(text, encoding='utf-8')
    os.replace(temporary, path)
