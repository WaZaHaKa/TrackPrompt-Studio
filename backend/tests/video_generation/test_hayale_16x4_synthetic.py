"""Real local media acceptance; all inputs are generated synthetic signals."""
import shutil
import wave
from pathlib import Path

import numpy as np
from PIL import Image

from app.video_generation.hayale_finishing import finish_edit, render_local_scene
from app.video_generation.hayale_plan import (
    AssetSelection,
    MusicEdit,
    MusicSegment,
    compile_hayale,
    load_scenario,
)
from app.video_generation.jsonio import sha256_file


def test_real_synthetic_64_second_1080p_export(tmp_path, monkeypatch):
    repo = Path(__file__).resolve().parents[3]
    scenario = load_scenario(repo)
    profile = scenario.profiles['still-animatic'].model_copy(update={'resolution': '1080p'})
    scenario = scenario.model_copy(update={'profiles': {**scenario.profiles, 'still-animatic': profile}})
    assets = {}
    for i, scene in enumerate(scenario.scenes):
        path = tmp_path / f'synthetic-{i}.png'
        Image.new('RGB', (160, 90), (20+i*5, 60+i*2, 90)).save(path)
        assets[scene.id] = AssetSelection(path=str(path), sha256=sha256_file(path), promotional_use=True)
    plan = compile_hayale(scenario, assets, profile_id='still-animatic', bucket='synthetic-bucket')
    clips = {}
    for slot in plan.continuity['hayaleEdit']['scenes']:
        target = tmp_path / (slot['shotId']+'.mp4')
        render_local_scene(assets[slot['shotId']], slot, target, width=1920, height=1080)
        clips[slot['shotId']] = target
    audio = tmp_path / 'synthetic-source.wav'
    samples = (np.sin(2*np.pi*220*np.arange(48000*64)/48000)*5000).astype('<i2')
    with wave.open(str(audio), 'wb') as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(48000)
        handle.writeframes(np.column_stack((samples,samples)).tobytes())
    source_hash = sha256_file(audio)
    result = finish_edit(plan.to_dict(), clips, music_source=audio,
                          music_edit=MusicEdit(source_sha256=source_hash, approved=True,
                                               segments=(MusicSegment(source_in=0,duration=64),)),
                          output_root=tmp_path/'export')
    assert result['frames'] == 1536 and result['durationSeconds'] == 64
    assert result['audioPresent'] and result['technicalVerified']
    assert result['creativeApproval'] == 'pending'
    assert result['music']['originalPreserved']
    assert sha256_file(audio) == source_hash
    # Safe durable acceptance artifact for this explicit synthetic test only.
    output = repo/'.trackprompt-data/hayale-16x4-implementation/synthetic-delivery'
    output.mkdir(parents=True, exist_ok=True)
    shutil.copy2(result['deliveryPath'], output/'synthetic-64s.mp4')
    from app.video_generation.jsonio import atomic_write_json
    atomic_write_json(output/'verification.json', {key: result[key] for key in
                      ('technicalVerified','creativeApproval','frames','fps','durationSeconds',
                       'width','height','audioPresent','sha256')})
