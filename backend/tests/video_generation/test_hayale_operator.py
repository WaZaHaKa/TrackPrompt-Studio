import argparse
from dataclasses import replace
from pathlib import Path

import pytest

from app.video_generation import hayale_operator as operator
from app.video_generation.contracts import ContractError
from app.video_generation.jsonio import atomic_write_json, sha256_json
from tests.video_generation.test_prepared_video_plan import controller_at, prepared_plan


def test_worker_reconstructs_bound_environment_before_store_creation(tmp_path, monkeypatch):
    monkeypatch.setattr(operator, 'repository', lambda: tmp_path)
    job_id, nonce = 'a'*36, 'b'*32
    state = tmp_path/'runtime/mission-control'
    environment = {'TRACKPROMPT_MC_STATE_ROOT': str(state), 'TRACKPROMPT_GCP_AUTH_CONFIG': str(tmp_path/'auth/auth.json'),
                   'TRACKPROMPT_MC_FFMPEG_PATH': 'ffmpeg', 'TRACKPROMPT_MC_FFPROBE_PATH': 'ffprobe',
                   'TRACKPROMPT_WNP_ROOT': '', 'TRACKPROMPT_WNP_RUN': ''}
    intent = {'jobId': job_id, 'nonce': nonce, 'planDigest': 'c'*64,
              'binding': {'environment': environment, 'python': str(Path(operator.sys.executable).resolve())}}
    intent['bindingDigest'] = sha256_json(intent['binding'])
    path = tmp_path/'runtime/hayale-16x4/workers'/job_id/nonce/'intent.json'
    atomic_write_json(path, intent)
    for key in environment:
        monkeypatch.setenv(key, '')
    monkeypatch.delenv('TRACKPROMPT_MC_STATE_ROOT', raising=False)
    monkeypatch.setenv('TRACKPROMPT_WNP_ROOT', 'wrong-node')
    args = argparse.Namespace(job_id=job_id, nonce=nonce, worker_intent=str(path))
    operator.restore_worker_binding(args)
    assert operator.os.environ['TRACKPROMPT_MC_STATE_ROOT'] == str(state)
    assert 'TRACKPROMPT_WNP_ROOT' not in operator.os.environ


@pytest.mark.asyncio
async def test_missing_local_clip_blocks_launch_before_provider(tmp_path):
    plan, refs = prepared_plan(tmp_path)
    local = replace(plan.shots[0], shot_id='shot-002', order=2, continuation_mode='local-composition',
                    first_frame_reference=None, estimated_cost_usd=0)
    plan = replace(plan, shots=(*plan.shots, local)).with_digest()
    service, store = controller_at(tmp_path)
    view = await service.create_prepared_plan(plan, gcp_project_id='test-project', gcs_bucket='test-bucket', reference_paths=refs)
    with pytest.raises(ContractError, match='local composition.*Prepare'):
        operator.require_local_scenes(store.get_video_job(view.job_id))
    from app.video_generation.mission_models import VideoAuthorizationRequest
    job = store.get_video_job(view.job_id)
    changed = {**job.plan, 'continuity': {**job.plan['continuity'], 'hayaleEdit': {'version': 'test'}}}
    store.put_video_job(job.model_copy(update={'plan': changed}))
    await service.authorize(view.job_id, VideoAuthorizationRequest(confirmation=view.authorization_phrase))
    with pytest.raises(ContractError, match='local composition.*Prepare'):
        await service.start(view.job_id)
