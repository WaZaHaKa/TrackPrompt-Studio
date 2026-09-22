"""Fresh-shell controller: hashes, original transfers, cloud jobs and delivery only."""
from __future__ import annotations

import argparse
import importlib
import json
import os
import time
import urllib.parse
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..gcp_auth import GcpAuthError, access_token, load_binding, resolve_gcloud
from ..jsonio import atomic_write_json
from .cloud import CloudError, GcsClient, StateConflict
from .config import ACCOUNT, BUCKET, NAMES, PROJECT, REGION, bound_path, build_plan, file_hash
from .delivery import deliver
from .infrastructure import (
    ApiError,
    CloudSetupError,
    GoogleApi,
    batch_spec,
    build_spec,
    immutable_image,
    setup,
    worker_archive,
)
from .state import validate_manifest


def now() -> str:
    return datetime.now(UTC).isoformat()


class BoundToken:
    def __init__(self) -> None:
        self.binding = load_binding(project_id=PROJECT)
        if self.binding.account != ACCOUNT or self.binding.bucket != BUCKET or self.binding.region != REGION:
            raise ValueError('The existing TrackPrompt credential binding differs from the approved Hayale identity.')
        self.value, self.expires = '', 0.0

    def __call__(self) -> str:
        if time.monotonic() >= self.expires:
            self.value = access_token(binding=self.binding)
            self.expires = time.monotonic() + 1800
        return self.value

    def invalidate(self) -> None:
        self.value, self.expires = '', 0.0


@contextmanager
def controller_lock(root: Path) -> Iterator[None]:
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'controller.lock').open('a+b') as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl = importlib.import_module('fcntl')
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise CloudSetupError('Another Hayale controller is already attached. Its cloud run continues independently.') from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _same_binding(value: dict[str, Any], plan: dict[str, Any]) -> None:
    if value.get('runId') != plan['runId'] or value.get('planDigest') != plan['planDigest']:
        raise CloudSetupError('A different canonical GCP-only Hayale plan already exists; it will not be replaced or reauthorized automatically.')


def bind_run(storage: GcsClient, plan: dict[str, Any], mapping: dict[str, Any], sources: Path) -> dict[str, Any]:
    anchor = 'hayale-final/final-gcp16-binding.json'
    identity = {'schemaVersion': 1, 'runId': plan['runId'], 'planDigest': plan['planDigest']}
    try:
        storage.put_json(anchor, identity, if_generation_match=0)
    except StateConflict:
        _same_binding(storage.get_json(anchor)[0], plan)
    name = plan['gcsRoot'] + 'state/run-manifest.json'
    if storage.metadata(name):
        current, _ = storage.get_json(name)
        _same_binding(current, plan)
        validate_manifest(current)
        return current
    # Only the fixed map is iterated. No directory scanning or derived media upload.
    for role, entry in mapping['sources'].items():
        source = bound_path(sources, entry)
        extension = '.png' if entry['mimeType'] == 'image/png' else '.jpg'
        category = 'logo' if role == 'logo' else 'stills'
        object_name = plan['gcsRoot'] + f'inputs/{category}/{role}-{entry["sha256"]}{extension}'
        receipt = storage.upload_file(object_name, source, content_type=entry['mimeType'])
        plan['sources'][role] = {**plan['sources'][role], **receipt}
    audio = bound_path(sources, mapping['soundtrack'])
    receipt = storage.upload_file(plan['gcsRoot'] + 'inputs/audio/' + file_hash(audio) + audio.suffix.lower(),
                                  audio, content_type=mapping['soundtrack'].get('mimeType', 'audio/wav'))
    plan['soundtrack'] = {**plan['soundtrack'], **receipt}
    plan['createdAt'] = plan['updatedAt'] = now()
    validate_manifest(plan)
    try:
        storage.put_json(name, plan, if_generation_match=0)
    except StateConflict:
        current, _ = storage.get_json(name)
        _same_binding(current, plan)
        validate_manifest(current)
        return current
    return plan


def ensure_image(api: GoogleApi, storage: GcsClient, repository: Path, root: Path) -> str:
    digest = worker_archive(repository, root / 'worker-source.tar.gz')
    image = immutable_image(api, digest)
    if image:
        return image
    source = storage.upload_file('hayale-build/' + digest + '.tar.gz', root / 'worker-source.tar.gz', content_type='application/gzip')
    base = f'https://cloudbuild.googleapis.com/v1/projects/{PROJECT}/locations/{REGION}/builds'
    query = urllib.parse.urlencode({'filter': 'tags=source-' + digest, 'pageSize': 10})
    builds = (api.request('GET', base + '?' + query) or {}).get('builds', [])
    intent_name = 'hayale-build/' + digest + '-submission.json'
    intent_meta = storage.metadata(intent_name)
    intent: dict[str, Any] = storage.get_json(intent_name)[0] if intent_meta else {'sourceDigest': digest, 'attempts': []}
    previous_attempt = intent['attempts'][-1] if intent['attempts'] else None
    if previous_attempt and previous_attempt.get('buildId') and not any(b.get('id') == previous_attempt['buildId'] for b in builds):
        retained = api.request('GET', base + '/' + previous_attempt['buildId'], missing_ok=True)
        if retained:
            builds.append(retained)
    active = [b for b in builds if b.get('status') in {'QUEUED', 'PENDING', 'WORKING'}]
    if len(active) > 1:
        raise CloudSetupError('Multiple worker builds need reconciliation; no production is submitted.')
    if active:
        build = active[0]
    elif any(b.get('status') == 'SUCCESS' for b in builds):
        image = immutable_image(api, digest)
        if not image:
            raise CloudSetupError('Successful worker build has no immutable image digest.')
        return image
    else:
        if len(builds) >= 3:
            raise CloudSetupError('Worker image build failed three times; inspect the bounded Cloud Build error.')
        if previous_attempt and previous_attempt.get('status') != 'request_rejected' and not any(b.get('id') == previous_attempt.get('buildId') for b in builds):
            raise CloudSetupError('Worker build submission is uncertain and no matching operation is visible yet. The preserved intent prevents a duplicate build.')
        attempt: dict[str, Any] = {'status': 'submission_intent', 'createdAt': now()}
        intent['attempts'].append(attempt)
        intent_meta = storage.put_json(intent_name, intent, if_generation_match=intent_meta['generation'] if intent_meta else 0)
        try:
            response = api.request('POST', base, build_spec(source, digest)) or {}
            build = response.get('metadata', {}).get('build', {})
        except CloudSetupError as exc:
            if isinstance(exc, ApiError) and exc.code in {400, 401, 403, 404, 429}:
                # An explicit rejection is not an accepted build; preserve it.
                attempt.update(status='request_rejected', httpStatus=exc.code)
                storage.put_json(intent_name, intent, if_generation_match=intent_meta['generation'])
                raise
            # A lost build acknowledgement is reconciled by the exact source tag.
            time.sleep(5)
            matches = (api.request('GET', base + '?' + query) or {}).get('builds', [])
            active = [b for b in matches if b.get('status') in {'QUEUED', 'PENDING', 'WORKING', 'SUCCESS'}]
            if len(active) != 1:
                raise CloudSetupError('Worker build submission is uncertain. Resume this command after Cloud Build becomes reachable.') from None
            build = active[0]
    if not build.get('id'):
        raise CloudSetupError('Cloud Build did not return a durable build identity; resume to reconcile by source digest.')
    if intent['attempts']:
        intent['attempts'][-1].update(status='accepted', buildId=build['id'])
        storage.put_json(intent_name, intent, if_generation_match=intent_meta['generation'] if intent_meta else 0)
    atomic_write_json(root / 'build.json', {'sourceDigest': digest, 'buildId': build['id'], 'updatedAt': now()})
    print('Building the bounded cloud worker. No source media has been uploaded.', flush=True)
    for _ in range(180):
        build = api.request('GET', base + '/' + str(build['id'])) or {}
        if build.get('status') == 'SUCCESS':
            image = immutable_image(api, digest)
            if image:
                return image
        elif build.get('status') in {'FAILURE', 'INTERNAL_ERROR', 'TIMEOUT', 'CANCELLED', 'EXPIRED'}:
            raise CloudSetupError('The cloud worker image build failed. Its build identity is saved; no generation started.')
        time.sleep(10)
    raise CloudSetupError('Cloud Build remains pending; resume this command to reconnect.')


def ensure_batch(api: GoogleApi, run_id: str, image: str, *, smoke: bool = False) -> dict[str, Any]:
    parent = f'https://batch.googleapis.com/v1/projects/{PROJECT}/locations/{REGION}/jobs'
    for attempt in range(1, 4):
        job_id = (run_id + '-smoke') if smoke else (run_id + f'-worker-{attempt}')
        existing = api.request('GET', parent + '/' + job_id, missing_ok=True)
        if existing:
            if existing.get('labels', {}).get('run') != run_id:
                raise CloudSetupError('The deterministic Batch job identity belongs to another run.')
            if existing.get('status', {}).get('state') in {'FAILED', 'DELETION_IN_PROGRESS'}:
                if smoke:
                    raise CloudSetupError('Cloud runtime smoke failed; inspect its retained Batch diagnostics.')
                continue
            return existing
        spec = batch_spec(run_id, image, smoke=smoke)
        try:
            return api.request('POST', parent + '?jobId=' + job_id, spec) or {}
        except CloudSetupError as exc:
            # Job ID is an idempotency identity, not a time-based fresh run.
            existing = api.request('GET', parent + '/' + job_id, missing_ok=True)
            if existing:
                return existing
            if isinstance(exc, ApiError) and exc.code not in {408, 409, 429, 500, 502, 503, 504}:
                raise
            raise CloudSetupError('Batch submission acknowledgement is unresolved. Resume the same command; the deterministic job ID prevents duplicates.') from exc
    raise CloudSetupError('Three bounded cloud workers failed; completed scenes remain retained in GCS.')


def observe(repository: Path, manifest: dict[str, Any], generation: str) -> None:
    """Optional projection of durable cloud facts; never a cloud execution dependency."""
    try:
        from app.wnp import WNPProjection, emit_lifecycle
        from app.wnp.check import local_check
        status = local_check(repository)
        if status.get('project') != 'trackprompt-studio' or status.get('node') != 'render-rtx3060':
            return
        with WNPProjection(repository) as projection:
            if projection.drain().pending:
                return
            projection.select_run(manifest['runId'])
        os.environ['TRACKPROMPT_WNP_ROOT'] = str(repository)
        os.environ['TRACKPROMPT_WNP_RUN'] = manifest['runId']
        complete = sum(bool(scene.get('output')) for scene in manifest['scenes'])
        verified = manifest.get('status') == 'cloud_verified'
        stage = {'cloud_verified': 'export', 'preparation': 'preparation', 'seed_preparation': 'preparation',
                 'provider_polling': 'generation', 'scene_verification': 'verification',
                 'audio_preparation': 'music-mux', 'final_verification': 'verification',
                 'mux': 'music-mux', 'finishing': 'finishing', 'transfer': 'transfer',
                 'delivery': 'transfer', 'retrying': 'recovery', 'blocked': 'recovery'
                 }.get(manifest.get('stage', manifest['status']), 'generation')
        emit_lifecycle(run=manifest['runId'], source_id='hayale-gcp-' + generation, stage=stage,
                       state='waiting' if verified else ('failed' if manifest.get('status') == 'blocked' else 'running'),
                       profile='cloud-' + str(manifest.get('stage', manifest.get('status', 'preparation'))).replace('_', '-'),
                       technical_verified=verified,
                       final_manifest_sha256=manifest.get('delivery', {}).get(NAMES[1], {}).get('sha256'),
                       progress={'current': complete, 'total': 16, 'unit': 'scenes'},
                       pipeline={'estimated_cost_usd': manifest.get('cost', {}).get('estimatedGenerationUsd'),
                                 'provider_attempts': sum(len(s.get('attempts', [])) for s in manifest['scenes'])})
    except Exception:
        pass


def execute(repository: Path, *, check: bool = False, setup_only: bool = False,
            smoke: bool = False, no_open: bool = False) -> dict[str, Any]:
    root = repository / '.trackprompt-data/hayale-gcp-final'
    auth = Path(os.environ['LOCALAPPDATA']) / 'TrackPrompt/hayale-gcp-20260922/auth.json'
    os.environ['TRACKPROMPT_GCP_AUTH_CONFIG'] = str(auth.resolve())
    os.environ.pop('TRACKPROMPT_GCLOUD_EXECUTABLE', None)
    resolve_gcloud()
    mapping = json.loads((repository / 'video-projects/hayale-flowers-promo/v4-gcp-only/source-map.json').read_text(encoding='utf-8'))
    sources = Path('D:/HayaleFlowers/promo-2026-09-15')
    # Once originals are bound in GCS, a disconnected source drive must not
    # prevent recovery/download. Missing uploads are hash-checked in bind_run.
    plan = build_plan(mapping, sources, verify_local_sources=check)
    token = BoundToken()
    api, storage = GoogleApi(token.__call__), GcsClient(BUCKET, token.__call__)
    with controller_lock(root):
        if check:
            return {'ok': True, 'mode': 'non-generative-check', 'sourceHashesVerified': 16,
                    'runId': plan['runId'], 'cost': plan['cost'], **setup(api, check_only=True)}
        local_binding = root / 'canonical-plan.json'
        if local_binding.exists():
            _same_binding(json.loads(local_binding.read_text(encoding='utf-8')), plan)
        else:
            atomic_write_json(local_binding, {'runId': plan['runId'], 'planDigest': plan['planDigest']})
        setup_result = setup(api)
        image = ensure_image(api, storage, repository, root)
        if setup_only and not smoke:
            return {'ok': True, 'mode': 'setup-only', 'image': image, **setup_result}
        if smoke:
            smoke_id = 'hayale-smoke-' + image.rsplit(':', 1)[-1][:24]
            smoke_job = ensure_batch(api, smoke_id, image, smoke=True)
            for _ in range(120):
                status = (api.request('GET', 'https://batch.googleapis.com/v1/' + smoke_job['name']) or {}).get('status', {}).get('state')
                if status == 'SUCCEEDED':
                    return {'ok': True, 'mode': 'cloud-runtime-smoke', 'productionStarted': False, 'image': image, 'job': smoke_job['name']}
                if status == 'FAILED':
                    raise CloudSetupError('Cloud runtime smoke failed; no production inputs were uploaded.')
                time.sleep(10)
            raise CloudSetupError('Cloud runtime smoke remains pending; resume -Smoke to reconnect.')
        print(f"{plan['cost']['model']} | {REGION} | native 1080p | 16 x 4s = 64 generated seconds", flush=True)
        print('Generation estimate: USD 6.40 at USD 0.10/second (rate checked 2026-09-22); additional cloud processing is metered. Cost is telemetry, not an admission cap.', flush=True)
        manifest = bind_run(storage, plan, mapping, sources)
        name = plan['gcsRoot'] + 'state/run-manifest.json'
        job: dict[str, Any] | None = None
        if manifest.get('status') != 'cloud_verified':
            job = ensure_batch(api, plan['runId'], image)
            atomic_write_json(root / 'cloud-job.json', {'name': job['name'], 'image': image, 'runId': plan['runId']})
            print('Cloud production is running independently. Keep this window for automatic download/open, or rerun this command later to reconnect.', flush=True)
        previous = ''
        while True:
            manifest, meta = storage.get_json(name)
            _same_binding(manifest, plan)
            validate_manifest(manifest)
            atomic_write_json(root / 'last-cloud-manifest.json', manifest)
            observe(repository, manifest, str(meta['generation']))
            status = str(manifest.get('stage', manifest.get('status', 'preparation')))
            progress = sum(bool(scene.get('output')) for scene in manifest['scenes'])
            message = f'{status}: {progress}/16 scenes verified in GCP'
            if message != previous:
                print(message, flush=True)
                previous = message
            if manifest.get('status') == 'cloud_verified':
                output = Path.home() / 'Videos/Hayale-Final-1080P'
                result = deliver(storage, manifest, output, open_file=not no_open)
                result.update(runId=plan['runId'], completedAt=now())
                atomic_write_json(root / 'delivery.json', result)
                receipt_name = plan['gcsRoot'] + 'state/local-delivery.json'
                previous_receipt = storage.metadata(receipt_name)
                storage.put_json(receipt_name, result, if_generation_match=previous_receipt['generation'] if previous_receipt else 0)
                return {'ok': True, **result}
            if manifest.get('status') == 'blocked':
                error = manifest.get('lastError', manifest.get('error', {}))
                code = error.get('code', 'cloud_workflow_blocked') if isinstance(error, dict) else str(error)
                raise CloudSetupError('Cloud workflow stopped: ' + code + '. All accepted operations and completed scenes are preserved.')
            if job:
                native = api.request('GET', 'https://batch.googleapis.com/v1/' + job['name']) or {}
                if native.get('status', {}).get('state') == 'FAILED':
                    job = ensure_batch(api, plan['runId'], image)
                elif native.get('status', {}).get('state') == 'SUCCEEDED':
                    latest, _ = storage.get_json(name)
                    _same_binding(latest, plan)
                    if latest.get('status') == 'cloud_verified':
                        continue
                    raise CloudSetupError('Cloud worker exited without verified delivery; retained manifest needs reconciliation.')
            time.sleep(15)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', type=Path, required=True)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--setup-only', action='store_true')
    parser.add_argument('--smoke', action='store_true')
    parser.add_argument('--no-open', action='store_true')
    args = parser.parse_args(argv)
    try:
        while True:
            try:
                result = execute(args.repository.resolve(), check=args.check, setup_only=args.setup_only,
                                 smoke=args.smoke, no_open=args.no_open)
                break
            except (CloudError, CloudSetupError, GcpAuthError) as exc:
                if not getattr(exc, 'retryable', False):
                    raise
                print('A temporary cloud read or transfer failed. Reconnecting to the same durable run in 30 seconds.', flush=True)
                time.sleep(30)
        check_name = str(result.get('mode', 'operator-delivery'))
        atomic_write_json(args.repository / '.trackprompt-data/hayale-gcp-final/checks' /
                          (check_name + '-' + datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ') + '.json'), result)
        print(json.dumps(result, ensure_ascii=True), flush=True)
        return 0
    except KeyboardInterrupt:
        print('Controller closed. Any accepted cloud job continues; rerun this same command to reconnect.', flush=True)
        return 130
    except (CloudError, CloudSetupError, GcpAuthError, ValueError, OSError) as exc:
        code = getattr(exc, 'code', None)
        message = str(exc) if isinstance(exc, (CloudSetupError, ValueError, GcpAuthError)) else code or 'Required local file or runtime is unavailable.'
        root = args.repository / '.trackprompt-data/hayale-gcp-final'
        try:
            atomic_write_json(root / 'last-error.json', {'error': message, 'code': code, 'at': now()})
        except OSError:
            pass
        print('Hayale stopped: ' + message, flush=True)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
