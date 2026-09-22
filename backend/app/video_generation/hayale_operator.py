"""Local operator boundary around the canonical video controller and store."""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.mission_control.config import MissionControlConfig
from app.mission_control.processes import process_is_alive, process_started_at
from app.mission_control.store import MissionControlStore
from app.wnp import emit_lifecycle, observe_worker

from .audio import probe_audio
from .authorization import BatchAuthorization, authorization_phrase
from .contracts import ContractError
from .hayale_finishing import executable, finish_edit, probe_streams, render_local_scene, verify_delivery
from .hayale_plan import (
    AssetSelection,
    MusicEdit,
    MusicSegment,
    compile_hayale,
    load_scenario,
    validate_music,
)
from .jsonio import atomic_write_json, read_json, sha256_file, sha256_json
from .mission_controller import VideoGenerationController
from .mission_models import VideoAuthorizationRequest, VideoJobRecord, VideoJobState, VideoShotState, utc_now


def repository() -> Path:
    return Path(__file__).resolve().parents[3]


def private_root() -> Path:
    return MissionControlConfig.from_repository(repository()).state_root.parent / 'hayale-16x4'


def selection_path() -> Path:
    return private_root() / 'selection.json'


def selections() -> dict[str, AssetSelection]:
    if not selection_path().exists():
        return {}
    value = read_json(selection_path())
    return {key: AssetSelection.model_validate(item) for key, item in value.items()}


async def _notify(sequence: int) -> None:
    return None


def controller() -> VideoGenerationController:
    config = MissionControlConfig.from_repository(repository())
    return VideoGenerationController(repository_root=config.repository_root, state_root=config.state_root,
        store=MissionControlStore(config.database_path), notify_event=_notify,
        ffmpeg_path=lambda: Path(executable('ffmpeg')), ffprobe_path=lambda: Path(executable('ffprobe')))


def check() -> dict[str, Any]:
    scenario = load_scenario(repository())
    assets = selections()
    invalid: list[str] = []
    for key, selection in assets.items():
        try:
            selection.verify()
        except ContractError:
            invalid.append(key)
    dependencies: dict[str, bool] = {}
    for name in ('ffmpeg', 'ffprobe'):
        try:
            executable(name)
            dependencies[name] = True
        except ContractError:
            dependencies[name] = False
    from PIL import Image
    dependencies['pngJpegDecoders'] = bool(Image.registered_extensions().get('.png') and Image.registered_extensions().get('.jpg'))
    dependencies['stableRuntime'] = '.cache/codex-runtimes' not in Path(sys.base_prefix).as_posix().lower()
    return {'status': 'inputs_required' if scenario.missing_inputs(assets) or invalid else 'prepared_inputs',
            'sceneCount': len(scenario.scenes), 'durationSeconds': scenario.duration_seconds,
            'frames': scenario.total_frames, 'missingScenes': scenario.missing_inputs(assets),
            'invalidScenes': invalid, 'musicArrangementPresent': (private_root() / 'music.json').is_file(),
            'dependencies': dependencies, 'networkContacted': False, 'generationSubmitted': False}


def select_asset(args: argparse.Namespace) -> dict[str, Any]:
    scene = next((s for s in load_scenario(repository()).scenes if s.id == args.scene), None)
    if scene is None or not args.asset:
        raise ContractError('Select requires a valid -Scene shot-NNN and -Asset path')
    if scene.role in {'logo', 'customer'} and (args.cloud_upload or args.retained_video):
        raise ContractError('logo and customer slots require local image composition')
    path = Path(args.asset).expanduser().resolve(strict=True)
    duration, fps = None, None
    if args.retained_video:
        evidence = probe_streams(path)
        stream = next(s for s in evidence['streams'] if s['codec_type'] == 'video')
        from fractions import Fraction
        duration, fps = float(stream['duration']), float(Fraction(stream['avg_frame_rate']))
    asset = AssetSelection(path=str(path), sha256=sha256_file(path), promotional_use=args.promotional_use,
                           cloud_upload=args.cloud_upload, kind='video' if args.retained_video else 'image',
                           source_in_seconds=args.source_in, source_duration_seconds=duration, source_fps=fps)
    asset.verify()
    values = selections()
    values[scene.id] = asset
    atomic_write_json(selection_path(), {key: value.model_dump(by_alias=True) for key, value in values.items()})
    return {'selectedScene': scene.id, 'sha256': asset.sha256, 'cloudUploadApproved': asset.cloud_upload,
            'networkContacted': False}


def select_music(args: argparse.Namespace) -> dict[str, Any]:
    if not args.asset or not args.music_segment:
        raise ContractError('Music requires -Asset and explicit -MusicSegment start:duration values totaling 64 seconds')
    path = Path(args.asset).resolve(strict=True)
    source = probe_audio(path, ffprobe=executable('ffprobe'))
    segments = []
    for item in args.music_segment:
        start, duration = item.split(':')
        segments.append(MusicSegment(source_in=float(start), duration=float(duration)))
    edit = MusicEdit(source_sha256=source.sha256, approved=args.approve_arrangement, segments=tuple(segments))
    validate_music(edit, source_duration=source.duration_seconds, target_seconds=64)
    atomic_write_json(private_root() / 'music.json', {'source': str(path), 'edit': edit.model_dump(by_alias=True)})
    return {'musicArrangementSeconds': 64, 'sourcePreserved': True, 'sourceSha256': source.sha256}


async def prepare(args: argparse.Namespace, service: VideoGenerationController) -> dict[str, Any]:
    scenario, assets = load_scenario(repository()), selections()
    if args.profile == 'still-animatic':
        # No provider can be constructed for local-only plans. These are explicit
        # local placeholders, never an inferred user's cloud project/account.
        project_id, bucket = 'local-preview', 'local-preview-bucket'
    else:
        from .gcp_auth import load_binding
        binding = load_binding()
        if binding.model_id != 'veo-3.1-fast-generate-001':
            raise ContractError('selected cloud model differs from the Hayale Fast profile; reconcile the explicit binding')
        project_id, bucket = binding.project_id, binding.bucket
    plan = compile_hayale(scenario, assets, profile_id=args.profile, bucket=bucket)
    paths = {s.shot_id: Path(assets[s.shot_id].path) for s in plan.shots if s.first_frame_reference}
    view = await service.create_prepared_plan(plan, gcp_project_id=project_id, gcs_bucket=bucket, reference_paths=paths)
    job = service._record(view.job_id)
    root = service._job_root(job)
    snapshot = root / 'hayale-selection.json'
    selected = {key: asset.model_dump(by_alias=True) for key, asset in assets.items()}
    if snapshot.exists() and read_json(snapshot) != selected:
        raise ContractError('existing prepared selection differs; reconcile the saved plan')
    atomic_write_json(snapshot, selected)
    emit_lifecycle(run=job.id, source_id='hayale-plan:'+plan.plan_digest, stage='planning',
                   profile=args.profile, state='waiting')
    width, height = (1920, 1080) if plan.profile.resolution == '1080p' else (1280, 720)
    for slot in plan.continuity['hayaleEdit']['scenes']:
        if slot['mode'] != 'local-composition':
            continue
        target = root / 'local-composition' / (slot['shotId'] + '.mp4')
        evidence = await asyncio.to_thread(render_local_scene, assets[slot['shotId']], slot, target,
                                           width=width, height=height)
        await service.register_local_clip(job.id, slot['shotId'], target, expected_sha256=evidence['sha256'])
    return summary(service._record(job.id))


def summary(job: VideoJobRecord) -> dict[str, Any]:
    edit = job.plan['continuity']['hayaleEdit']
    root = private_root() / 'workers' / job.id
    worker = read_json(root / 'worker.json') if (root / 'worker.json').is_file() else {}
    alive = bool(worker) and process_is_alive(worker.get('pid'))
    start = process_started_at(worker.get('pid')) if alive else None
    alive = alive and start is not None and start.isoformat() == worker.get('processStartedAt')
    return {'jobId': job.id, 'state': job.state.value, 'planDigest': job.plan_digest,
            'profile': edit['profile'], 'editedSeconds': edit['editedSeconds'],
            'generatedSeconds': edit['generatedSeconds'], 'sampleCount': 1,
            'maxConcurrentPaid': 1, 'maxAttemptsPerShot': 1,
            'verifiedScenes': sum(s.latest_attempt is not None and s.latest_attempt.state is VideoShotState.VERIFIED for s in job.shots),
            'totalScenes': len(job.shots), 'reservedCostUsd': job.reserved_cost_usd,
            'cost': job.plan['cost'], 'storageTransferAssumption': edit['storageTransferAssumption'],
            'authorizationPhrase': authorization_phrase(job.project_id, job.plan_digest, job.plan['cost']['maxSpendUsd']),
            'workerAlive': alive, 'lastWorkerHeartbeat': worker.get('heartbeatAt'),
            'errorCode': job.error.code if job.error else None,
            'creativeApproval': 'pending', 'generationSubmitted': any(s.latest_attempt and s.latest_attempt.operation_name for s in job.shots)}


def _detached_helper() -> Any:
    spec = importlib.util.spec_from_file_location('trackprompt_hayale_detached', repository() / 'tools/hayale-wnp.py')
    if spec is None or spec.loader is None:
        raise ContractError('repository detached process helper is unavailable')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def require_local_scenes(job: VideoJobRecord) -> None:
    for shot in job.shots:
        if VideoGenerationController._compiled(job, shot.shot_id).continuation_mode != 'local-composition':
            continue
        attempt = shot.latest_attempt
        if (attempt is None or attempt.state is not VideoShotState.VERIFIED or not attempt.local_clip_path
            or not attempt.probe_evidence or not Path(attempt.local_clip_path).is_file()
            or sha256_file(Path(attempt.local_clip_path)) != attempt.clip_sha256):
            raise ContractError('required local composition is incomplete; repeat Prepare for the same profile before Start')


_WORKER_ENV = {'TRACKPROMPT_MC_STATE_ROOT', 'TRACKPROMPT_GCP_AUTH_CONFIG',
               'TRACKPROMPT_MC_FFMPEG_PATH', 'TRACKPROMPT_MC_FFPROBE_PATH',
               'TRACKPROMPT_WNP_ROOT', 'TRACKPROMPT_WNP_RUN'}


def restore_worker_binding(args: argparse.Namespace) -> dict[str, Any]:
    path = Path(args.worker_intent or '').resolve()
    if not path.is_relative_to(repository().resolve()) or path.is_symlink() or path.stat().st_size > 32_000:
        raise ContractError('invalid private worker intent location')
    intent: dict[str, Any] = read_json(path)
    binding = intent['binding']
    environment = binding['environment']
    if (intent.get('jobId') != args.job_id or intent.get('nonce') != args.nonce
        or intent.get('bindingDigest') != sha256_json(binding)
        or binding.get('python') != str(Path(sys.executable).resolve())
        or set(environment) != _WORKER_ENV
        or not all(isinstance(v, str) and '\0' not in v for v in environment.values())):
        raise ContractError('worker runtime/configuration binding mismatch')
    expected = Path(environment['TRACKPROMPT_MC_STATE_ROOT']).parent / 'hayale-16x4/workers' / args.job_id / args.nonce / 'intent.json'
    if path != expected.resolve():
        raise ContractError('worker intent does not belong to the selected job store')
    for key, value in environment.items():
        if value:
            os.environ[key] = value
        else:
            os.environ.pop(key, None)
    return intent


def launch(job: VideoJobRecord) -> dict[str, Any]:
    if summary(job)['workerAlive']:
        return {**summary(job), 'launch': 'existing_worker_retained'}
    if '.cache/codex-runtimes' in Path(sys.base_prefix).as_posix().lower():
        raise ContractError('production requires the stable installed Python runtime')
    if all(s.continuation_mode == 'local-composition' for s in (service_shots(job))):
        raise ContractError('local animatic is prepared; use Export with its approved music arrangement')
    require_local_scenes(job)
    if not any(s.latest_attempt and s.latest_attempt.operation_name for s in job.shots):
        if not job.authorization:
            raise ContractError('approve the displayed bounded batch before Start')
        BatchAuthorization.from_dict(job.authorization).validate_for(
            project_id=job.project_id, plan_digest=job.plan_digest,
            current_reserved_usd=job.reserved_cost_usd, next_request_usd=0)
    from .gcp_auth import load_binding
    auth = load_binding(project_id=job.gcp_project_id)
    if auth.bucket != job.gcs_bucket or auth.model_id != job.plan['profile']['modelId']:
        raise ContractError('saved job and selected cloud binding differ; reconcile before Start')
    for name in ('ffmpeg', 'ffprobe'):
        executable(name)
    root = private_root() / 'workers' / job.id
    nonce = uuid4().hex
    launch_root = root / nonce
    launch_root.mkdir(parents=True)
    receipt = launch_root / 'accepted.json'
    helper = _detached_helper()
    if Path(sys.executable).resolve() != helper.stable_python():
        raise ContractError('detached Start requires the installed Python312 selected by the repository broker')
    environment = {key: os.environ.get(key, '') for key in _WORKER_ENV}
    environment.update(TRACKPROMPT_MC_STATE_ROOT=str(MissionControlConfig.from_repository(repository()).state_root),
                       TRACKPROMPT_GCP_AUTH_CONFIG=str(auth.config_path),
                       TRACKPROMPT_MC_FFMPEG_PATH=executable('ffmpeg'),
                       TRACKPROMPT_MC_FFPROBE_PATH=executable('ffprobe'))
    binding = {'environment': environment, 'python': str(Path(sys.executable).resolve())}
    digest = sha256_json(binding)
    intent_path = launch_root / 'intent.json'
    atomic_write_json(intent_path, {'jobId': job.id, 'planDigest': job.plan_digest, 'nonce': nonce,
                                  'binding': binding, 'bindingDigest': digest})
    child = helper.spawn_detached_python(
        [str(repository() / 'tools/hayale-16x4.py'), '_worker', '--job-id', job.id, '--nonce', nonce,
         '--worker-intent', str(intent_path)],
        launch_root / 'stdout.log', launch_root / 'stderr.log', cwd=repository())
    deadline = time.monotonic()+20
    while not receipt.exists() and time.monotonic() < deadline:
        time.sleep(.1)
    if not receipt.exists():
        raise ContractError('worker acceptance is unconfirmed; use Status and inspect retained launch evidence before another Start')
    value = read_json(receipt)
    if (value.get('pid') != child.pid or value.get('jobId') != job.id
        or value.get('planDigest') != job.plan_digest or value.get('bindingDigest') != digest):
        raise ContractError('worker acceptance identity mismatch; reconcile before another Start')
    return {'jobId': job.id, 'workerAccepted': True, 'pid': child.pid, 'planDigest': job.plan_digest}


def service_shots(job: VideoJobRecord) -> list[Any]:
    return [VideoGenerationController._compiled(job, shot.shot_id) for shot in job.shots]


async def worker(args: argparse.Namespace, service: VideoGenerationController) -> dict[str, Any]:
    if not re.fullmatch(r'[a-f0-9]{32}', args.nonce or ''):
        raise ContractError('worker requires a valid launch identity')
    job = service._record(args.job_id)
    root = private_root() / 'workers' / job.id
    launch_root = root / args.nonce
    intent = read_json(launch_root / 'intent.json')
    if (intent.get('jobId') != job.id or intent.get('planDigest') != job.plan_digest or intent.get('nonce') != args.nonce):
        raise ContractError('worker launch intent mismatch')
    require_local_scenes(job)
    started = process_started_at(os.getpid())
    if started is None:
        raise ContractError('worker process-start identity is unavailable')
    await service.resume(job.id)
    identity = {'jobId': job.id, 'planDigest': job.plan_digest, 'nonce': args.nonce,
                'pid': os.getpid(), 'processStartedAt': started.isoformat(), 'bindingDigest': intent['bindingDigest']}
    atomic_write_json(launch_root / 'accepted.json', identity)
    task = service._tasks[job.id]
    while not task.done():
        now = utc_now().isoformat()
        atomic_write_json(root / 'worker.json', {**identity, 'heartbeatAt': now})
        observe_worker(run=job.id, heartbeat_at=now, pid=os.getpid())
        stop = root / 'stop.json'
        if stop.exists():
            request = read_json(stop)
            if request.get('nonce') == args.nonce and request.get('planDigest') == job.plan_digest:
                await service.cancel(job.id)
                break
        await asyncio.sleep(1)
    try:
        await task
    except asyncio.CancelledError:
        pass
    result = summary(service._record(job.id))
    atomic_write_json(launch_root / 'result.json', result)
    return result


async def export(job: VideoJobRecord, service: VideoGenerationController) -> dict[str, Any]:
    service._claim_prepared_lease(job)
    try:
        clips: dict[str, Path] = {}
        for shot in job.shots:
            attempt = shot.latest_attempt
            if attempt is None or attempt.state is not VideoShotState.VERIFIED or not attempt.local_clip_path:
                raise ContractError('all 16 scenes must be technically verified before export')
            path = Path(attempt.local_clip_path)
            if not attempt.clip_sha256 or sha256_file(path) != attempt.clip_sha256:
                raise ContractError('verified clip changed; resume verification before export')
            clips[shot.shot_id] = path
        music = read_json(private_root() / 'music.json')
        edit = MusicEdit.model_validate(music['edit'])
        root = service._job_root(job)
        revision = sha256_json({'plan': job.plan_digest, 'music': edit.model_dump()})
        emit_lifecycle(run=job.id, source_id='finishing:'+revision, stage='finishing', state='running')
        result = await asyncio.to_thread(finish_edit, job.plan, clips, music_source=Path(music['source']),
                                         music_edit=edit, output_root=root / 'deliveries', wnp_run=job.id)
        manifest = Path(result['exportRoot']) / 'verification.json'
        atomic_write_json(root / 'hayale-final.json', result)
        await service._save(job.model_copy(update={'state': VideoJobState.COMPLETE,
            'preview_path': result['deliveryPath'], 'export_root': result['exportRoot'],
            'timeline_path': str(Path(result['exportRoot']) / 'resolved-timeline.json'),
            'completed_at': utc_now(), 'updated_at': utc_now(), 'error': None}))
        emit_lifecycle(run=job.id, source_id='export:'+sha256_file(manifest), stage='export',
                        technical_verified=True, final_manifest_sha256=sha256_file(manifest),
                        creative_approval='pending', state='waiting', tasks=result['sceneVerification'])
        return {'jobId': job.id, 'technicalVerified': True, 'creativeApproval': 'pending',
                'frames': result['frames'], 'durationSeconds': result['durationSeconds'],
                'deliveryPath': result['deliveryPath']}
    finally:
        service._release_prepared_lease(job.id)


async def command(args: argparse.Namespace) -> dict[str, Any]:
    if args.action == '_worker':
        restore_worker_binding(args)
    if args.action == 'check':
        return check()
    if args.action == 'select':
        return select_asset(args)
    if args.action == 'music':
        return select_music(args)
    service = controller()
    try:
        if args.action == 'prepare':
            return await prepare(args, service)
        if not args.job_id or not re.fullmatch(r'[a-f0-9-]{36}', args.job_id):
            raise ContractError('this action requires the saved -JobId')
        job = service._record(args.job_id)
        if args.action == 'recoverworker':
            service.recover_stopped_worker(job.id, expected_token=args.lease_token or '')
            if 'hayaleEdit' in job.plan.get('continuity', {}):
                return {**summary(service._record(job.id)), 'workerLeaseRecovered': True}
            return {'jobId': job.id, 'state': job.state.value, 'planDigest': job.plan_digest,
                    'workerLeaseRecovered': True, 'generationSubmitted': False}
        if 'hayaleEdit' not in job.plan.get('continuity', {}):
            raise ContractError('this command only accepts versioned Hayale 16x4 jobs')
        if args.action == 'status':
            return summary(job)
        if args.action == 'authorize':
            await service.authorize(job.id, VideoAuthorizationRequest(confirmation=args.confirmation or ''))
            return summary(service._record(job.id))
        if args.action in {'start', 'resume'}:
            return launch(job)
        if args.action == '_worker':
            return await worker(args, service)
        if args.action == 'stop':
            root = private_root() / 'workers' / job.id
            owner = read_json(root / 'worker.json')
            atomic_write_json(root / 'stop.json', {'planDigest': job.plan_digest, 'nonce': owner['nonce']})
            return {'jobId': job.id, 'localStopRequested': True, 'providerCancellationConfirmed': False}
        if args.action == 'export':
            return await export(job, service)
        if args.action == 'verify':
            result = read_json(service._job_root(job) / 'hayale-final.json')
            if sha256_file(Path(result['deliveryPath'])) != result['sha256']:
                raise ContractError('final delivery changed since its verified export')
            return verify_delivery(Path(result['deliveryPath']), width=result['width'], height=result['height'],
                                   fps=result['fps'], frames=result['frames'], require_audio=True)
        raise ContractError('unsupported operator action')
    finally:
        service.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['check','select','music','prepare','authorize','start','resume',
                                         'status','stop','export','verify','recoverworker','_worker'])
    parser.add_argument('--profile', default='still-animatic')
    for name in ('job-id','scene','asset','confirmation','lease-token','nonce','worker-intent'):
        parser.add_argument('--'+name)
    parser.add_argument('--music-segment', action='append')
    for name in ('promotional-use','cloud-upload','retained-video','approve-arrangement'):
        parser.add_argument('--'+name, action='store_true')
    parser.add_argument('--source-in', type=float, default=0)
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(command(args))
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as exc:
        from .gcp_auth import GcpAuthError
        if isinstance(exc, GcpAuthError):
            print(json.dumps({'ok': False, **exc.to_dict(), 'generationAutomaticallyRetried': False}))
            return 2
        # Native diagnostics and private paths remain in local receipts, never WNP.
        detail = str(exc) if isinstance(exc, ContractError) else 'Inspect the saved local run and selected input prerequisites.'
        print(json.dumps({'ok': False, 'code': 'hayale_action_required', 'detail': detail,
                          'generationAutomaticallyRetried': False}))
        return 2
