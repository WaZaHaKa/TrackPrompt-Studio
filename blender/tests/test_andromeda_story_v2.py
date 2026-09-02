from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from trackprompt_visualizer import andromeda_story_v2 as story_module
from trackprompt_visualizer import mcp_entrypoints
from trackprompt_visualizer.andromeda_story_v2 import (
    ANDROMEDA_V2_BUILDER_ID,
    ANDROMEDA_V2_FRAME_END,
    ANDROMEDA_V2_FRAME_START,
    ANDROMEDA_V2_SHOT_COUNT,
    ANIMATIC_FAST_MODE,
    MASTER_MODE,
    _animate_shot_story_visibility,
    _apply_and_read_setting,
    _arguments,
    _bounded_lighting_plan,
    _intentional_cut_frames,
    _normalize_animation_interpolation,
    _protagonist_transform,
    _story_animation_plan,
    build_and_save_andromeda_v2_master,
    build_andromeda_v2_scene_spec,
    build_protagonist_component_story_plan,
    build_shot_story_action_plan,
    configure_render_mode,
    load_and_validate_visual_cues,
    load_andromeda_v2_source_contracts,
)
from trackprompt_visualizer.composition_profiles import (
    HORIZONTAL_VARIANT_ID,
    VERTICAL_VARIANT_ID,
    all_authored_composition_profiles,
    authored_composition_profile,
    resolve_shot_compositions,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _cue_payload() -> dict[str, object]:
    curve = {
        "interpolation": "linear",
        "pointFormat": ["frame", "value"],
        "smoothing": {
            "method": "asymmetric-exponential",
            "attackSeconds": 0.08,
            "releaseSeconds": 0.35,
        },
        "points": [
            [ANDROMEDA_V2_FRAME_START, 0.0],
            [ANDROMEDA_V2_FRAME_END, 1.0],
        ],
    }
    return {
        "schemaVersion": "1.1.0",
        "timeline": {
            "durationSeconds": ANDROMEDA_V2_FRAME_END / 30,
            "fps": 30,
            "frameStart": ANDROMEDA_V2_FRAME_START,
            "frameEnd": ANDROMEDA_V2_FRAME_END,
        },
        "curves": {
            name: json.loads(json.dumps(curve))
            for name in (
                "brightness",
                "masterEnergy",
                "transientActivity",
                "bassEnergy",
            )
        },
    }


def test_scene_spec_is_deterministic_complete_and_not_authorized() -> None:
    first = build_andromeda_v2_scene_spec(REPOSITORY_ROOT)
    second = build_andromeda_v2_scene_spec(REPOSITORY_ROOT)
    assert first == second
    assert first["frameStart"] == ANDROMEDA_V2_FRAME_START
    assert first["frameEnd"] == ANDROMEDA_V2_FRAME_END
    assert first["builderId"] == ANDROMEDA_V2_BUILDER_ID
    assert first["shotCount"] == ANDROMEDA_V2_SHOT_COUNT
    assert first["actOrder"] == [
        "signal",
        "awakening",
        "departure",
        "gates",
        "rupture",
        "transformation",
        "arrival",
    ]
    assert first["verticalEnabledByDefault"] is False
    assert first["productionAuthorized"] is False
    assert first["renderStarted"] is False
    assert len(first["complexityByShot"]) == ANDROMEDA_V2_SHOT_COUNT
    assert len(first["compositionProfiles"]) == 14
    assert first["storyActionCount"] == ANDROMEDA_V2_SHOT_COUNT
    assert len(first["storyActionPlanSha256"]) == 64
    assert len(first["canonicalSha256"]) == 64


def test_every_authored_story_field_drives_a_deterministic_action() -> None:
    shots = load_andromeda_v2_source_contracts(REPOSITORY_ROOT)["shots"]["shots"]
    first = build_shot_story_action_plan(shots)
    second = build_shot_story_action_plan(shots)
    assert first == second
    assert len(first["shotActions"]) == ANDROMEDA_V2_SHOT_COUNT
    assert len(
        {
            action["actionSignatureSha256"]
            for action in first["shotActions"]
        }
    ) == ANDROMEDA_V2_SHOT_COUNT

    by_sequence = {
        action["sequence"]: action for action in first["shotActions"]
    }
    assert by_sequence[23]["actionFamily"] == "fracture-impact"
    assert by_sequence[27]["actionFamily"] == "component-release"
    assert by_sequence[28]["actionFamily"] == "route-repair"
    assert by_sequence[29]["actionFamily"] == "aperture-rebirth"
    assert by_sequence[30]["actionFamily"] == "transformed-release"
    assert by_sequence[35]["actionFamily"] == "arrival-settle"
    for shot, action in zip(shots, first["shotActions"], strict=True):
        assert action["storyPurpose"] == shot["storyPurpose"]
        assert action["secondaryNarrativeAction"] == (
            shot["secondaryNarrativeAction"]
        )
        assert action["dominantShape"]["id"] == shot["dominantShape"]
        assert [
            landmark["id"] for landmark in action["requiredLandmarks"]
        ] == shot["requiredLandmarks"]
        assert len(action["controllerStates"]) == 3

    purpose_changed = json.loads(json.dumps(shots))
    purpose_changed[0]["storyPurpose"] += " Purpose-specific physical beat."
    secondary_changed = json.loads(json.dumps(shots))
    secondary_changed[0]["secondaryNarrativeAction"] += " Secondary beat."
    dominant_changed = json.loads(json.dumps(shots))
    dominant_changed[0]["dominantShape"] = "nested-ring-purpose-test"
    landmark_changed = json.loads(json.dumps(shots))
    landmark_changed[0]["requiredLandmarks"][0] = "crystal-purpose-test"

    baseline = first["shotActions"][0]
    assert (
        build_shot_story_action_plan(purpose_changed)["shotActions"][0][
            "actionSignatureSha256"
        ]
        != baseline["actionSignatureSha256"]
    )
    assert (
        build_shot_story_action_plan(secondary_changed)["shotActions"][0][
            "actionSignatureSha256"
        ]
        != baseline["actionSignatureSha256"]
    )
    assert (
        build_shot_story_action_plan(dominant_changed)["shotActions"][0][
            "dominantShape"
        ]["geometryKind"]
        == "ring"
    )
    assert (
        build_shot_story_action_plan(landmark_changed)["shotActions"][0][
            "requiredLandmarks"
        ][0]["id"]
        == "crystal-purpose-test"
    )


def test_component_damage_and_transformation_persist_through_arrival() -> None:
    shots = load_andromeda_v2_source_contracts(REPOSITORY_ROOT)["shots"]["shots"]
    plan = build_protagonist_component_story_plan(shots)
    assert plan["damageBeginsFrame"] == shots[22]["frameStart"]
    assert plan["damagePersistsUntilFrame"] == shots[26]["frameEnd"]
    assert plan["transformationCompletesFrame"] == shots[29]["frameEnd"]
    assert plan["transformationPersistsThroughFrame"] == ANDROMEDA_V2_FRAME_END

    components = plan["components"]
    assert components["damaged-armor-plate"][-1] == {
        "frame": ANDROMEDA_V2_FRAME_END,
        "stage": "removed-through-arrival",
        "scaleMultiplier": (0.001, 0.001, 0.001),
        "locationOffset": (2.42, 1.24, 1.46),
        "rotationOffset": (1.42, 0.92, 1.34),
    }
    assert components["damaged-crystal-route"][-1]["stage"] == (
        "retired-through-arrival"
    )
    assert components["repaired-crystal-bridge"][-1]["stage"] == (
        "arrival-functional-route"
    )
    assert components["front-aperture"][-1]["stage"] == (
        "protected-arrival-aperture"
    )
    for component_id in ("transformed-fin-left", "transformed-fin-right"):
        assert components[component_id][0]["scaleMultiplier"] == (
            0.001,
            0.001,
            0.001,
        )
        assert components[component_id][-1]["frame"] == ANDROMEDA_V2_FRAME_END
        assert components[component_id][-1]["scaleMultiplier"] == (
            1.0,
            1.0,
            1.0,
        )

    transformed = _protagonist_transform(
        frame=shots[29]["frameEnd"],
        location=(0.0, 0.0, 0.0),
        sequence=30,
        protagonist_state="transformed",
        midpoint=False,
    )
    arrived = _protagonist_transform(
        frame=ANDROMEDA_V2_FRAME_END,
        location=(0.0, 0.0, 0.0),
        sequence=35,
        protagonist_state="arrived",
        midpoint=False,
    )
    assert transformed["scale"] == (1.08, 1.16, 0.98)
    assert arrived["scale"] == transformed["scale"]


def test_mcp_entrypoint_builds_v2_without_starting_a_render(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "andromeda-v2-mcp.blend"
    captured: dict[str, object] = {}

    def fake_builder(
        repository_root: Path,
        output_blend: Path,
        **kwargs: object,
    ) -> dict[str, object]:
        captured["repositoryRoot"] = repository_root
        captured["outputBlend"] = output_blend
        captured.update(kwargs)
        return {
            "ok": True,
            "builderId": ANDROMEDA_V2_BUILDER_ID,
            "renderStarted": False,
        }

    monkeypatch.setattr(
        story_module,
        "build_and_save_andromeda_v2_master",
        fake_builder,
    )
    result = mcp_entrypoints.build_andromeda_v2_master_scene(
        str(REPOSITORY_ROOT),
        str(output),
        composition_id=VERTICAL_VARIANT_ID,
        render_mode=ANIMATIC_FAST_MODE,
    )
    assert result == {
        "ok": True,
        "builderId": ANDROMEDA_V2_BUILDER_ID,
        "renderStarted": False,
    }
    assert captured["repositoryRoot"] == REPOSITORY_ROOT.resolve()
    assert captured["outputBlend"] == output.resolve()
    assert captured["composition_id"] == VERTICAL_VARIANT_ID
    assert captured["render_mode"] == ANIMATIC_FAST_MODE
    assert captured["audio_path"] is None
    assert captured["visual_cues_path"] is None

    invalid = mcp_entrypoints.build_andromeda_v2_master_scene(
        "relative-repository",
        str(tmp_path / "invalid.blend"),
    )
    assert invalid == {
        "ok": False,
        "error": {
            "code": "validation_failed",
            "message": "Repository root path must be absolute.",
        },
    }


def test_compositions_are_native_independent_and_protect_landmarks() -> None:
    profiles = all_authored_composition_profiles()
    assert len({profile["profileId"] for profile in profiles}) == 14
    for act_id in (
        "signal",
        "awakening",
        "departure",
        "gates",
        "rupture",
        "transformation",
        "arrival",
    ):
        horizontal = authored_composition_profile(HORIZONTAL_VARIANT_ID, act_id)
        vertical = authored_composition_profile(VERTICAL_VARIANT_ID, act_id)
        assert (horizontal["width"], horizontal["height"]) == (1920, 1080)
        assert (vertical["width"], vertical["height"]) == (1080, 1920)
        assert horizontal["profileId"] != vertical["profileId"]
        assert horizontal["compositionMode"] == "authored"
        assert vertical["compositionMode"] == "authored"
        assert horizontal["deliverableRole"] == "primary-master"
        assert vertical["deliverableRole"] == "optional-social"
        assert horizontal["cameraName"] != vertical["cameraName"]
        assert horizontal["safeZone"] != vertical["safeZone"]
        assert horizontal["subjectAnchor"] != vertical["subjectAnchor"]
        assert horizontal["landmarkAnchor"] != vertical["landmarkAnchor"]
        assert horizontal["canonicalSha256"] != vertical["canonicalSha256"]
        assert horizontal["cropPolicy"] == "native-authored-never-crop"
        assert vertical["cropPolicy"] == "native-authored-never-crop"
        assert "front-aperture" in horizontal["occlusion"]["protectedRegions"]
        assert "primary-story-landmark" in vertical["occlusion"]["protectedRegions"]


def test_every_shot_resolves_both_profiles_safe_zones_and_landmarks() -> None:
    contracts = load_andromeda_v2_source_contracts(REPOSITORY_ROOT)
    shots = contracts["shots"]["shots"]
    for shot in shots:
        resolved = resolve_shot_compositions(shot)
        assert set(resolved) == {HORIZONTAL_VARIANT_ID, VERTICAL_VARIANT_ID}
        assert resolved[HORIZONTAL_VARIANT_ID]["safeZone"]
        assert resolved[VERTICAL_VARIANT_ID]["safeZone"]
        assert resolved[HORIZONTAL_VARIANT_ID]["independentlyAuthored"] is True
        assert resolved[VERTICAL_VARIANT_ID]["independentlyAuthored"] is True
        assert resolved[HORIZONTAL_VARIANT_ID]["derivedByCrop"] is False
        assert resolved[VERTICAL_VARIANT_ID]["derivedByCrop"] is False
        assert (
            resolved[HORIZONTAL_VARIANT_ID]["cameraRigId"]
            != resolved[VERTICAL_VARIANT_ID]["cameraRigId"]
        )
        assert (
            resolved[HORIZONTAL_VARIANT_ID]["lensMm"]
            != resolved[VERTICAL_VARIANT_ID]["lensMm"]
        )
        assert (
            resolved[HORIZONTAL_VARIANT_ID]["subjectOccupancyFraction"]
            != resolved[VERTICAL_VARIANT_ID]["subjectOccupancyFraction"]
        )
        assert resolved[HORIZONTAL_VARIANT_ID]["requiredLandmarks"] == shot["requiredLandmarks"]
        assert resolved[VERTICAL_VARIANT_ID]["requiredLandmarks"] == shot["requiredLandmarks"]
        assert resolved[HORIZONTAL_VARIANT_ID]["canonicalSha256"] != (
            resolved[VERTICAL_VARIANT_ID]["canonicalSha256"]
        )


def test_final_environments_are_authored_not_future_landmarks() -> None:
    contracts = load_andromeda_v2_source_contracts(REPOSITORY_ROOT)
    final_shots = [
        shot
        for shot in contracts["shots"]["shots"]
        if shot["actId"] in {"rupture", "transformation", "arrival"}
    ]
    assert len(final_shots) == 15
    assert {shot["environmentBlueprintId"] for shot in final_shots} == {
        "andromeda-v2-broken-route-fracture-canyon",
        "andromeda-v2-reconstruction-cathedral",
        "andromeda-v2-andromeda-lattice-horizon",
    }
    serialized = json.dumps(final_shots, sort_keys=True).lower()
    assert "future" not in serialized
    assert "generic" not in serialized
    assert "placeholder" not in serialized


def test_visual_cues_require_exact_story_hash_and_smoothed_linear_curves(
    tmp_path: Path,
) -> None:
    cue_path = tmp_path / "visual-cues.json"
    cue_path.write_text(
        json.dumps(_cue_payload(), sort_keys=True),
        encoding="utf-8",
    )
    expected_sha256 = hashlib.sha256(cue_path.read_bytes()).hexdigest()
    loaded = load_and_validate_visual_cues(cue_path, expected_sha256)
    assert loaded["schemaVersion"] == "1.1.0"
    assert set(loaded["curves"]) == {
        "brightness",
        "masterEnergy",
        "transientActivity",
        "bassEnergy",
    }

    with pytest.raises(ValueError, match="SHA-256"):
        load_and_validate_visual_cues(cue_path, "0" * 64)

    invalid_payload = _cue_payload()
    invalid_payload["curves"]["brightness"]["interpolation"] = "BEZIER"  # type: ignore[index]
    invalid_path = tmp_path / "invalid-cues.json"
    invalid_path.write_text(
        json.dumps(invalid_payload, sort_keys=True),
        encoding="utf-8",
    )
    invalid_sha256 = hashlib.sha256(invalid_path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="linear"):
        load_and_validate_visual_cues(invalid_path, invalid_sha256)


def test_bounded_lighting_maps_all_features_and_caps_each_layer() -> None:
    features = (
        "smoothed-spectral-centroid",
        "smoothed-rms-energy",
        "smoothed-onset-density",
        "smoothed-bass-energy",
    )
    shot = {
        "id": "bounded-lighting-test",
        "actId": "signal",
        "frameStart": ANDROMEDA_V2_FRAME_START,
        "frameEnd": ANDROMEDA_V2_FRAME_END,
        "audioReactiveLayers": [
            {
                "sourceFeature": feature,
                "maximumInfluenceFraction": 0.20,
                "controlsMajorCameraOrProtagonistTravel": False,
            }
            for feature in features
        ],
    }
    plan = _bounded_lighting_plan([shot], _cue_payload())
    assert plan["sourceFeatureToCurve"] == {
        "smoothed-spectral-centroid": "brightness",
        "smoothed-rms-energy": "masterEnergy",
        "smoothed-onset-density": "transientActivity",
        "smoothed-bass-energy": "bassEnergy",
    }
    assert plan["maximumInfluenceFraction"] == pytest.approx(0.12)
    assert plan["maximumAppliedInfluenceFraction"] == pytest.approx(0.12)
    assert plan["controlsMajorCameraOrProtagonistTravel"] is False
    shot_plan = plan["shotPlans"]["bounded-lighting-test"]
    assert {
        layer["curve"] for layer in shot_plan["layers"]
    } == {
        "brightness",
        "masterEnergy",
        "transientActivity",
        "bassEnergy",
    }
    assert all(
        layer["appliedMaximumInfluenceFraction"] <= 0.12
        for layer in shot_plan["layers"]
    )
    factors = [
        keyframe["energyFactor"]
        for keyframe in shot_plan["keyframes"]
    ]
    assert factors == pytest.approx([0.88, 1.12])
    assert "camera" not in shot_plan
    assert "protagonist" not in shot_plan


def test_non_cut_boundaries_preserve_protagonist_and_both_camera_states() -> None:
    shots = load_andromeda_v2_source_contracts(REPOSITORY_ROOT)["shots"]["shots"]
    plan = _story_animation_plan(shots)
    assert _intentional_cut_frames(shots) == [
        1,
        1304,
        2867,
        4691,
        6776,
        8600,
        10945,
    ]
    for previous, current in zip(plan, plan[1:], strict=False):
        if current["intentionalCut"]:
            continue
        previous_protagonist = {
            key: value
            for key, value in previous["protagonist"][-1].items()
            if key != "frame"
        }
        current_protagonist = {
            key: value
            for key, value in current["protagonist"][0].items()
            if key != "frame"
        }
        assert current_protagonist == previous_protagonist
        for variant_id in (HORIZONTAL_VARIANT_ID, VERTICAL_VARIANT_ID):
            previous_camera = {
                key: value
                for key, value in previous["cameras"][variant_id][-1].items()
                if key != "frame"
            }
            current_camera = {
                key: value
                for key, value in current["cameras"][variant_id][0].items()
                if key != "frame"
            }
            assert current_camera == previous_camera


def test_animation_keyframes_are_normalized_to_linear() -> None:
    object_points = [
        SimpleNamespace(interpolation="BEZIER"),
        SimpleNamespace(interpolation="CONSTANT"),
    ]
    data_points = [SimpleNamespace(interpolation="BEZIER")]
    visibility_points = [
        SimpleNamespace(interpolation="BEZIER"),
        SimpleNamespace(interpolation="LINEAR"),
    ]
    obj = SimpleNamespace(
        animation_data=SimpleNamespace(
            action=SimpleNamespace(
                fcurves=[
                    SimpleNamespace(
                        data_path="location",
                        keyframe_points=object_points,
                    ),
                    SimpleNamespace(
                        data_path="hide_render",
                        keyframe_points=visibility_points,
                    ),
                ]
            )
        ),
        data=SimpleNamespace(
            animation_data=SimpleNamespace(
                action=SimpleNamespace(
                    fcurves=[
                        SimpleNamespace(keyframe_points=data_points),
                    ]
                )
            )
        ),
    )
    assert _normalize_animation_interpolation([obj, obj]) == 5
    assert all(
        point.interpolation == "LINEAR"
        for point in [*object_points, *data_points]
    )
    assert all(point.interpolation == "CONSTANT" for point in visibility_points)


def test_shot_story_visibility_is_exact_and_hidden_outside_the_shot() -> None:
    class FakeObject(dict[str, object]):
        def __init__(self) -> None:
            super().__init__()
            self.hide_render = False
            self.hide_viewport = False
            self.keys: list[tuple[str, int, bool]] = []

        def keyframe_insert(self, data_path: str, *, frame: int) -> None:
            self.keys.append((data_path, frame, bool(getattr(self, data_path))))

    first = FakeObject()
    middle = FakeObject()
    last = FakeObject()
    plan = {
        "shotActions": [
            {"shotId": "first", "frameStart": 1, "frameEnd": 10},
            {"shotId": "middle", "frameStart": 11, "frameEnd": 20},
            {"shotId": "last", "frameStart": 21, "frameEnd": 30},
        ]
    }
    count = _animate_shot_story_visibility(
        {"first": [first], "middle": [middle], "last": [last]},
        plan,
        scene_frame_start=1,
        scene_frame_end=30,
    )

    assert count == 24
    assert first.keys == [
        ("hide_render", 1, False),
        ("hide_viewport", 1, False),
        ("hide_render", 10, False),
        ("hide_viewport", 10, False),
        ("hide_render", 11, True),
        ("hide_viewport", 11, True),
    ]
    assert middle.keys == [
        ("hide_render", 1, True),
        ("hide_viewport", 1, True),
        ("hide_render", 10, True),
        ("hide_viewport", 10, True),
        ("hide_render", 11, False),
        ("hide_viewport", 11, False),
        ("hide_render", 20, False),
        ("hide_viewport", 20, False),
        ("hide_render", 21, True),
        ("hide_viewport", 21, True),
    ]
    assert last.keys == [
        ("hide_render", 1, True),
        ("hide_viewport", 1, True),
        ("hide_render", 20, True),
        ("hide_viewport", 20, True),
        ("hide_render", 21, False),
        ("hide_viewport", 21, False),
        ("hide_render", 30, False),
        ("hide_viewport", 30, False),
    ]
    assert middle["trackprompt_visibility_policy"] == "current-shot-only"
    assert middle["trackprompt_visibility_frame_start"] == 11
    assert middle["trackprompt_visibility_frame_end"] == 20


def test_animation_keyframes_support_blender_52_layered_actions() -> None:
    points = [
        SimpleNamespace(interpolation="BEZIER"),
        SimpleNamespace(interpolation="BEZIER"),
    ]
    channelbag = SimpleNamespace(
        fcurves=[SimpleNamespace(keyframe_points=points)]
    )
    action = SimpleNamespace(
        fcurves=[],
        layers=[
            SimpleNamespace(
                strips=[SimpleNamespace(channelbags=[channelbag])]
            )
        ],
    )
    obj = SimpleNamespace(
        animation_data=SimpleNamespace(action=action),
        data=None,
    )

    assert _normalize_animation_interpolation([obj, obj]) == 2
    assert [point.interpolation for point in points] == ["LINEAR", "LINEAR"]


class _FakeImageSettings:
    def __init__(self) -> None:
        self.file_format = ""
        self.color_mode = ""
        self.color_depth = ""


class _FakeFFmpeg:
    def __init__(self) -> None:
        self.format = ""
        self.codec = ""
        self.constant_rate_factor = ""
        self.audio_codec = ""
        self.audio_bitrate = 0
        self.audio_mixrate = 0
        self.audio_channels = ""


class _FakeRender:
    def __init__(self) -> None:
        self.engine = ""
        self.resolution_percentage = 100
        self.image_settings = _FakeImageSettings()
        self.ffmpeg = _FakeFFmpeg()
        self.use_motion_blur = True


class _FakeEevee:
    def __init__(self) -> None:
        self.taa_render_samples = 16
        self.volumetric_samples = 64
        self.use_taa_reprojection = False


class _FakeScene(dict[str, object]):
    def __init__(self, *, expose_eevee: bool = True) -> None:
        super().__init__()
        self.render = _FakeRender()
        if expose_eevee:
            self.eevee = _FakeEevee()


def test_render_modes_configure_but_never_start_rendering() -> None:
    scene = _FakeScene()
    animatic = configure_render_mode(scene, ANIMATIC_FAST_MODE)
    assert animatic["mode"] == "animatic-fast"
    assert animatic["resolutionPercentage"] == 25
    assert animatic["temporalSamples"] == 2
    assert animatic["audioCodec"] == "AAC"
    assert animatic["audioSampleRate"] == 44100
    assert animatic["audioChannels"] == "stereo"
    assert animatic["lockedSettings"]["temporalSamples"] == {
        "requested": 2,
        "applied": 2,
        "source": "scene.eevee.taa_render_samples",
        "status": "applied",
    }
    assert animatic["lockedSettings"]["motionBlur"]["applied"] is False
    assert animatic["renderStarted"] is False
    assert scene["trackprompt_animatic_audio_enabled"] is True
    assert scene["trackprompt_render_started"] is False
    master = configure_render_mode(scene, MASTER_MODE)
    assert master["resolutionPercentage"] == 100
    assert master["temporalSamples"] == 64
    assert master["volumetricSamples"] == 32
    assert master["reprojection"] is True
    assert master["motionBlur"] is False
    assert master["lockedSettingsSatisfied"] is True
    assert master["lockedSettings"]["volumetricSamples"]["source"] == (
        "scene.eevee.volumetric_samples"
    )
    assert master["lockedSettings"]["reprojection"]["source"] == (
        "scene.eevee.use_taa_reprojection"
    )
    assert master["audioCodec"] is None
    assert master["renderStarted"] is False

    builder_source = inspect.getsource(build_and_save_andromeda_v2_master)
    assert "bpy.ops.render" not in builder_source
    assert "save_as_mainfile" in builder_source
    assert '"builderSourceSha256": builder_source_sha256' in builder_source
    assert "trackprompt_builder_source_sha256" in builder_source


def test_master_render_mode_fails_closed_when_locked_settings_are_unavailable() -> None:
    with pytest.raises(RuntimeError, match="cannot be locked"):
        configure_render_mode(_FakeScene(expose_eevee=False), MASTER_MODE)

    animatic = configure_render_mode(
        _FakeScene(expose_eevee=False),
        ANIMATIC_FAST_MODE,
    )
    assert animatic["temporalSamples"] is None
    assert animatic["lockedSettings"]["temporalSamples"]["status"] == "unavailable"
    assert animatic["lockedSettingsSatisfied"] is False


def test_read_only_matching_render_default_is_recorded_truthfully() -> None:
    class _ReadOnlySetting:
        @property
        def samples(self) -> int:
            return 64

        @samples.setter
        def samples(self, _value: int) -> None:
            raise TypeError("read-only")

    receipt = _apply_and_read_setting(
        [(_ReadOnlySetting(), "samples", "scene.fixed.samples")],
        64,
    )
    assert receipt == {
        "requested": 64,
        "applied": 64,
        "source": "scene.fixed.samples",
        "status": "introspected-default",
    }


def test_cli_accepts_optional_visual_cues_path() -> None:
    args = _arguments(
        [
            "--repository-root",
            str(REPOSITORY_ROOT),
            "--output",
            str(REPOSITORY_ROOT / "ignored.blend"),
            "--visual-cues",
            str(REPOSITORY_ROOT / "private-visual-cues.json"),
        ]
    )
    assert args.visual_cues == str(REPOSITORY_ROOT / "private-visual-cues.json")


def test_unknown_composition_or_render_mode_fails_closed() -> None:
    with pytest.raises(ValueError, match="unknown"):
        authored_composition_profile("square", "signal")
    with pytest.raises(ValueError, match="unknown"):
        authored_composition_profile(HORIZONTAL_VARIANT_ID, "epilogue")
    with pytest.raises(ValueError, match="render mode"):
        configure_render_mode(_FakeScene(), "production-now")
