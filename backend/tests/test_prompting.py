from __future__ import annotations

import pytest

from app.analysis.core import feature
from app.editing import PatchError, apply_analysis_patch
from app.exports import analysis_json_export, analysis_markdown_export
from app.prompting import compose_prompt
from app.schemas import (
    AnalysisPatch,
    Confidence,
    FeatureUpdate,
    InstrumentCandidate,
    PromptLength,
    PromptPreferences,
    SectionStemEvidence,
)


def _prompt_ready(analysis):
    prepared = analysis.model_copy(deep=True)
    prepared.style_and_mood.broad_style = feature(
        ["electronic rock"], Confidence.HIGH, "test tagger"
    )
    prepared.style_and_mood.mood = feature(["tense"], Confidence.HIGH, "test tagger")
    prepared.style_and_mood.energy = feature("high", Confidence.HIGH, "test tagger")
    prepared.instrumentation.candidates = feature(
        [
            InstrumentCandidate(name="drums", prominence="prominent", confidence=Confidence.HIGH),
            InstrumentCandidate(name="synth pads", prominence="present", confidence=Confidence.MEDIUM),
        ],
        Confidence.HIGH,
        "test tagger",
    )
    prepared.production.production_character = feature(
        ["polished", "textured"], Confidence.HIGH, "test rules"
    )
    return prepared


def test_prompt_is_stable_and_has_originality_clause(click_analysis) -> None:
    analysis = _prompt_ready(click_analysis)
    preferences = PromptPreferences()
    first = compose_prompt(analysis, preferences)
    second = compose_prompt(analysis, preferences)
    assert first == second
    assert "original melody, arrangement, and any lyrics" in first.primary_prompt
    assert "secret-source-name" not in first.primary_prompt
    assert "Private Artist" not in first.primary_prompt
    assert "Private Title" not in first.primary_prompt


def test_seeded_variation_is_stable_and_changes_wording(click_analysis) -> None:
    analysis = _prompt_ready(click_analysis)
    one = compose_prompt(analysis, PromptPreferences(variation_seed=1))
    one_again = compose_prompt(analysis, PromptPreferences(variation_seed=1))
    two = compose_prompt(analysis, PromptPreferences(variation_seed=2))
    assert one.primary_prompt == one_again.primary_prompt
    assert one.primary_prompt != two.primary_prompt


def test_low_confidence_fact_is_omitted(click_analysis) -> None:
    analysis = _prompt_ready(click_analysis)
    analysis.style_and_mood.mood = feature(["melancholic"], Confidence.LOW, "weak heuristic")
    package = compose_prompt(analysis, PromptPreferences())
    assert "melancholic" not in package.primary_prompt
    assert any(item.path == "styleAndMood.mood" for item in package.facts_omitted)


def test_user_edited_artist_like_value_is_still_rejected(click_analysis) -> None:
    analysis = _prompt_ready(click_analysis)
    analysis.style_and_mood.broad_style = feature(
        ["Taylor Swift"], Confidence.LOW, "user edit"
    )
    analysis.style_and_mood.broad_style.user_edited = True
    package = compose_prompt(analysis, PromptPreferences())
    assert "Taylor Swift" not in package.primary_prompt


def test_lowercase_named_artist_override_is_rejected(click_analysis) -> None:
    analysis = _prompt_ready(click_analysis)
    package = compose_prompt(analysis, PromptPreferences(target_genre="taylor swift"))
    assert "taylor swift" not in package.primary_prompt.lower()
    assert package.warnings


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "dark taylor swift",
        "dark madonna",
        "make a TaylorSwift track",
        "legato organ taylor swift",
    ],
)
def test_named_artist_cannot_hide_beside_safe_words(click_analysis, unsafe_text: str) -> None:
    analysis = _prompt_ready(click_analysis)
    package = compose_prompt(analysis, PromptPreferences(target_genre=unsafe_text))
    assert unsafe_text.casefold() not in package.primary_prompt.casefold()
    assert package.warnings


def test_documented_safe_user_phrases_are_retained(click_analysis) -> None:
    analysis = _prompt_ready(click_analysis)
    package = compose_prompt(
        analysis,
        PromptPreferences(
            target_mood="layered chorus",
            desired_vocal_presentation="raspy nasal vibrato",
            exclusions=[
                "overly bright mastering",
                "No vocal chops",
                "Avoid dense orchestration",
                "No abrupt ending",
            ],
        ),
    )
    assert "layered chorus" in package.detailed_prompt
    assert "raspy nasal vibrato" in package.detailed_prompt
    assert package.exclusions == [
        "overly bright mastering",
        "No vocal chops",
        "Avoid dense orchestration",
        "No abrupt ending",
    ]


@pytest.mark.parametrize("target_genre", ["dubstep", "bluegrass", "shoegaze"])
def test_legitimate_target_genres_are_retained(click_analysis, target_genre: str) -> None:
    analysis = _prompt_ready(click_analysis)
    package = compose_prompt(analysis, PromptPreferences(target_genre=target_genre))
    assert target_genre in package.detailed_prompt
    assert not any("target-genre" in warning for warning in package.warnings)


@pytest.mark.parametrize(
    "vocal_presentation",
    [
        "legato versus staccato phrasing",
        "melismatic tendency",
        "vocal density",
    ],
)
def test_legitimate_vocal_phrases_are_retained(
    click_analysis,
    vocal_presentation: str,
) -> None:
    analysis = _prompt_ready(click_analysis)
    package = compose_prompt(
        analysis,
        PromptPreferences(desired_vocal_presentation=vocal_presentation),
    )
    assert vocal_presentation in package.detailed_prompt
    assert not any("vocal preference" in warning for warning in package.warnings)


def test_source_identity_in_analyzer_value_is_rejected(click_analysis) -> None:
    analysis = _prompt_ready(click_analysis)
    analysis.style_and_mood.broad_style = feature(
        [analysis.file.display_name], Confidence.HIGH, "compromised tagger"
    )
    package = compose_prompt(analysis, PromptPreferences())
    assert analysis.file.display_name not in package.primary_prompt


def test_generic_musical_filename_stem_does_not_erase_target_genre(click_analysis) -> None:
    analysis = _prompt_ready(click_analysis)
    analysis.file.display_name = "rock.wav"
    package = compose_prompt(
        analysis,
        PromptPreferences(generation_intent="genre_transfer", target_genre="rock"),
    )
    assert "rock" in package.detailed_prompt
    assert "Translate the groove" in package.detailed_prompt


def test_distinctive_filename_stem_remains_a_source_identity_blocker(click_analysis) -> None:
    analysis = _prompt_ready(click_analysis)
    analysis.file.display_name = "unique-source-identity.wav"
    analysis.style_and_mood.broad_style = feature(
        ["unique-source-identity"], Confidence.HIGH, "compromised tagger"
    )
    package = compose_prompt(analysis, PromptPreferences())
    assert "unique-source-identity" not in package.detailed_prompt


def test_unknown_lyric_override_is_never_read_by_base_composer(click_analysis) -> None:
    analysis = _prompt_ready(click_analysis)
    package = compose_prompt(
        analysis,
        PromptPreferences(user_overrides={"lyrics": "quoted secret lyric"}),
    )
    assert "quoted secret lyric" not in package.primary_prompt


def test_length_budget_preserves_whole_originality_clause_and_salience(click_analysis) -> None:
    analysis = _prompt_ready(click_analysis)
    package = compose_prompt(
        analysis,
        PromptPreferences(
            prompt_length=PromptLength.CUSTOM,
            custom_max_characters=240,
            instrumental=True,
        ),
    )
    assert len(package.primary_prompt) <= 240
    assert "fully instrumental" in package.primary_prompt
    assert package.primary_prompt.endswith("reference recording.")


def test_cross_group_contradiction_prefers_target_mood(click_analysis) -> None:
    analysis = _prompt_ready(click_analysis)
    analysis.production.production_character = feature(
        ["dark", "polished"], Confidence.HIGH, "test rules"
    )
    package = compose_prompt(analysis, PromptPreferences(target_mood="bright"))
    lowered = package.primary_prompt.lower()
    assert "bright" in lowered
    assert "dark" not in lowered


def test_exclusions_are_deduplicated_and_named_style_is_rejected(click_analysis) -> None:
    analysis = _prompt_ready(click_analysis)
    package = compose_prompt(
        analysis,
        PromptPreferences(
            exclusions=[
                "No vocal chops",
                "no vocal chops",
                "in the style of Private Artist",
                analysis.file.display_name,
            ]
        ),
    )
    assert package.exclusions == ["No vocal chops"]
    assert package.warnings


def test_creativity_has_deterministic_effect(click_analysis) -> None:
    analysis = _prompt_ready(click_analysis)
    restrained = compose_prompt(analysis, PromptPreferences(creativity=0.1))
    adventurous = compose_prompt(analysis, PromptPreferences(creativity=0.9))
    assert "focused and controlled" in restrained.detailed_prompt
    assert "bolder textural turns" in adventurous.detailed_prompt


def test_instrumental_intent_cannot_emit_vocal_delivery(click_analysis) -> None:
    analysis = _prompt_ready(click_analysis)
    preferences = PromptPreferences(
        generation_intent="instrumental_reinterpretation",
        instrumental=False,
        desired_vocal_presentation="breathy layered lead",
    )
    assert preferences.instrumental is True
    package = compose_prompt(
        analysis,
        preferences,
    )
    lowered = package.detailed_prompt.lower()
    assert "fully instrumental" in lowered
    assert "no vocals" in lowered
    assert "breathy layered lead" not in lowered


def test_low_confidence_fact_can_be_accepted_and_restore_clears_acceptance(
    click_analysis,
) -> None:
    analysis = _prompt_ready(click_analysis)
    analysis.rhythm.bpm = feature(123.0, Confidence.LOW, "ambiguous detector")
    detected = analysis.model_copy(deep=True)
    accepted = apply_analysis_patch(
        analysis,
        detected,
        AnalysisPatch(
            updates=[FeatureUpdate(path="rhythm.bpm", accepted_for_prompt=True)]
        ),
    )
    assert accepted.rhythm.bpm.user_accepted is True
    assert "123 BPM" in compose_prompt(accepted, PromptPreferences()).detailed_prompt
    restored = apply_analysis_patch(
        accepted,
        detected,
        AnalysisPatch(
            updates=[FeatureUpdate(path="rhythm.bpm", restore_detected=True)]
        ),
    )
    assert restored.rhythm.bpm.user_accepted is False


def test_safe_key_and_violin_corrections_reach_prompt(click_analysis) -> None:
    analysis = _prompt_ready(click_analysis)
    detected = analysis.model_copy(deep=True)
    edited = apply_analysis_patch(
        analysis,
        detected,
        AnalysisPatch(
            updates=[
                FeatureUpdate(path="harmony.key", value="C#"),
                FeatureUpdate(path="harmony.mode", value="minor"),
                FeatureUpdate(
                    path="instrumentation.candidates",
                    value=[
                        {
                            "name": "violin",
                            "prominence": "prominent",
                            "confidence": "high",
                            "sections": [],
                        },
                        {
                            "name": "mandolin",
                            "prominence": "present",
                            "confidence": "high",
                            "sections": [],
                        },
                        {
                            "name": "oboe",
                            "prominence": "present",
                            "confidence": "high",
                            "sections": [],
                        },
                    ],
                ),
            ]
        ),
    )
    prompt = compose_prompt(edited, PromptPreferences()).detailed_prompt
    assert "C# minor" in prompt
    assert "violin" in prompt
    assert "mandolin" in prompt
    assert "oboe" in prompt


def test_brief_listed_instrument_and_vocal_terms_reach_prompt(click_analysis) -> None:
    analysis = _prompt_ready(click_analysis)
    detected = analysis.model_copy(deep=True)
    edited = apply_analysis_patch(
        analysis,
        detected,
        AnalysisPatch(
            updates=[
                FeatureUpdate(
                    path="instrumentation.candidates",
                    value=[
                        {
                            "name": name,
                            "prominence": "present",
                            "confidence": "high",
                            "sections": [],
                        }
                        for name in ("organ", "brass", "woodwinds", "arpeggiator")
                    ],
                )
            ]
        ),
    )
    package = compose_prompt(
        edited,
        PromptPreferences(
            target_mood="sustained synthetic texture",
            desired_vocal_presentation=(
                "legato staccato melismatic backing vocals with choir-like layering and vocoder"
            ),
        ),
    )
    prompt = package.detailed_prompt.casefold()
    for term in (
        "organ",
        "brass",
        "woodwinds",
        "arpeggiator",
        "legato",
        "staccato",
        "melismatic",
        "choir-like",
        "vocoder",
        "sustained synthetic texture",
    ):
        assert term in prompt


def test_rejected_user_edit_has_explicit_warning_and_omitted_reason(
    click_analysis,
) -> None:
    analysis = _prompt_ready(click_analysis)
    analysis.style_and_mood.mood = feature(
        ["dark madonna"], Confidence.LOW, "user edit"
    )
    analysis.style_and_mood.mood.user_edited = True
    package = compose_prompt(analysis, PromptPreferences())
    assert "madonna" not in package.detailed_prompt.casefold()
    assert any("user-edited value" in warning for warning in package.warnings)
    assert any(
        item.path == "styleAndMood.mood" and "safe musical vocabulary" in item.reason
        for item in package.facts_omitted
    )


def test_section_label_edit_disable_and_bounds_validation(click_analysis) -> None:
    analysis = _prompt_ready(click_analysis)
    detected = analysis.model_copy(deep=True)
    edited = apply_analysis_patch(
        analysis,
        detected,
        AnalysisPatch(
            updates=[
                FeatureUpdate(
                    path="structure.sections.0.inferredLabel",
                    value="build",
                ),
                FeatureUpdate(
                    path="structure.sections.0.energy",
                    disabled_for_prompt=True,
                ),
            ]
        ),
    )
    blueprint = compose_prompt(edited, PromptPreferences()).arrangement_blueprint
    assert blueprint[0].startswith("build:")
    assert "energy" not in blueprint[0]
    prechorus = apply_analysis_patch(
        edited,
        detected,
        AnalysisPatch(
            updates=[
                FeatureUpdate(
                    path="structure.sections.0.inferredLabel",
                    value="prechorus",
                )
            ]
        ),
    )
    assert prechorus.structure.sections[0].inferred_label == "prechorus"
    with pytest.raises(PatchError):
        apply_analysis_patch(
            edited,
            detected,
            AnalysisPatch(
                updates=[
                    FeatureUpdate(
                        path="structure.sections.0.endSeconds",
                        value=analysis.file.duration_seconds + 10,
                    )
                ]
            ),
        )


def test_disabled_inferred_section_labels_keep_times_without_semantic_fallback(
    click_analysis,
) -> None:
    analysis = _prompt_ready(click_analysis)
    template = analysis.structure.sections[0]
    analysis.structure.sections = [
        template.model_copy(
            update={
                "id": "section-1",
                "neutral_label": "A",
                "inferred_label": "intro",
                "start_seconds": 0.0,
                "end_seconds": 6.0,
            }
        ),
        template.model_copy(
            update={
                "id": "section-2",
                "neutral_label": "B",
                "inferred_label": "outro",
                "start_seconds": 6.0,
                "end_seconds": 12.0,
            }
        ),
    ]
    analysis.disabled_feature_paths.extend(
        [
            "structure.sections.0.inferredLabel",
            "structure.sections.1.inferredLabel",
        ]
    )

    package = compose_prompt(analysis, PromptPreferences())

    assert "restrained opening" not in package.detailed_prompt
    assert "resolved outro" not in package.detailed_prompt
    assert package.arrangement_blueprint[0].startswith("section: 0.0-6.0s")
    assert package.arrangement_blueprint[1].startswith("section: 6.0-12.0s")
    assert not package.arrangement_blueprint[0].startswith("A:")
    assert not package.arrangement_blueprint[1].startswith("B:")


def test_change_instrumentation_intent_does_not_preserve_current_instruments(
    click_analysis,
) -> None:
    analysis = _prompt_ready(click_analysis)
    preferences = PromptPreferences(
        generation_intent="change_instrumentation_preserve_structure",
        preserve_instrumentation=True,
    )
    assert preferences.preserve_instrumentation is False
    prompt = compose_prompt(analysis, preferences).detailed_prompt
    assert "replacing the instrumental palette" in prompt
    assert "centered on" not in prompt


def test_genre_transfer_requires_safe_nonblank_target(click_analysis) -> None:
    analysis = _prompt_ready(click_analysis)
    with pytest.raises(ValueError):
        PromptPreferences(generation_intent="genre_transfer")
    unsafe = compose_prompt(
        analysis,
        PromptPreferences(
            generation_intent="genre_transfer",
            target_genre="dark madonna",
        ),
    )
    assert "Translate the groove" not in unsafe.detailed_prompt
    assert any("safe nonblank target genre" in warning for warning in unsafe.warnings)
    safe = compose_prompt(
        analysis,
        PromptPreferences(generation_intent="genre_transfer", target_genre="synthwave"),
    )
    assert "synthwave" in safe.detailed_prompt
    assert "Translate the groove" in safe.detailed_prompt


def test_patch_edit_disable_and_restore(click_analysis) -> None:
    detected = click_analysis.model_copy(deep=True)
    edited = apply_analysis_patch(
        click_analysis.model_copy(deep=True),
        detected,
        AnalysisPatch(
            updates=[
                FeatureUpdate(path="rhythm.bpm", value=135.0, disabled_for_prompt=True)
            ]
        ),
    )
    assert edited.rhythm.bpm.value == 135.0
    assert edited.rhythm.bpm.user_edited is True
    assert "rhythm.bpm" in edited.disabled_feature_paths
    restored = apply_analysis_patch(
        edited,
        detected,
        AnalysisPatch(
            updates=[
                FeatureUpdate(
                    path="rhythm.bpm",
                    restore_detected=True,
                    disabled_for_prompt=False,
                )
            ]
        ),
    )
    assert restored.rhythm.bpm.value == detected.rhythm.bpm.value
    assert restored.rhythm.bpm.user_edited is False
    assert "rhythm.bpm" not in restored.disabled_feature_paths


def test_patch_rejects_private_file_and_invalid_value(click_analysis) -> None:
    detected = click_analysis.model_copy(deep=True)
    for update in (
        FeatureUpdate(path="file.displayName", value="escape"),
        FeatureUpdate(path="rhythm.bpm", value="not-a-number"),
    ):
        try:
            apply_analysis_patch(
                click_analysis.model_copy(deep=True),
                detected,
                AnalysisPatch(updates=[update]),
            )
        except PatchError:
            pass
        else:
            raise AssertionError("Unsafe edit unexpectedly succeeded")


def test_json_and_markdown_exports_include_versions_and_escape_html(click_analysis) -> None:
    analysis = _prompt_ready(click_analysis)
    package = compose_prompt(analysis, PromptPreferences())
    malicious = package.model_copy(update={"primary_prompt": "<script>alert(1)</script>"})
    json_bytes = analysis_json_export(analysis, package)
    markdown = analysis_markdown_export(analysis, malicious).decode("utf-8")
    assert b'"schemaVersion": "1.4.0"' in json_bytes
    assert b'"analysisVersion": "0.5.0"' in json_bytes
    assert "<script>" not in markdown
    assert "&lt;script>" in markdown


def test_ambiguous_key_is_omitted_until_explicitly_accepted(click_analysis) -> None:
    analysis = _prompt_ready(click_analysis)
    alternatives = [{"key": "B", "mode": "major", "templateFit": 0.455}]
    analysis.harmony.key = feature(
        "C#",
        Confidence.LOW,
        "near-tied templates",
        score=0.456,
        alternatives=alternatives,
        warning="Ambiguous between C# major and B major.",
    )
    analysis.harmony.mode = feature("major", Confidence.LOW, "near-tied templates")
    analysis.harmony.character = feature(["tonally fluid"], Confidence.LOW, "ambiguous")
    omitted = compose_prompt(analysis, PromptPreferences())
    assert "C# major tonal center" not in omitted.primary_prompt
    assert any(item.path == "harmony.key" for item in omitted.facts_omitted)
    analysis.harmony.key.user_accepted = True
    analysis.harmony.mode.user_accepted = True
    accepted = compose_prompt(analysis, PromptPreferences())
    assert "C# major tonal center" in accepted.detailed_prompt


def test_no_genre_fallback_uses_measured_attributes_and_warns(click_analysis) -> None:
    analysis = click_analysis.model_copy(deep=True)
    package = compose_prompt(analysis, PromptPreferences())
    assert "genre-fluid" not in package.primary_prompt
    assert "measured groove, timbre, and arrangement" in package.detailed_prompt
    assert any("genre adapter is unavailable" in warning.casefold() for warning in package.warnings)


def test_deep_section_vocal_changes_influence_arrangement_phrase(click_analysis) -> None:
    analysis = _prompt_ready(click_analysis)
    template = analysis.structure.sections[0]
    analysis.structure.sections = [
        template.model_copy(
            update={"id": f"section-{index + 1}", "start_seconds": index * 4.0, "end_seconds": (index + 1) * 4.0}
        )
        for index in range(3)
    ]
    for index, section in enumerate(analysis.structure.sections):
        section.deep_evidence = SectionStemEvidence(
            relative_rms={"vocals": 0.4 if index == 1 else 0.0, "drums": 0.6},
            activity={
                "vocals": "present" if index == 1 else "inactive",
                "drums": "prominent",
            },
            method="synthetic stem evidence",
            confidence=Confidence.MEDIUM,
        )
    package = compose_prompt(analysis, PromptPreferences())
    assert "vocals enter and recede" in package.detailed_prompt
    assert "drum prominence" in package.detailed_prompt
    assert package.compact_prompt != package.detailed_prompt
