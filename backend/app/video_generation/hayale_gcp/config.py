"""Closed production scope and hash-only discovery on the Windows controller."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

PROJECT = 'nexus-core-481713'
BUCKET = 'nexus-brain-481713'
REGION = 'us-central1'
ACCOUNT = 'theone@theclearpill.online'
MODEL = 'veo-3.1-fast-generate-001'
WORKER_ACCOUNT = f'trackprompt-hayale-worker@{PROJECT}.iam.gserviceaccount.com'
BUILD_ACCOUNT = f'trackprompt-hayale-build@{PROJECT}.iam.gserviceaccount.com'
REPOSITORY = 'trackprompt-hayale'
IMAGE = f'{REGION}-docker.pkg.dev/{PROJECT}/{REPOSITORY}/worker'
ROLES = ('logo', 'customer-01', 'customer-02', 'customer-03', 'exterior-01', 'exterior-02',
         'product-01', 'product-02', 'product-03', 'vibe-01', 'vibe-02', 'vibe-03',
         'tour-01', 'tour-02', 'tour-03')
ORDER = ('logo', 'exterior-01', 'exterior-02', 'customer-01', 'product-01', 'vibe-01',
         'tour-01', 'customer-02', 'product-02', 'vibe-02', 'tour-02', 'customer-03',
         'product-03', 'vibe-03', 'tour-03', 'logo')
NAMES = ('Hayale-Flowers-Promo-1080p.mp4', 'Hayale-Flowers-Promo-verification.json',
         'Hayale-Flowers-Promo-delivery-report.md')


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def digest_json(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def validate_mapping(mapping: dict[str, Any]) -> None:
    sources = mapping.get('sources', {})
    if mapping.get('schemaVersion') != 1 or set(sources) != set(ROLES):
        raise ValueError('Source map must bind exactly one logo, three customers, two exteriors and three of each interior role.')
    entries = list(sources.values()) + [mapping.get('soundtrack', {})]
    for entry in entries:
        relative = str(entry.get('relativePath', '')).replace('\\', '/')
        if (not relative or relative.startswith('/') or ':' in relative or '..' in relative.split('/')
                or not re.fullmatch('[a-f0-9]{64}', str(entry.get('sha256', '')))):
            raise ValueError('Every original requires a safe relative path and SHA-256 hash.')
    if len({entry['sha256'] for entry in sources.values()}) != 15:
        raise ValueError('The fifteen original images, including the three customers, must be distinct.')
    if any(entry.get('mimeType') not in {'image/png', 'image/jpeg'} for entry in sources.values()):
        raise ValueError('Selected originals must be JPEG or PNG photographs/logo.')


def bound_path(root: Path, entry: dict[str, Any]) -> Path:
    path = (root / str(entry['relativePath'])).resolve(strict=True)
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError('A selected original is outside the Hayale source root.')
    if file_hash(path) != entry['sha256']:
        raise ValueError('A selected original hash changed; the approved source binding is no longer valid.')
    return path


def build_plan(mapping: dict[str, Any], source_root: Path, *, verify_local_sources: bool = True) -> dict[str, Any]:
    validate_mapping(mapping)
    if verify_local_sources:
        for entry in [*mapping['sources'].values(), mapping['soundtrack']]:
            bound_path(source_root, entry)
    sources = {role: {'sha256': value['sha256'], 'mimeType': value['mimeType'],
                      'seedRecipe': value.get('seedRecipe', {'strategy': 'preserve-subject'})}
               for role, value in mapping['sources'].items()}
    scope = {'schemaVersion': 1, 'workflow': 'hayale-all-sixteen-gcp-only-v1',
             'project': PROJECT, 'bucket': BUCKET, 'region': REGION, 'model': MODEL,
             'sources': sources, 'soundtrack': {'sha256': mapping['soundtrack']['sha256']},
             'profile': {'resolution': '1080p', 'width': 1920, 'height': 1080, 'aspectRatio': '16:9',
                         'durationSeconds': 4, 'fps': 24, 'generateAudio': False, 'enhancePrompt': True},
             'policy': {'maxGenerationsPerScene': 3, 'sceneCount': 16, 'generationConcurrency': 1,
                        'costIsTelemetry': True, 'noCreativeAlternates': True},
             'arrangement': {'segments': [{'sourceStartSeconds': 0, 'durationSeconds': 43.24},
                                          {'sourceStartSeconds': 38.76, 'durationSeconds': 4.48},
                                          {'sourceStartSeconds': 43.24, 'durationSeconds': 16.28}],
                             'joinFadeSeconds': 0.005},
             'sceneOrder': list(ORDER)}
    digest = digest_json(scope)
    run_id = 'hayale-gcp16-' + digest[:24]
    return {**scope, 'immutablePlan': json.loads(json.dumps(scope)),
            'planDigest': digest, 'runId': run_id, 'gcsRoot': f'hayale-final/{run_id}/',
            'runtimeServiceAccount': WORKER_ACCOUNT,
            'scenes': [{'sceneId': f'scene-{i:03}', 'role': role, 'mode': 'gcp-image-to-video',
                        'seedRecipe': sources[role]['seedRecipe'], 'attempts': [], 'seed': None, 'output': None}
                       for i, role in enumerate(ORDER, 1)],
            'cost': {'model': MODEL, 'region': REGION, 'resolution': '1080p', 'sceneCount': 16,
                     'generatedSeconds': 64, 'rateUsdPerGeneratedSecond': 0.10,
                     'estimatedGenerationUsd': 6.40, 'maximumAttemptSeconds': 192,
                     'rateCheckedOn': '2026-09-22',
                     'rateSource': 'https://cloud.google.com/vertex-ai/generative-ai/pricing',
                     'additionalCloudProcessingUsd': None,
                     'additionalCloudProcessing': 'Batch CPU VM, Cloud Build, private storage and transfer; metered actual usage.',
                     'invoicedCostUsd': None}, 'status': 'preparation'}
