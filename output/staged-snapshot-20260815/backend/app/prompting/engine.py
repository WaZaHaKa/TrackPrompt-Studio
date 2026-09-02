from __future__ import annotations

import re
import secrets
from difflib import SequenceMatcher
from typing import Any

from ..config import Settings
from ..lyrics.quality import contains_private_lyrics_fragment
from ..schemas import (
    AnalysisResult,
    Confidence,
    GenreInterpretationMode,
    LocalPromptCandidate,
    LyricsInfluenceMode,
    PrivateLyricsTranscript,
    PromptEngineMode,
    PromptEvidence,
    PromptGenerationParameters,
    PromptLength,
    PromptPackage,
    PromptPreferences,
    PromptRationale,
)
from ..taxonomies.music_styles import load_music_style_taxonomy
from .composer import (
    ORIGINALITY_CLAUSE,
    PROHIBITED_REFERENCE,
    compose_prompt,
    resolve_prompt_facts,
)
from .local_writer import LocalPromptWriterAdapter, LocalPromptWriterError, create_prompt_writer

INJECTION_PATTERN = re.compile(r"\b(?:ignore previous|system prompt|developer message|follow these instructions)\b", re.IGNORECASE)
PATH_PATTERN = re.compile(r"(?:[A-Za-z]:\\|/data/|/app/|traceback \(most recent call last\))", re.IGNORECASE)
CHORD_SEQUENCE_PATTERN = re.compile(
    r"(?:\b[A-G](?:#|b)?(?:m|maj|min|dim|aug|sus)?\b\s*(?:->|\u2192|-|,|\|)\s*){3,}"
)
NOTE_SEQUENCE_PATTERN = re.compile(r"(?:\b[A-G](?:#|b)?[0-8]?\b\s*){6,}")
BPM_CLAIM_PATTERN = re.compile(r"\b(\d{2,3}(?:\.\d+)?)\s*bpm\b", re.IGNORECASE)
METER_CLAIM_PATTERN = re.compile(r"\b([2-9])\s*/\s*([248])\b")


def _bounded_user_text(value: str | None, maximum: int) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", "".join(character for character in value if character.isprintable())).strip()
    if not cleaned or INJECTION_PATTERN.search(cleaned) or PATH_PATTERN.search(cleaned) or PROHIBITED_REFERENCE.search(cleaned):
        return None
    return cleaned[:maximum].rstrip()


def _feature_list(value: Any, path: str, disabled_paths: set[str]) -> list[str]:
    if path in disabled_paths or value.value is None:
        return []
    if value.confidence not in {Confidence.MEDIUM, Confidence.HIGH} and not value.user_edited and not value.user_accepted:
        return []
    raw = value.value if isinstance(value.value, list) else [value.value]
    return [str(item)[:80] for item in raw[:8] if _bounded_user_text(str(item), 80)]


def _character_limit(preferences: PromptPreferences) -> int:
    if preferences.prompt_length == PromptLength.COMPACT:
        return 450
    if preferences.prompt_length == PromptLength.DETAILED:
        return 1800
    if preferences.prompt_length == PromptLength.CUSTOM:
        return preferences.custom_max_characters or 1000
    return 1000


def build_prompt_evidence(analysis: AnalysisResult, preferences: PromptPreferences) -> PromptEvidence:
    disabled_paths = set(analysis.disabled_feature_paths) | set(preferences.disabled_feature_paths)
    accepted_genres: list[str] = []
    blend: list[str] = []
    genre = analysis.genre_analysis
    if (
        genre is not None
        and not genre.disabled_for_prompt
        and preferences.include_detected_genre
        and preferences.genre_interpretation_mode != GenreInterpretationMode.DISABLED
    ):
        candidates = genre.broad_candidates + genre.subgenre_candidates
        if preferences.genre_interpretation_mode == GenreInterpretationMode.DETECTED_LAYERED:
            primary = genre.primary_production_genre
            secondary = genre.secondary_production_genres
            primary_values = [primary.value] if primary is not None and isinstance(primary.value, str) else []
            secondary_values = (
                secondary.value
                if secondary is not None and isinstance(secondary.value, list)
                else []
            )
            accepted_genres = [
                cleaned
                for raw in [*primary_values, *secondary_values[:3]]
                if (cleaned := _bounded_user_text(str(raw), 120)) is not None
            ]
            if genre.overall_genre_blend is not None and isinstance(genre.overall_genre_blend.value, str):
                cleaned_blend = _bounded_user_text(genre.overall_genre_blend.value, 240)
                if cleaned_blend:
                    blend = [cleaned_blend]
        else:
            selected = [
                item for item in candidates if not item.rejected and item.accepted
            ]
            if preferences.accepted_genre_ids:
                accepted_id_filter = set(preferences.accepted_genre_ids)
                selected = [item for item in selected if item.id in accepted_id_filter]
            if preferences.genre_interpretation_mode == GenreInterpretationMode.STRICT_TOP:
                selected = selected[:1]
            elif preferences.genre_interpretation_mode == GenreInterpretationMode.BLEND:
                selected = selected[:2]
            elif preferences.genre_interpretation_mode == GenreInterpretationMode.USER_SELECTED_ONLY:
                selected = [item for item in selected if item.user_edited or item.custom]
            accepted_genres = [
                cleaned
                for item in selected
                if (cleaned := _bounded_user_text(item.label, 120)) is not None
            ]
            if preferences.genre_interpretation_mode == GenreInterpretationMode.BLEND:
                if genre.overall_genre_blend is not None and isinstance(genre.overall_genre_blend.value, str):
                    cleaned_blend = _bounded_user_text(genre.overall_genre_blend.value, 240)
                    if cleaned_blend and accepted_genres:
                        blend = [cleaned_blend]
                elif len(accepted_genres) >= 2:
                    blend = [f"{accepted_genres[0]} with {accepted_genres[1]} influence"]
    themes: list[str] = []
    if (
        preferences.lyrics_influence_mode == LyricsInfluenceMode.ABSTRACT_THEMES
        and preferences.include_lyrical_themes
        and analysis.lyrics_summary is not None
        and analysis.lyrics_summary.themes_user_approved
    ):
        themes = [theme for theme in analysis.lyrics_summary.abstract_themes if _bounded_user_text(theme, 120)][:8]
    elif preferences.lyrics_influence_mode == LyricsInfluenceMode.USER_WRITTEN_DIRECTION:
        direction = _bounded_user_text(preferences.user_written_lyrical_direction, 240)
        if direction:
            themes = [direction]
    tempo = (
        analysis.rhythm.bpm.value
        if "rhythm.bpm" not in disabled_paths
        and (
            analysis.rhythm.bpm.confidence in {Confidence.MEDIUM, Confidence.HIGH}
            or analysis.rhythm.bpm.user_edited
            or analysis.rhythm.bpm.user_accepted
        )
        else None
    )
    meter_values = _feature_list(analysis.rhythm.meter, "rhythm.meter", disabled_paths)
    vocal_presence = _feature_list(analysis.vocals.presence, "vocals.presence", disabled_paths)
    vocal_density = _feature_list(analysis.vocals.density, "vocals.density", disabled_paths)
    energy_values = _feature_list(analysis.style_and_mood.energy, "styleAndMood.energy", disabled_paths)
    instrumentation_value = analysis.instrumentation.candidates
    instrumentation_enabled = (
        "instrumentation.candidates" not in disabled_paths
        and preferences.preserve_instrumentation
        and instrumentation_value.value is not None
        and (
            instrumentation_value.confidence in {Confidence.MEDIUM, Confidence.HIGH}
            or instrumentation_value.user_edited
            or instrumentation_value.user_accepted
        )
    )
    structure_summary = [
        section.inferred_label or section.neutral_label
        for index, section in enumerate(analysis.structure.sections[:10])
        if f"structure.sections.{index}" not in disabled_paths
        and f"structure.sections.{index}.inferredLabel" not in disabled_paths
    ]
    section_energy_summary = [
        f"section {index + 1}: {section.energy:.2f} relative energy"
        for index, section in enumerate(analysis.structure.sections[:10])
        if section.energy is not None
        and f"structure.sections.{index}" not in disabled_paths
        and f"structure.sections.{index}.energy" not in disabled_paths
    ]
    return PromptEvidence(
        accepted_genre_candidates=accepted_genres,
        accepted_genre_blend=blend,
        tempo=float(tempo) if tempo is not None and preferences.include_bpm else None,
        meter=meter_values[0] if meter_values else None,
        groove=_feature_list(analysis.rhythm.groove_descriptors, "rhythm.grooveDescriptors", disabled_paths),
        mood=_feature_list(analysis.style_and_mood.mood, "styleAndMood.mood", disabled_paths),
        energy=energy_values[0] if energy_values else None,
        instrumentation=[candidate.name[:80] for candidate in (analysis.instrumentation.candidates.value or [])[:8]]
        if instrumentation_enabled
        else [],
        vocal_presence=None if preferences.instrumental else (vocal_presence[0] if vocal_presence else None),
        vocal_density=None if preferences.instrumental else (vocal_density[0] if vocal_density else None),
        approved_vocal_descriptors=[]
        if preferences.instrumental
        else _feature_list(analysis.vocals.delivery, "vocals.delivery", disabled_paths),
        vocal_genre_influences=(
            []
            if preferences.instrumental
            or genre is None
            or genre.vocal_genre_influences is None
            or not isinstance(genre.vocal_genre_influences.value, list)
            else [str(item)[:80] for item in genre.vocal_genre_influences.value[:6]]
        ),
        overall_genre_blend=(blend[0] if blend else None),
        structure_summary=structure_summary,
        section_energy_summary=section_energy_summary,
        production_descriptors=_feature_list(
            analysis.production.production_character,
            "production.productionCharacter",
            disabled_paths,
        ),
        low_end_weight=(
            values[0]
            if (values := _feature_list(analysis.production.low_end_weight, "production.lowEndWeight", disabled_paths))
            else None
        ),
        stereo_character=(
            values[0]
            if (values := _feature_list(analysis.production.spaciousness_proxy, "production.spaciousnessProxy", disabled_paths))
            else None
        ),
        energy_arc=(
            values[0]
            if (values := _feature_list(analysis.structure.energy_arc, "structure.energyArc", disabled_paths))
            else None
        ),
        repetition_character=(
            values[0]
            if (values := _feature_list(analysis.structure.repetition_summary, "structure.repetitionSummary", disabled_paths))
            else None
        ),
        harmonic_character=_feature_list(analysis.harmony.character, "harmony.character", disabled_paths),
        target_genre=_bounded_user_text(preferences.target_genre, 120),
        target_mood=_bounded_user_text(preferences.target_mood, 120),
        target_duration=preferences.target_duration,
        generation_intent=preferences.generation_intent,
        creative_freedom=preferences.creativity,
        locked_facts=preferences.locked_feature_paths,
        desired_transformations=[
            cleaned
            for item in preferences.desired_transformations
            if (cleaned := _bounded_user_text(item, 200)) is not None
        ],
        allowed_lyrical_themes=themes,
        exclusions=[cleaned for item in preferences.exclusions if (cleaned := _bounded_user_text(item, 160)) is not None],
        originality_requirement=ORIGINALITY_CLAUSE,
        maximum_characters=_character_limit(preferences),
        output_language=(_bounded_user_text(preferences.output_language, 60) or "English"),
    )


def _parameters(mode: PromptEngineMode, preferences: PromptPreferences, settings: Settings) -> PromptGenerationParameters:
    if mode == PromptEngineMode.EXPERIMENTAL:
        temperature = 0.8 + 0.18 * preferences.creativity
        top_p = 0.94 + 0.03 * preferences.creativity
        repetition = 1.12
    elif mode == PromptEngineMode.CREATIVE:
        temperature = 0.6 + 0.15 * preferences.creativity
        top_p = 0.88 + 0.05 * preferences.creativity
        repetition = 1.08
    else:
        return PromptGenerationParameters()
    return PromptGenerationParameters(
        sampling=True,
        temperature=round(temperature, 3),
        top_p=round(top_p, 3),
        repetition_penalty=repetition,
        maximum_tokens=700,
        timeout_seconds=settings.local_llm_timeout_seconds,
    )


def _candidate_errors(
    raw: dict[str, Any],
    evidence: PromptEvidence,
    analysis: AnalysisResult,
    transcript: PrivateLyricsTranscript | None,
    mode: PromptEngineMode,
    required_fact_paths: set[str],
) -> list[str]:
    prompt = raw.get("prompt")
    if not isinstance(prompt, str):
        return ["candidate prompt is missing"]
    errors: list[str] = []
    if "\n" in prompt.strip() or len(prompt) > evidence.maximum_characters:
        errors.append("prompt must be one bounded paragraph")
    if "original" not in prompt.casefold() or "melody" not in prompt.casefold() or "arrangement" not in prompt.casefold():
        errors.append("originality requirement is missing")
    claimed_tempos = [float(match.group(1)) for match in BPM_CLAIM_PATTERN.finditer(prompt)]
    if claimed_tempos and evidence.tempo is None:
        errors.append("a BPM claim without allowed evidence is forbidden")
    elif evidence.tempo is not None and any(abs(claimed - evidence.tempo) > 1.0 for claimed in claimed_tempos):
        errors.append(
            "a locked BPM fact was contradicted"
            if "rhythm.bpm" in evidence.locked_facts
            else "an observed BPM fact was contradicted"
        )
    claimed_meters = [f"{match.group(1)}/{match.group(2)}" for match in METER_CLAIM_PATTERN.finditer(prompt)]
    if claimed_meters and not evidence.meter:
        errors.append("a meter claim without allowed evidence is forbidden")
    elif evidence.meter:
        expected_meter = re.sub(r"\s+", "", evidence.meter)
        if any(claimed != expected_meter for claimed in claimed_meters):
            errors.append(
                "a locked meter fact was contradicted"
                if "rhythm.meter" in evidence.locked_facts
                else "an observed meter fact was contradicted"
            )
    if PROHIBITED_REFERENCE.search(prompt):
        errors.append("named-artist or imitation language is forbidden")
    if INJECTION_PATTERN.search(prompt) or PATH_PATTERN.search(prompt):
        errors.append("prompt-injection or private-path language is forbidden")
    source_values = {analysis.file.display_name.casefold(), analysis.file.display_name.rsplit(".", 1)[0].casefold()}
    source_values.update(value.casefold() for value in analysis.file.private_metadata.values())
    if any(value and len(value) >= 3 and value in prompt.casefold() for value in source_values):
        errors.append("source identity is forbidden")
    if CHORD_SEQUENCE_PATTERN.search(prompt) or NOTE_SEQUENCE_PATTERN.search(prompt):
        errors.append("complete chord or note sequence is forbidden")
    if transcript is not None and contains_private_lyrics_fragment(prompt, transcript):
        errors.append("raw transcript text is forbidden")
    genre_scan_prompt = prompt
    allowed_genre_phrases = [
        *evidence.accepted_genre_candidates,
        *evidence.accepted_genre_blend,
        *evidence.vocal_genre_influences,
        *([evidence.overall_genre_blend] if evidence.overall_genre_blend else []),
        *([evidence.target_genre] if evidence.target_genre else []),
    ]
    for allowed_phrase in sorted(allowed_genre_phrases, key=len, reverse=True):
        cleaned = re.sub(r"\s+", " ", allowed_phrase.strip())
        if cleaned:
            genre_scan_prompt = re.sub(
                rf"(?<!\w){re.escape(cleaned)}(?!\w)",
                " ",
                genre_scan_prompt,
                flags=re.IGNORECASE,
            )
    forbidden_genres = _forbidden_detected_genres(analysis, evidence, mode)
    if any(_contains_bounded_phrase(genre_scan_prompt, label) for label in forbidden_genres):
        errors.append("a detected genre not allowed by review or mode is forbidden")
    auxiliary_text: list[str] = []
    short_title = raw.get("shortTitle")
    if isinstance(short_title, str):
        auxiliary_text.append(short_title)
    creative_directions = raw.get("creativeDirectionsUsed")
    if isinstance(creative_directions, list):
        auxiliary_text.extend(item for item in creative_directions if isinstance(item, str))
    for value in auxiliary_text:
        if transcript is not None and contains_private_lyrics_fragment(value, transcript):
            errors.append("raw transcript text is forbidden outside the prompt")
        if any(source and len(source) >= 3 and source in value.casefold() for source in source_values):
            errors.append("source identity is forbidden outside the prompt")
        if INJECTION_PATTERN.search(value) or PATH_PATTERN.search(value) or PROHIBITED_REFERENCE.search(value):
            errors.append("unsafe model metadata is forbidden")
    expected_directions = evidence.desired_transformations
    if not isinstance(creative_directions, list) or any(
        not isinstance(item, str) for item in creative_directions
    ):
        errors.append("creativeDirectionsUsed must be a bounded string list")
    elif any(direction not in creative_directions for direction in expected_directions):
        errors.append("required creative directions are missing")
    facts = raw.get("factsUsed")
    if not isinstance(facts, list) or any(not isinstance(item, str) or len(item) > 120 for item in facts):
        errors.append("factsUsed must be a bounded string list")
    missing_required_evidence = sorted(
        path
        for path in required_fact_paths
        if not _fact_path_is_expressed(path, prompt, evidence, analysis)
    )
    if missing_required_evidence:
        errors.append(
            "required reviewed prompt evidence is missing: "
            + ", ".join(missing_required_evidence)
        )
    return errors


def _contains_bounded_phrase(text: str, phrase: str) -> bool:
    cleaned = re.sub(r"\s+", " ", phrase.strip())
    if len(cleaned) < 3:
        return False
    return re.search(
        rf"(?<!\w){re.escape(cleaned)}(?!\w)",
        re.sub(r"\s+", " ", text),
        flags=re.IGNORECASE,
    ) is not None


def _forbidden_detected_genres(
    analysis: AnalysisResult,
    evidence: PromptEvidence,
    mode: PromptEngineMode,
) -> set[str]:
    genre = analysis.genre_analysis
    target = (evidence.target_genre or "").casefold().strip()
    allowed = {
        label.casefold().strip()
        for label in [
            *evidence.accepted_genre_candidates,
            *evidence.vocal_genre_influences,
        ]
        if label.strip()
    }
    taxonomy = load_music_style_taxonomy()
    labels = {
        label.strip()
        for item in [*taxonomy.broad_genres, *taxonomy.subgenres]
        for label in (item.label, item.prompt_safe_label, *item.aliases)
        if label.strip()
        and label.casefold().strip() not in allowed
        and label.casefold().strip() != target
    }
    if genre is not None:
        candidates = genre.broad_candidates + genre.subgenre_candidates
        forbidden = [
            item
            for item in candidates
            if genre.disabled_for_prompt
            or item.rejected
            or item.label.casefold().strip() not in allowed
        ]
        labels.update(
            label.strip()
            for item in forbidden
            for label in (item.label, item.canonical_label)
            if label.strip() and label.casefold().strip() != target
        )
    if mode == PromptEngineMode.EXPERIMENTAL:
        # "Experimental" is also the engine-mode name and may truthfully
        # describe a requested transformation without asserting a detected
        # genre. Other unaccepted genre labels remain forbidden.
        labels = {label for label in labels if label.casefold() != "experimental"}
    return labels


def _materially_diverse(
    left: str,
    right: str,
    mode: PromptEngineMode = PromptEngineMode.CREATIVE,
) -> bool:
    normalized_left = re.sub(r"\W+", " ", left.casefold()).strip()
    normalized_right = re.sub(r"\W+", " ", right.casefold()).strip()
    similarity = SequenceMatcher(None, normalized_left, normalized_right).ratio()
    left_ngrams = set(zip(normalized_left.split(), normalized_left.split()[1:], strict=False))
    right_ngrams = set(zip(normalized_right.split(), normalized_right.split()[1:], strict=False))
    overlap = len(left_ngrams & right_ngrams) / max(1, len(left_ngrams | right_ngrams))
    similarity_limit = 0.8 if mode == PromptEngineMode.EXPERIMENTAL else 0.86
    overlap_limit = 0.6 if mode == PromptEngineMode.EXPERIMENTAL else 0.72
    return (
        similarity < similarity_limit
        and overlap < overlap_limit
        and normalized_left.split()[:5] != normalized_right.split()[:5]
    )


def _canonical_evidence_fact_paths(
    evidence: PromptEvidence,
    analysis: AnalysisResult,
    lyrics_influence_mode: LyricsInfluenceMode,
) -> list[str]:
    paths: list[str] = []
    allowed_genres = {label.casefold().strip() for label in evidence.accepted_genre_candidates}
    if analysis.genre_analysis is not None:
        for candidate in (
            analysis.genre_analysis.broad_candidates
            + analysis.genre_analysis.subgenre_candidates
        ):
            if (
                candidate.accepted
                and not candidate.rejected
                and candidate.label.casefold().strip() in allowed_genres
            ):
                paths.append(f"genreAnalysis.accepted.{candidate.id}")
        genre = analysis.genre_analysis
        if evidence.overall_genre_blend and genre.overall_genre_blend is not None:
            paths.append("genreAnalysis.overallGenreBlend")
        if evidence.accepted_genre_candidates and genre.primary_production_genre is not None:
            paths.append("genreAnalysis.primaryProductionGenre")
        if evidence.accepted_genre_candidates and genre.secondary_production_genres is not None:
            paths.append("genreAnalysis.secondaryProductionGenres")
        if evidence.approved_vocal_descriptors and genre.vocal_delivery_style is not None:
            paths.append("genreAnalysis.vocalDeliveryStyle")
        if evidence.vocal_genre_influences and genre.vocal_genre_influences is not None:
            paths.append("genreAnalysis.vocalGenreInfluences")
    if (
        evidence.allowed_lyrical_themes
        and lyrics_influence_mode == LyricsInfluenceMode.ABSTRACT_THEMES
    ):
        paths.append("lyricsSummary.abstractThemes")
    elif (
        evidence.allowed_lyrical_themes
        and lyrics_influence_mode == LyricsInfluenceMode.USER_WRITTEN_DIRECTION
    ):
        paths.append("preferences.userWrittenLyricalDirection")
    if evidence.target_genre is not None:
        paths.append("preferences.targetGenre")
    if evidence.tempo is not None:
        paths.append("rhythm.bpm")
    if evidence.meter is not None:
        paths.append("rhythm.meter")
    if evidence.groove:
        paths.append("rhythm.grooveDescriptors")
    if evidence.mood:
        paths.append("styleAndMood.mood")
    if evidence.energy is not None:
        paths.append("styleAndMood.energy")
    if evidence.instrumentation:
        paths.append("instrumentation.candidates")
    if evidence.vocal_presence is not None:
        paths.append("vocals.presence")
    if evidence.vocal_density is not None:
        paths.append("vocals.density")
    if evidence.approved_vocal_descriptors:
        paths.append("vocals.delivery")
    if evidence.structure_summary:
        paths.append("structure.sections")
    if evidence.energy_arc is not None:
        paths.append("structure.energyArc")
    if evidence.repetition_character is not None:
        paths.append("structure.repetitionSummary")
    if evidence.production_descriptors:
        paths.append("production.productionCharacter")
    if evidence.low_end_weight is not None:
        paths.append("production.lowEndWeight")
    if evidence.stereo_character is not None:
        paths.append("production.spaciousnessProxy")
    if evidence.harmonic_character:
        paths.append("harmony.character")
    return paths


def _fact_path_is_expressed(
    path: str,
    prompt: str,
    evidence: PromptEvidence,
    analysis: AnalysisResult,
) -> bool:
    if path.startswith("genreAnalysis.accepted.") and analysis.genre_analysis is not None:
        candidate_id = path.rsplit(".", 1)[-1]
        candidate = next(
            (
                item
                for item in (
                    analysis.genre_analysis.broad_candidates
                    + analysis.genre_analysis.subgenre_candidates
                )
                if item.id == candidate_id
            ),
            None,
        )
        return candidate is not None and _contains_bounded_phrase(prompt, candidate.label)
    if path == "genreAnalysis.overallGenreBlend" and evidence.overall_genre_blend:
        return _contains_bounded_phrase(prompt, evidence.overall_genre_blend)
    if path == "genreAnalysis.primaryProductionGenre" and analysis.genre_analysis is not None:
        layer = analysis.genre_analysis.primary_production_genre
        return layer is not None and isinstance(layer.value, str) and _contains_bounded_phrase(prompt, layer.value)
    if path == "genreAnalysis.secondaryProductionGenres" and analysis.genre_analysis is not None:
        layer = analysis.genre_analysis.secondary_production_genres
        return layer is not None and isinstance(layer.value, list) and any(
            _contains_bounded_phrase(prompt, str(value)) for value in layer.value
        )
    if path == "genreAnalysis.vocalDeliveryStyle":
        return any(_contains_bounded_phrase(prompt, value) for value in evidence.approved_vocal_descriptors)
    if path == "genreAnalysis.vocalGenreInfluences":
        return any(_contains_bounded_phrase(prompt, value) for value in evidence.vocal_genre_influences)
    if path == "rhythm.bpm" and evidence.tempo is not None:
        return any(
            abs(float(match.group(1)) - evidence.tempo) <= 1.0
            for match in BPM_CLAIM_PATTERN.finditer(prompt)
        )
    if path == "rhythm.meter" and evidence.meter:
        expected = re.sub(r"\s+", "", evidence.meter)
        return any(
            f"{match.group(1)}/{match.group(2)}" == expected
            for match in METER_CLAIM_PATTERN.finditer(prompt)
        )
    if path == "lyricsSummary.abstractThemes":
        return any(_contains_bounded_phrase(prompt, theme) for theme in evidence.allowed_lyrical_themes)
    if path == "preferences.userWrittenLyricalDirection":
        return any(_contains_bounded_phrase(prompt, direction) for direction in evidence.allowed_lyrical_themes)
    if path == "preferences.targetGenre" and evidence.target_genre is not None:
        return _contains_bounded_phrase(prompt, evidence.target_genre)
    evidence_values: dict[str, list[str]] = {
        "rhythm.grooveDescriptors": evidence.groove,
        "styleAndMood.mood": evidence.mood,
        "styleAndMood.energy": [evidence.energy] if evidence.energy else [],
        "instrumentation.candidates": evidence.instrumentation,
        "vocals.presence": [evidence.vocal_presence] if evidence.vocal_presence else [],
        "vocals.density": [evidence.vocal_density] if evidence.vocal_density else [],
        "vocals.delivery": evidence.approved_vocal_descriptors,
        "structure.sections": evidence.structure_summary,
        "structure.energyArc": [evidence.energy_arc] if evidence.energy_arc else [],
        "structure.repetitionSummary": [evidence.repetition_character] if evidence.repetition_character else [],
        "production.productionCharacter": evidence.production_descriptors,
        "production.lowEndWeight": [evidence.low_end_weight] if evidence.low_end_weight else [],
        "production.spaciousnessProxy": [evidence.stereo_character] if evidence.stereo_character else [],
        "harmony.character": evidence.harmonic_character,
    }
    if path in evidence_values:
        return any(_contains_bounded_phrase(prompt, value) for value in evidence_values[path])
    return False


def _required_evidence_fact_paths(
    allowed_fact_paths: list[str],
    locked_fact_paths: list[str],
) -> list[str]:
    locked = set(locked_fact_paths)
    return [
        path
        for path in allowed_fact_paths
        if path in locked
        or path.startswith("genreAnalysis.accepted.")
        or path == "genreAnalysis.overallGenreBlend"
        or path == "rhythm.bpm"
        or path
        in {
            "lyricsSummary.abstractThemes",
            "preferences.targetGenre",
            "preferences.userWrittenLyricalDirection",
        }
    ]


def _required_evidence_literals(
    required_fact_paths: list[str],
    evidence: PromptEvidence,
    analysis: AnalysisResult,
) -> dict[str, str]:
    literals: dict[str, str] = {}
    for path in required_fact_paths:
        if path.startswith("genreAnalysis.accepted.") and analysis.genre_analysis is not None:
            candidate_id = path.rsplit(".", 1)[-1]
            candidate = next(
                (
                    item
                    for item in (
                        analysis.genre_analysis.broad_candidates
                        + analysis.genre_analysis.subgenre_candidates
                    )
                    if item.id == candidate_id
                ),
                None,
            )
            if candidate is not None:
                literals[path] = candidate.label
        elif path == "genreAnalysis.overallGenreBlend" and evidence.overall_genre_blend:
            literals[path] = evidence.overall_genre_blend
        elif path == "genreAnalysis.primaryProductionGenre" and analysis.genre_analysis is not None:
            layer = analysis.genre_analysis.primary_production_genre
            if layer is not None and isinstance(layer.value, str):
                literals[path] = layer.value
        elif path == "genreAnalysis.secondaryProductionGenres" and analysis.genre_analysis is not None:
            layer = analysis.genre_analysis.secondary_production_genres
            if layer is not None and isinstance(layer.value, list) and layer.value:
                literals[path] = str(layer.value[0])
        elif path == "genreAnalysis.vocalDeliveryStyle" and evidence.approved_vocal_descriptors:
            literals[path] = evidence.approved_vocal_descriptors[0]
        elif path == "genreAnalysis.vocalGenreInfluences" and evidence.vocal_genre_influences:
            literals[path] = evidence.vocal_genre_influences[0]
        elif path == "lyricsSummary.abstractThemes" and evidence.allowed_lyrical_themes:
            literals[path] = evidence.allowed_lyrical_themes[0]
        elif path == "preferences.userWrittenLyricalDirection" and evidence.allowed_lyrical_themes:
            literals[path] = evidence.allowed_lyrical_themes[0]
        elif path == "preferences.targetGenre" and evidence.target_genre is not None:
            literals[path] = evidence.target_genre
        elif path == "rhythm.bpm" and evidence.tempo is not None:
            literals[path] = f"{evidence.tempo:g} BPM"
        elif path == "rhythm.meter" and evidence.meter:
            literals[path] = evidence.meter
    return literals


def _insert_required_prompt_evidence(
    raw: dict[str, Any],
    required_fact_literals: dict[str, str],
    evidence: PromptEvidence,
    analysis: AnalysisResult,
) -> tuple[dict[str, Any], bool]:
    prompt = raw.get("prompt")
    if not isinstance(prompt, str):
        return raw, False
    missing = [
        (path, literal)
        for path, literal in required_fact_literals.items()
        if literal.strip() and not _fact_path_is_expressed(path, prompt, evidence, analysis)
    ]
    if not missing:
        return raw, False
    clauses: list[str] = []
    genres = [literal for path, literal in missing if path.startswith("genreAnalysis.accepted.")]
    if genres:
        clauses.append("Use the reviewed genre direction: " + ", ".join(genres) + ".")
    for path, literal in missing:
        clean_literal = literal.strip().rstrip(".")
        if path.startswith("genreAnalysis.accepted."):
            continue
        if path == "genreAnalysis.overallGenreBlend":
            clauses.append(f"Use the measured layered genre direction: {clean_literal}.")
        elif path == "genreAnalysis.primaryProductionGenre":
            clauses.append(f"Keep the production foundation in {clean_literal}.")
        elif path == "genreAnalysis.secondaryProductionGenres":
            clauses.append(f"Allow the production influence of {clean_literal}.")
        elif path == "genreAnalysis.vocalDeliveryStyle":
            clauses.append(f"Use {clean_literal} vocal delivery.")
        elif path == "genreAnalysis.vocalGenreInfluences":
            clauses.append(f"Keep {clean_literal} as a vocal influence, not the whole backing genre.")
        elif path == "lyricsSummary.abstractThemes":
            clauses.append(f"Use the approved abstract lyrical theme: {clean_literal}.")
        elif path == "preferences.userWrittenLyricalDirection":
            clauses.append(f"Follow the user-written lyrical direction exactly: {clean_literal}.")
        elif path == "preferences.targetGenre":
            clauses.append(f"Target the requested genre: {clean_literal}.")
        elif path == "rhythm.bpm":
            clauses.append(f"Keep the pulse at {clean_literal}.")
        elif path == "rhythm.meter":
            clauses.append(f"Keep the meter at {clean_literal}.")
    if not clauses:
        return raw, False
    combined = f"{prompt.strip()} {' '.join(clauses)}"
    if len(combined) > evidence.maximum_characters:
        return raw, False
    return {**raw, "prompt": combined}, True


def _validated_candidates(
    raw_candidates: list[dict[str, Any]],
    evidence: PromptEvidence,
    analysis: AnalysisResult,
    preferences: PromptPreferences,
    transcript: PrivateLyricsTranscript | None,
    mode: PromptEngineMode,
    seed: int,
    model_id: str,
    parameters: PromptGenerationParameters,
    allowed_fact_paths: list[str],
    required_fact_paths: list[str],
    required_fact_literals: dict[str, str],
    insert_required_evidence: bool = False,
) -> tuple[list[LocalPromptCandidate], list[str]]:
    candidates: list[LocalPromptCandidate] = []
    warnings: list[str] = []
    allowed_facts = set(allowed_fact_paths)
    required_facts = set(required_fact_paths)
    for index, raw in enumerate(raw_candidates[:3]):
        if insert_required_evidence:
            raw, inserted = _insert_required_prompt_evidence(
                raw,
                required_fact_literals,
                evidence,
                analysis,
            )
            if inserted:
                warnings.append(
                    "Required reviewed evidence was inserted by the deterministic safety repair."
                )
        errors = _candidate_errors(
            raw,
            evidence,
            analysis,
            transcript,
            mode,
            required_facts,
        )
        if errors:
            warnings.extend(errors)
            continue
        prompt = str(raw["prompt"]).strip()
        if any(
            not _materially_diverse(prompt, existing.prompt, mode)
            for existing in candidates
        ):
            warnings.append("A near-duplicate candidate was rejected.")
            continue
        title = _bounded_user_text(str(raw.get("shortTitle", "Prompt candidate")), 80) or f"Candidate {index + 1}"
        declared_facts = [str(item)[:120] for item in raw.get("factsUsed", [])[:100]]
        unsupported_facts = [item for item in declared_facts if item not in allowed_facts]
        if unsupported_facts:
            warnings.append("Unsupported local-writer fact metadata was omitted.")
        unexpressed_facts = [
            item
            for item in declared_facts
            if item in allowed_facts
            and not _fact_path_is_expressed(item, prompt, evidence, analysis)
        ]
        if unexpressed_facts:
            warnings.append("Unexpressed local-writer fact metadata was omitted.")
        expressed_fact_paths = [
            path
            for path in allowed_fact_paths
            if _fact_path_is_expressed(path, prompt, evidence, analysis)
        ][:100]
        candidates.append(
            LocalPromptCandidate(
                id=f"candidate-{seed}-{index + 1}",
                prompt=prompt,
                short_title=title,
                engine_mode=mode,
                seed=seed,
                model_id=model_id,
                generation_parameters=parameters,
                facts_used=resolve_prompt_facts(analysis, preferences, expressed_fact_paths),
                creative_directions_used=[
                    direction
                    for direction in evidence.desired_transformations
                    if direction in raw.get("creativeDirectionsUsed", [])
                ][:20],
            )
        )
    return candidates, list(dict.fromkeys(warnings))


def _reliable_package(analysis: AnalysisResult, preferences: PromptPreferences) -> PromptPackage:
    package = compose_prompt(analysis, preferences)
    seed = preferences.variation_seed
    candidate = LocalPromptCandidate(
        id=f"candidate-reliable-{seed or 0}",
        prompt=package.primary_prompt,
        short_title="Reliable prompt",
        engine_mode=PromptEngineMode.RELIABLE,
        seed=seed,
        model_id="trackprompt-deterministic-composer",
        generation_parameters=PromptGenerationParameters(),
        facts_used=package.facts_used,
        warnings=package.warnings,
    )
    return package.model_copy(
        update={
            "engine_mode": PromptEngineMode.RELIABLE,
            "candidates": [candidate],
            "selected_candidate_id": candidate.id,
            "model_id": candidate.model_id,
            "seed": seed,
            "generation_parameters": candidate.generation_parameters,
        }
    )


def generate_prompt_package(
    analysis: AnalysisResult,
    preferences: PromptPreferences,
    settings: Settings,
    transcript: PrivateLyricsTranscript | None = None,
    adapter: LocalPromptWriterAdapter | None = None,
) -> PromptPackage:
    reliable = _reliable_package(analysis, preferences)
    mode = preferences.prompt_engine_mode
    if mode == PromptEngineMode.RELIABLE:
        return reliable
    seed = preferences.variation_seed if preferences.variation_seed is not None else secrets.randbelow(2_147_483_648)
    parameters = _parameters(mode, preferences, settings)
    evidence = build_prompt_evidence(analysis, preferences)
    writer = adapter or create_prompt_writer(settings)
    allowed_fact_paths = _canonical_evidence_fact_paths(
        evidence,
        analysis,
        preferences.lyrics_influence_mode,
    )
    required_fact_paths = _required_evidence_fact_paths(
        allowed_fact_paths,
        evidence.locked_facts,
    )
    required_fact_literals = _required_evidence_literals(
        required_fact_paths,
        evidence,
        analysis,
    )
    forbidden_genre_labels = sorted(
        cleaned
        for label in _forbidden_detected_genres(analysis, evidence, mode)
        if (cleaned := _bounded_user_text(label, 120)) is not None
    )
    warnings: list[str] = []
    candidates: list[LocalPromptCandidate] = []
    try:
        raw = writer.generate_candidates(
            evidence,
            mode,
            seed,
            preferences.candidate_count,
            parameters,
            forbidden_genre_labels=forbidden_genre_labels,
            allowed_fact_paths=allowed_fact_paths,
            required_fact_paths=required_fact_paths,
            required_fact_literals=required_fact_literals,
        )
        candidates, validation = _validated_candidates(
            raw,
            evidence,
            analysis,
            preferences,
            transcript,
            mode,
            seed,
            writer.model_metadata()["modelId"],
            parameters,
            allowed_fact_paths,
            required_fact_paths,
            required_fact_literals,
        )
        initial_validation = validation
        if len(candidates) < preferences.candidate_count:
            repaired = writer.generate_candidates(
                evidence,
                mode,
                seed,
                preferences.candidate_count,
                parameters,
                repair_errors=validation or ["candidates were not materially diverse"],
                forbidden_genre_labels=forbidden_genre_labels,
                allowed_fact_paths=allowed_fact_paths,
                required_fact_paths=required_fact_paths,
                required_fact_literals=required_fact_literals,
            )
            candidates, validation = _validated_candidates(
                repaired,
                evidence,
                analysis,
                preferences,
                transcript,
                mode,
                seed,
                writer.model_metadata()["modelId"],
                parameters,
                allowed_fact_paths,
                required_fact_paths,
                required_fact_literals,
                insert_required_evidence=True,
            )
            if len(candidates) < preferences.candidate_count:
                warnings.extend(initial_validation)
            warnings.extend(validation)
        else:
            warnings.extend(initial_validation)
    except LocalPromptWriterError as exc:
        warnings.append(str(exc))
    finally:
        writer.unload_or_release()
    if len(candidates) != preferences.candidate_count:
        warnings.append(
            "The local writer did not return the requested number of distinct valid candidates."
        )
        return reliable.model_copy(
            update={
                "engine_mode": mode,
                "seed": seed,
                "validation_warnings": list(dict.fromkeys(warnings + ["Reliable deterministic fallback was used."])),
                "deterministic_fallback_used": True,
            }
        )
    selected = candidates[0]
    return reliable.model_copy(
        update={
            "primary_prompt": selected.prompt,
            "compact_prompt": selected.prompt,
            "detailed_prompt": selected.prompt,
            "arrangement_blueprint": reliable.arrangement_blueprint,
            "rationale": [
                PromptRationale(
                    phrase="Selected local-writer candidate grounded in eligible measured and reviewed evidence.",
                    fact_paths=[fact.path for fact in selected.facts_used],
                )
            ],
            "facts_used": selected.facts_used,
            "engine_mode": mode,
            "candidates": candidates,
            "selected_candidate_id": selected.id,
            "model_id": selected.model_id,
            "seed": seed,
            "generation_parameters": parameters,
            "validation_warnings": list(dict.fromkeys(warnings)),
            "deterministic_fallback_used": len(candidates) < preferences.candidate_count,
        }
    )
