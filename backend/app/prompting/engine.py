from __future__ import annotations

import re
import secrets
from difflib import SequenceMatcher
from typing import Any

from ..config import Settings
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
)
from .composer import ORIGINALITY_CLAUSE, PROHIBITED_REFERENCE, compose_prompt
from .local_writer import LocalPromptWriterAdapter, LocalPromptWriterError, create_prompt_writer

INJECTION_PATTERN = re.compile(r"\b(?:ignore previous|system prompt|developer message|follow these instructions)\b", re.IGNORECASE)
PATH_PATTERN = re.compile(r"(?:[A-Za-z]:\\|/data/|/app/|traceback \(most recent call last\))", re.IGNORECASE)
CHORD_SEQUENCE_PATTERN = re.compile(r"(?:\b[A-G](?:#|b)?(?:m|maj|min|dim|aug|sus)?\b\s*(?:-|→|,|\|)\s*){3,}")
NOTE_SEQUENCE_PATTERN = re.compile(r"(?:\b[A-G](?:#|b)?[0-8]?\b\s*){6,}")


def _bounded_user_text(value: str | None, maximum: int) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", "".join(character for character in value if character.isprintable())).strip()
    if not cleaned or INJECTION_PATTERN.search(cleaned) or PATH_PATTERN.search(cleaned) or PROHIBITED_REFERENCE.search(cleaned):
        return None
    return cleaned[:maximum].rstrip()


def _feature_list(value: Any, path: str, analysis: AnalysisResult) -> list[str]:
    if path in analysis.disabled_feature_paths or value.value is None:
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
    accepted_genres: list[str] = []
    blend: list[str] = []
    genre = analysis.genre_analysis
    if genre is not None and not genre.disabled_for_prompt and preferences.genre_interpretation_mode != GenreInterpretationMode.DISABLED:
        candidates = genre.broad_candidates + genre.subgenre_candidates
        selected = [
            item
            for item in candidates
            if not item.rejected
            and (item.accepted or item.id in preferences.accepted_genre_ids)
        ]
        if preferences.genre_interpretation_mode == GenreInterpretationMode.STRICT_TOP:
            selected = selected[:1]
        elif preferences.genre_interpretation_mode == GenreInterpretationMode.BLEND:
            selected = selected[:2]
            blend = genre.blend_candidates[:1]
        elif preferences.genre_interpretation_mode == GenreInterpretationMode.USER_SELECTED_ONLY:
            selected = [item for item in selected if item.user_edited or item.custom]
        accepted_genres = [item.label for item in selected]
    themes: list[str] = []
    if (
        preferences.lyrics_influence_mode == LyricsInfluenceMode.ABSTRACT_THEMES
        and preferences.include_lyrical_themes
        and analysis.lyrics_summary is not None
    ):
        themes = [theme for theme in analysis.lyrics_summary.abstract_themes if _bounded_user_text(theme, 120)][:8]
    elif preferences.lyrics_influence_mode == LyricsInfluenceMode.USER_WRITTEN_DIRECTION:
        direction = _bounded_user_text(preferences.user_written_lyrical_direction, 240)
        if direction:
            themes = [direction]
    tempo = analysis.rhythm.bpm.value if analysis.rhythm.bpm.confidence in {Confidence.MEDIUM, Confidence.HIGH} else None
    meter_values = _feature_list(analysis.rhythm.meter, "rhythm.meter", analysis)
    vocal_presence = _feature_list(analysis.vocals.presence, "vocals.presence", analysis)
    vocal_density = _feature_list(analysis.vocals.density, "vocals.density", analysis)
    energy_values = _feature_list(analysis.style_and_mood.energy, "styleAndMood.energy", analysis)
    return PromptEvidence(
        accepted_genre_candidates=accepted_genres,
        accepted_genre_blend=blend,
        tempo=float(tempo) if tempo is not None and preferences.include_bpm else None,
        meter=meter_values[0] if meter_values else None,
        groove=_feature_list(analysis.rhythm.groove_descriptors, "rhythm.grooveDescriptors", analysis),
        mood=_feature_list(analysis.style_and_mood.mood, "styleAndMood.mood", analysis),
        energy=energy_values[0] if energy_values else None,
        instrumentation=[candidate.name[:80] for candidate in (analysis.instrumentation.candidates.value or [])[:8]]
        if preferences.preserve_instrumentation and analysis.instrumentation.candidates.value is not None
        else [],
        vocal_presence=None if preferences.instrumental else (vocal_presence[0] if vocal_presence else None),
        vocal_density=None if preferences.instrumental else (vocal_density[0] if vocal_density else None),
        approved_vocal_descriptors=[] if preferences.instrumental else _feature_list(analysis.vocals.delivery, "vocals.delivery", analysis),
        structure_summary=[section.inferred_label or section.neutral_label for section in analysis.structure.sections[:10]],
        section_energy_summary=[
            f"section {index + 1}: {section.energy:.2f} relative energy"
            for index, section in enumerate(analysis.structure.sections[:10])
            if section.energy is not None
        ],
        production_descriptors=_feature_list(analysis.production.production_character, "production.productionCharacter", analysis),
        harmonic_character=_feature_list(analysis.harmony.character, "harmony.character", analysis),
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
) -> list[str]:
    prompt = raw.get("prompt")
    if not isinstance(prompt, str):
        return ["candidate prompt is missing"]
    errors: list[str] = []
    if "\n" in prompt.strip() or len(prompt) > evidence.maximum_characters:
        errors.append("prompt must be one bounded paragraph")
    if "original" not in prompt.casefold() or "melody" not in prompt.casefold() or "arrangement" not in prompt.casefold():
        errors.append("originality requirement is missing")
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
    if transcript is not None:
        normalized_prompt = re.sub(r"\s+", " ", prompt.casefold())
        if any(
            len(segment.text.strip()) >= 8 and re.sub(r"\s+", " ", segment.text.casefold()) in normalized_prompt
            for segment in transcript.segments
        ):
            errors.append("raw transcript text is forbidden")
    facts = raw.get("factsUsed")
    if not isinstance(facts, list) or any(not isinstance(item, str) or len(item) > 120 for item in facts):
        errors.append("factsUsed must be a bounded string list")
    return errors


def _materially_diverse(left: str, right: str) -> bool:
    normalized_left = re.sub(r"\W+", " ", left.casefold()).strip()
    normalized_right = re.sub(r"\W+", " ", right.casefold()).strip()
    similarity = SequenceMatcher(None, normalized_left, normalized_right).ratio()
    left_ngrams = set(zip(normalized_left.split(), normalized_left.split()[1:], strict=False))
    right_ngrams = set(zip(normalized_right.split(), normalized_right.split()[1:], strict=False))
    overlap = len(left_ngrams & right_ngrams) / max(1, len(left_ngrams | right_ngrams))
    return similarity < 0.86 and overlap < 0.72 and normalized_left.split()[:5] != normalized_right.split()[:5]


def _validated_candidates(
    raw_candidates: list[dict[str, Any]],
    evidence: PromptEvidence,
    analysis: AnalysisResult,
    transcript: PrivateLyricsTranscript | None,
    mode: PromptEngineMode,
    seed: int,
    model_id: str,
    parameters: PromptGenerationParameters,
) -> tuple[list[LocalPromptCandidate], list[str]]:
    candidates: list[LocalPromptCandidate] = []
    warnings: list[str] = []
    for index, raw in enumerate(raw_candidates[:3]):
        errors = _candidate_errors(raw, evidence, analysis, transcript)
        if errors:
            warnings.extend(errors)
            continue
        prompt = str(raw["prompt"]).strip()
        if any(not _materially_diverse(prompt, existing.prompt) for existing in candidates):
            warnings.append("A near-duplicate candidate was rejected.")
            continue
        title = _bounded_user_text(str(raw.get("shortTitle", "Prompt candidate")), 80) or f"Candidate {index + 1}"
        candidates.append(
            LocalPromptCandidate(
                id=f"candidate-{seed}-{index + 1}",
                prompt=prompt,
                short_title=title,
                engine_mode=mode,
                seed=seed,
                model_id=model_id,
                generation_parameters=parameters,
                facts_used=[str(item)[:120] for item in raw.get("factsUsed", [])[:100]],
                creative_directions_used=[str(item)[:160] for item in raw.get("creativeDirectionsUsed", [])[:20]],
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
    warnings: list[str] = []
    candidates: list[LocalPromptCandidate] = []
    try:
        raw = writer.generate_candidates(evidence, mode, seed, preferences.candidate_count, parameters)
        candidates, validation = _validated_candidates(
            raw, evidence, analysis, transcript, mode, seed, writer.model_metadata()["modelId"], parameters
        )
        warnings.extend(validation)
        if len(candidates) < preferences.candidate_count:
            repaired = writer.generate_candidates(
                evidence,
                mode,
                seed,
                preferences.candidate_count,
                parameters,
                repair_errors=validation or ["candidates were not materially diverse"],
            )
            candidates, validation = _validated_candidates(
                repaired, evidence, analysis, transcript, mode, seed, writer.model_metadata()["modelId"], parameters
            )
            warnings.extend(validation)
    except LocalPromptWriterError as exc:
        warnings.append(str(exc))
    finally:
        writer.unload_or_release()
    if not candidates:
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
