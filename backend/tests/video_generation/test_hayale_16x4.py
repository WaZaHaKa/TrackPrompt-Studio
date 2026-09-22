from pathlib import Path

import pytest
from PIL import Image

from app.video_generation.contracts import ContractError
from app.video_generation.hayale_plan import (
    AssetSelection,
    MusicEdit,
    MusicSegment,
    compile_hayale,
    load_scenario,
    validate_music,
)
from app.video_generation.jsonio import sha256_file

REPO = Path(__file__).resolve().parents[3]


def selected(tmp_path):
    scenario = load_scenario(REPO)
    result = {}
    for i, scene in enumerate(scenario.scenes):
        path = tmp_path / f'asset-{i}.png'
        Image.new('RGB', (160, 90), (20 + i, 100, 30)).save(path)
        result[scene.id] = AssetSelection(path=str(path), sha256=sha256_file(path), promotional_use=True,
                                          cloud_upload=scene.role not in {'logo', 'customer'})
    return result


def test_exact_edit_contract():
    scenario = load_scenario(REPO)
    assert len(scenario.scenes) == 16
    assert scenario.total_frames == 1536
    assert scenario.duration_seconds == 64
    assert all(s.timeline_frames == 96 and s.transition == 'cut' for s in scenario.scenes)
    assert [i for i, s in enumerate(scenario.scenes) if s.role == 'logo'] == [0, 15]
    assert sum(s.role == 'customer' for s in scenario.scenes) == 3
    assert len({s.movement for s in scenario.scenes}) == 16


def test_missing_assets_are_reported_without_provider_contact():
    scenario = load_scenario(REPO)
    assert len(scenario.missing_inputs({})) == 16
    with pytest.raises(ContractError, match='selected assets'):
        compile_hayale(scenario, {}, profile_id='final', bucket='test-bucket')


def test_preview_cost_and_upload_separation(tmp_path):
    scenario = load_scenario(REPO)
    assets = selected(tmp_path)
    still = compile_hayale(scenario, assets, profile_id='still-animatic', bucket='test-bucket')
    motion = compile_hayale(scenario, assets, profile_id='motion-preview', bucket='test-bucket')
    final = compile_hayale(scenario, assets, profile_id='final', bucket='test-bucket')
    assert still.base_estimated_cost_usd == 0
    assert all(s.first_frame_reference is None for s in still.shots)
    assert sum(s.first_frame_reference is not None for s in motion.shots) == 2
    assert sum(s.first_frame_reference is not None for s in final.shots) == 11
    assert motion.base_estimated_cost_usd == .64
    assert final.base_estimated_cost_usd == 4.4
    assert final.plan_digest != motion.plan_digest
    assert final.continuity['hayaleEdit']['totalFrames'] == 1536
    assert 'path' not in str(final.continuity['hayaleEdit'])


def test_consent_and_asset_changes_fail_closed(tmp_path):
    scenario = load_scenario(REPO)
    assets = selected(tmp_path)
    assets['shot-003'] = assets['shot-003'].model_copy(update={'cloud_upload': False})
    with pytest.raises(ContractError, match='cloud upload'):
        compile_hayale(scenario, assets, profile_id='final', bucket='test-bucket')
    compile_hayale(scenario, assets, profile_id='still-animatic', bucket='test-bucket')
    Path(assets['shot-005'].path).write_bytes(b'changed')
    with pytest.raises(ContractError, match='changed'):
        compile_hayale(scenario, assets, profile_id='still-animatic', bucket='test-bucket')


def test_requires_three_distinct_customer_photos(tmp_path):
    assets = selected(tmp_path)
    assets['shot-009'] = assets['shot-005']
    with pytest.raises(ContractError, match='three distinct customer'):
        compile_hayale(load_scenario(REPO), assets, profile_id='still-animatic', bucket='test-bucket')


def test_provider_payload_rejects_unqualified_model_resolution_and_mode(tmp_path):
    from dataclasses import replace

    from app.video_generation.gcp_veo import build_request_payload
    plan = compile_hayale(load_scenario(REPO), selected(tmp_path), profile_id='final', bucket='test-bucket')
    shot = plan.shots[2]
    for changed in (replace(shot, model_id='veo-unknown'), replace(shot, resolution='2k'),
                    replace(shot, continuation_mode='reference-images')):
        with pytest.raises(ContractError, match='unsupported|qualified'):
            build_request_payload(changed)


def test_longer_source_is_trimmed_not_retimed(tmp_path):
    scenario = load_scenario(REPO)
    scene = scenario.scenes[2].model_copy(update={'source_seconds': 8, 'source_in_seconds': 2})
    scenario = scenario.model_copy(update={'scenes': (*scenario.scenes[:2], scene, *scenario.scenes[3:])})
    plan = compile_hayale(scenario, selected(tmp_path), profile_id='final', bucket='test-bucket')
    assert plan.shots[2].duration_seconds == 8
    slot = plan.continuity['hayaleEdit']['scenes'][2]
    assert slot['sourceInSeconds'] == 2 and slot['timelineFrames'] == 96
    assert plan.base_estimated_cost_usd == 4.8
    bad = scene.model_copy(update={'source_in_seconds': 5})
    with pytest.raises(ContractError, match='trim'):
        bad.validate_timing(24)


def test_music_requires_explicit_64_second_arrangement():
    short = MusicEdit(source_sha256='a'*64, approved=True,
                      segments=(MusicSegment(source_in=0, duration=60),))
    with pytest.raises(ContractError, match='64'):
        validate_music(short, source_duration=60, target_seconds=64)
    arranged = short.model_copy(update={'segments': (MusicSegment(source_in=0, duration=56),
                                                    MusicSegment(source_in=48, duration=8))})
    validate_music(arranged, source_duration=60, target_seconds=64)
    with pytest.raises(ContractError, match='approved'):
        validate_music(arranged.model_copy(update={'approved': False}), source_duration=60, target_seconds=64)
