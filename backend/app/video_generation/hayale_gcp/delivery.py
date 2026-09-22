"""Transport-only download, historical preservation and opening; no media decode."""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from ..jsonio import atomic_write_json
from .config import NAMES, PROJECT, digest_json, file_hash


class DownloadStorage(Protocol):
    def download(self, name: str, destination: Path, *, generation: str,
                 expected_sha256: str | None = None) -> dict[str, Any]: ...


def _install_snapshot(staging: Path, output: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """Persist the complete previous bundle before replacing its first member."""
    path = staging / 'installation.json'
    identity = {'runId': manifest['runId'], 'planDigest': manifest['planDigest'],
                'delivery': manifest['delivery']}
    if path.exists():
        snapshot: dict[str, Any] = json.loads(path.read_text(encoding='utf-8'))
        if any(snapshot.get(key) != value for key, value in identity.items()):
            raise ValueError('Retained delivery installation belongs to a different cloud artifact set.')
    else:
        initial_previous = {name: {'sha256': file_hash(output / name), 'size': (output / name).stat().st_size}
                    if (output / name).is_file() else None for name in NAMES}
        master = initial_previous[NAMES[0]]
        history_key = master['sha256'] if master else 'partial-' + digest_json(initial_previous)
        snapshot = {**identity, 'previous': initial_previous, 'historyKey': history_key, 'status': 'prepared'}
        atomic_write_json(path, snapshot)
    previous = snapshot.get('previous')
    if (not isinstance(previous, dict) or set(previous) != set(NAMES)
            or not re.fullmatch(r'(?:partial-)?[a-f0-9]{64}', str(snapshot.get('historyKey', '')))):
        raise ValueError('Retained delivery snapshot is damaged; previous artifacts are protected.')
    for prior in previous.values():
        if prior is not None and (not isinstance(prior, dict)
                or not re.fullmatch(r'[a-f0-9]{64}', str(prior.get('sha256', '')))
                or type(prior.get('size')) is not int or prior['size'] < 0):
            raise ValueError('Retained delivery snapshot is damaged; previous artifacts are protected.')
    history = output / 'history' / snapshot['historyKey']
    for name in NAMES:
        prior = previous[name]
        if prior is None or prior['sha256'] == manifest['delivery'][name]['sha256']:
            continue
        history.mkdir(parents=True, exist_ok=True)
        preserved = history / name
        if not preserved.exists():
            source = output / name
            if not source.is_file() or file_hash(source) != prior['sha256']:
                raise ValueError('Previous delivery changed before preservation; replacement is refused.')
            candidate = history / ('.preserving-' + uuid4().hex)
            with source.open('rb') as incoming, candidate.open('xb') as outgoing:
                shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
                outgoing.flush()
                os.fsync(outgoing.fileno())
            if file_hash(candidate) != prior['sha256'] or candidate.stat().st_size != prior['size']:
                raise ValueError('Historical delivery preservation failed; current master remains intact.')
            os.replace(candidate, preserved)
        if file_hash(preserved) != prior['sha256'] or preserved.stat().st_size != prior['size']:
            raise ValueError('Historical delivery preservation hash mismatch; retained snapshot is protected.')
    return snapshot


def deliver(storage: DownloadStorage, manifest: dict[str, Any], output: Path,
            *, open_file: bool = True) -> dict[str, Any]:
    if manifest.get('status') != 'cloud_verified' or set(manifest.get('delivery', {})) != set(NAMES):
        raise ValueError('The cloud has not published all three verified delivery objects.')
    output.mkdir(parents=True, exist_ok=True)
    staging = output / ('.download-' + manifest['runId'])
    staging.mkdir(exist_ok=True)
    receipts = manifest['delivery']
    for name in NAMES:
        receipt = receipts[name]
        final, partial = output / name, staging / name
        if final.is_file() and file_hash(final) == receipt['sha256']:
            shutil.copyfile(final, partial)
        elif not partial.is_file() or file_hash(partial) != receipt['sha256']:
            storage.download(receipt['object'], partial, generation=str(receipt['generation']),
                             expected_sha256=receipt['sha256'])
        if file_hash(partial) != receipt['sha256'] or partial.stat().st_size != int(receipt['size']):
            raise ValueError('Downloaded delivery hash or size does not match the authoritative cloud receipt.')
    verification = json.loads((staging / NAMES[1]).read_text(encoding='utf-8'))
    execution = verification.get('cloudExecution', {})
    if (verification.get('runId') != manifest['runId'] or verification.get('planDigest') != manifest['planDigest']
            or verification.get('sha256') != receipts[NAMES[0]]['sha256']
            or verification.get('technicalVerified') is not True
            or not isinstance(execution, dict) or execution.get('provider') != 'gcp-batch'
            or execution.get('projectId') != PROJECT or execution.get('identityVerified') is not True
            or execution.get('production') is not True or not execution.get('jobUid')):
        raise ValueError('Authoritative cloud verification does not bind this run and final master.')
    required = {'width': 1920, 'height': 1080, 'fps': 24, 'frames': 1536, 'durationSeconds': 64,
                'sceneCount': 16, 'codec': 'h264', 'audioCodec': 'aac', 'audioStartOffsetSeconds': 0}
    if any(verification.get(key) != expected for key, expected in required.items()) or any(
            verification.get(key) is not True for key in ('audioPresent', 'ptsVerified', 'fullDecodeVerified')):
        raise ValueError('Authoritative cloud verification does not satisfy the exact delivery contract.')
    snapshot = _install_snapshot(staging, output, manifest)
    # Install the MP4 last. Every replacement is already byte-verified above.
    for name in (NAMES[1], NAMES[2], NAMES[0]):
        current = output / name
        if current.is_file() and file_hash(current) == receipts[name]['sha256']:
            continue
        prior = snapshot['previous'][name]
        if current.exists() and (prior is None or file_hash(current) != prior['sha256']):
            raise ValueError('Local delivery changed outside this installation; replacement is refused.')
        os.replace(staging / name, output / name)
        if file_hash(output / name) != receipts[name]['sha256']:
            raise ValueError('Installed delivery bytes differ from their authoritative cloud receipt.')
    atomic_write_json(staging / 'installation.json', {**snapshot, 'status': 'complete'})
    result = {'transportVerified': True, 'sha256': file_hash(output / NAMES[0]),
              'authoritativeVerificationLocation': 'gcp', 'opened': False}
    if open_file:
        if os.name != 'nt':
            raise ValueError('Automatic delivery opening requires the Windows operator.')
        os.startfile(output / NAMES[0])
        result['opened'] = True
    return result
