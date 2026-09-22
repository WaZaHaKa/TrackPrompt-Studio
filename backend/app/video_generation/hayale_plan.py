"""Versioned Hayale edits compiled into the existing Mission Control contract.

Selection is local and explicit. A selected asset's promotional permission is
independent of permission to upload it. No file in this module starts a provider.
"""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Literal

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from .continuity import compile_reference_image
from .contracts import CompiledPlan, CompiledShot, ContractError, GenerationProfile
from .jsonio import read_json, sha256_file, sha256_json


class EditModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra='forbid')


class Scene(EditModel):
    id: str = Field(pattern=r'^shot-[0-9]{3}$')
    asset_slot: str
    title: str
    role: Literal['logo', 'customer', 'shop', 'bouquet', 'product', 'craft', 'detail']
    mode: Literal['gcp-first-frame', 'local-composition', 'retained-video']
    start: str = Field(min_length=20)
    movement: str = Field(min_length=20)
    reveal: str = Field(min_length=20)
    finish: str = Field(min_length=20)
    local_motion: Literal['hold', 'pan-left', 'pan-right', 'push', 'pull']
    timeline_frames: int = Field(gt=0)
    transition: Literal['cut'] = 'cut'
    source_seconds: int = Field(ge=4, le=600)
    source_fps: int = Field(ge=1, le=120)
    source_in_seconds: float = Field(ge=0, allow_inf_nan=False)

    def validate_timing(self, fps: int) -> None:
        if self.source_in_seconds + self.timeline_frames / fps > self.source_seconds:
            raise ContractError('source trim exceeds the selected source duration')
        if self.mode == 'gcp-first-frame' and (self.source_seconds not in {4, 6, 8} or self.source_fps != 24):
            raise ContractError('unsupported first-frame provider source duration or frame rate')
        if self.role in {'logo', 'customer'} and self.mode != 'local-composition':
            raise ContractError('logo and customer artwork must use local composition')


class EditProfile(EditModel):
    resolution: Literal['720p', '1080p']
    paid_scene_ids: tuple[str, ...]
    local_motion: bool


class Scenario(EditModel):
    schema_version: Literal['hayale-edit/1']
    version: str
    project_id: Literal['hayale-flowers-promo']
    title: str
    fps: Literal[24]
    aspect_ratio: Literal['16:9']
    scenes: tuple[Scene, ...]
    profiles: dict[str, EditProfile]
    music: dict[str, Any]
    pricing: dict[str, Any]
    capabilities: dict[str, Any]
    execution_limits: dict[str, int]

    @model_validator(mode='after')
    def film_contract(self) -> Scenario:
        if len(self.scenes) != 16 or [s.id for s in self.scenes] != [f'shot-{i:03}' for i in range(1, 17)]:
            raise ValueError('exactly 16 ordered scene slots are required')
        if [i for i, s in enumerate(self.scenes) if s.role == 'logo'] != [0, 15]:
            raise ValueError('intro and outro must be the two logo scenes')
        if sum(s.role == 'customer' for s in self.scenes) != 3:
            raise ValueError('three customer scenes are required')
        for scene in self.scenes:
            if scene.timeline_frames != self.fps * 4:
                raise ValueError('each timeline slot must be four seconds')
            scene.validate_timing(self.fps)
        allowed = {s.id for s in self.scenes if s.mode == 'gcp-first-frame'}
        if any(set(profile.paid_scene_ids) - allowed for profile in self.profiles.values()):
            raise ValueError('paid profile includes a protected local scene')
        if self.profiles['still-animatic'].paid_scene_ids:
            raise ValueError('still animatic cannot generate paid footage')
        if self.execution_limits != {'maxConcurrentPaid': 1, 'maxAttemptsPerShot': 1}:
            raise ValueError('this version supports one concurrent attempt and no implicit rerolls')
        return self

    @property
    def total_frames(self) -> int:
        return sum(s.timeline_frames for s in self.scenes)

    @property
    def duration_seconds(self) -> float:
        return self.total_frames / self.fps

    def missing_inputs(self, selections: dict[str, AssetSelection]) -> list[str]:
        return [s.id for s in self.scenes if s.id not in selections]


class AssetSelection(EditModel):
    path: str
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    promotional_use: bool = False
    cloud_upload: bool = False
    kind: Literal['image', 'video'] = 'image'
    source_in_seconds: float = Field(default=0, ge=0, allow_inf_nan=False)
    source_duration_seconds: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    source_fps: float | None = Field(default=None, gt=0, allow_inf_nan=False)

    def verify(self) -> Path:
        path = Path(self.path).expanduser().resolve()
        if not self.promotional_use:
            raise ContractError('promotional use must be approved for each selected asset')
        if not path.is_file() or sha256_file(path) != self.sha256:
            raise ContractError('selected asset is missing or changed; select it again')
        if self.kind == 'image':
            try:
                with Image.open(path) as image:
                    image.load()
            except (OSError, ValueError) as exc:
                raise ContractError('selected image could not be fully decoded') from exc
        return path


class MusicSegment(EditModel):
    source_in: float = Field(ge=0, allow_inf_nan=False)
    duration: float = Field(gt=0, allow_inf_nan=False)


class MusicEdit(EditModel):
    source_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    approved: bool = False
    segments: tuple[MusicSegment, ...]
    fade_in_seconds: float = Field(default=0, ge=0, le=4, allow_inf_nan=False)
    fade_out_seconds: float = Field(default=0, ge=0, le=4, allow_inf_nan=False)


def validate_music(edit: MusicEdit, *, source_duration: float, target_seconds: float) -> None:
    if not edit.approved:
        raise ContractError('the explicit music arrangement must be approved')
    if not edit.segments or abs(sum(s.duration for s in edit.segments) - target_seconds) > .00001:
        raise ContractError(f'music arrangement must be exactly {target_seconds:g} seconds')
    if any(s.source_in + s.duration > source_duration + .00001 for s in edit.segments):
        raise ContractError('music segment exceeds source duration; silence padding is prohibited')


def load_scenario(repository: Path) -> Scenario:
    value = read_json(repository / 'video-projects/hayale-flowers-promo/v3-16x4/scenario.json')
    return Scenario.model_validate(value)


def compile_hayale(
    scenario: Scenario, selections: dict[str, AssetSelection], *, profile_id: str, bucket: str,
    max_spend_usd: float | None = None,
) -> CompiledPlan:
    # Validate copies too: callers cannot bypass invariants through model_copy.
    scenario = Scenario.model_validate(scenario.model_dump())
    if scenario.missing_inputs(selections):
        raise ContractError('selected assets are missing for: ' + ', '.join(scenario.missing_inputs(selections)))
    if len({selections[s.id].sha256 for s in scenario.scenes if s.role == 'customer'}) != 3:
        raise ContractError('select three distinct customer photos for the three customer scenes')
    if not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', bucket):
        raise ContractError('invalid selected storage bucket')
    if profile_id not in scenario.profiles:
        raise ContractError('unsupported Hayale profile')
    profile = scenario.profiles[profile_id]
    model_id = 'veo-3.1-fast-generate-001'
    if scenario.pricing['modelId'] != model_id or scenario.capabilities['region'] != 'us-central1':
        raise ContractError('unsupported model or region; capability requalification required')
    generation = GenerationProfile(profile_id, model_id, profile.resolution, duration_seconds=4,
                                   person_generation='disallow', enhance_prompt=False)
    hashes = {f'{s.id}AssetSha256': selections[s.id].sha256 for s in scenario.scenes}
    hashes['scenarioSha256'] = sha256_json(scenario.model_dump(mode='json', by_alias=True))
    shots: list[CompiledShot] = []
    slots: list[dict[str, Any]] = []
    rate = float(scenario.pricing['videoOnlyRate'][profile.resolution])
    total_cost = 0.0
    generated_seconds = 0
    for i, scene in enumerate(scenario.scenes):
        asset = selections[scene.id]
        path = asset.verify()
        paid = scene.id in profile.paid_scene_ids and asset.kind != 'video'
        if paid and not asset.cloud_upload:
            raise ContractError(f'{scene.id}: explicit cloud upload permission is missing')
        source_seconds = scene.source_seconds if paid else 4
        cost = round(source_seconds * rate, 4) if paid else 0.0
        total_cost += cost
        generated_seconds += source_seconds if paid else 0
        reference = compile_reference_image(
            path=path, asset_id=scene.id, gcs_bucket=bucket, storage_prefix='hayale-v3',
            project_id=scenario.project_id, source_kind='operator-selected-cloud-approved',
        ) if paid else None
        prompt = ' '.join((scene.start, scene.movement, scene.reveal, scene.finish,
                           'Preserve the selected authentic flowers and product identity. No people, speech or text.'))
        shots.append(CompiledShot(
            shot_id=scene.id, chapter_id=f'scene-{i+1:02}', order=i+1, title=scene.title,
            duration_seconds=source_seconds, prompt=prompt,
            negative_prompt='No invented people, endorsements, lettering, signs, deformed flowers or unstable morphing.',
            seed=20260922+i, model_id=model_id, resolution=profile.resolution, aspect_ratio='16:9',
            sample_count=1, generate_audio=False, enhance_prompt=False, compression_quality='optimized',
            person_generation='disallow', storage_uri=f'gs://{bucket}/hayale-v3/{scene.id}/',
            required=True, estimated_cost_usd=cost, source_section_hints=(), review_notes=(),
            continuation_mode='first-frame-reference' if paid else 'local-composition',
            first_frame_reference=reference,
        ))
        if asset.kind == 'video' and (
            asset.source_duration_seconds is None or asset.source_fps is None
            or asset.source_in_seconds + 4 > asset.source_duration_seconds
        ):
            raise ContractError('retained video requires a verified source duration, fps and valid trim')
        slots.append({
            'shotId': scene.id, 'role': scene.role, 'startFrame': i*scene.timeline_frames,
            'timelineFrames': scene.timeline_frames, 'sourceSeconds': source_seconds,
            'sourceFps': scene.source_fps if paid else (asset.source_fps or 24),
            'sourceInSeconds': scene.source_in_seconds if paid else asset.source_in_seconds,
            'assetSha256': asset.sha256, 'mode': 'gcp-first-frame' if paid else 'local-composition',
            'inputKind': asset.kind, 'localMotion': scene.local_motion if profile.local_motion else 'hold',
            'transition': 'cut', 'promotionalUse': asset.promotional_use,
            'cloudUpload': asset.cloud_upload if paid else False,
        })
    total_cost = round(total_cost, 4)
    cap = total_cost if max_spend_usd is None else max_spend_usd
    if cap < total_cost or cap < 0 or not math.isfinite(cap):
        raise ContractError('spending cap must cover this bounded batch estimate')
    return CompiledPlan(
        schema_version='1.0.0', project_id=scenario.project_id, title=scenario.title,
        profile=generation, shots=tuple(shots), base_estimated_cost_usd=total_cost,
        conservative_estimated_cost_usd=total_cost, max_spend_usd=cap, source_artifacts=hashes,
        pricing_snapshot_date=str(scenario.pricing['checkedOn']), rate_usd_per_output_second=rate,
        continuity={
            'masterSeed': 20260922, 'seedLocked': True,
            'executionPolicy': 'foreground-prepared-single-owner',
            'executionLimits': scenario.execution_limits,
            'hayaleEdit': {'version': scenario.version, 'profile': profile_id, 'fps': scenario.fps,
                          'totalFrames': scenario.total_frames, 'editedSeconds': scenario.duration_seconds,
                          'generatedSeconds': generated_seconds, 'sampleCount': 1, 'scenes': slots,
                          'pricingSource': scenario.pricing['source'],
                          'storageTransferAssumption': scenario.pricing['storageTransfer'],
                          'creativeApproval': 'pending', 'autoPromote': False},
        },
    ).with_digest()
