from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..schemas import (
    AnalysisResult,
    Confidence,
    FeatureValue,
    GenreInterpretationMode,
    LyricsInfluenceMode,
    OmittedFact,
    PromptFact,
    PromptLength,
    PromptPackage,
    PromptPreferences,
    PromptRationale,
)
from ..taxonomies.music_styles import load_music_style_taxonomy

ORIGINALITY_CLAUSE = (
    "Create an original melody, arrangement, and any lyrics rather than reproducing the reference recording."
)
PROHIBITED_REFERENCE = re.compile(
    r"\b(?:in\s+the\s+style\s+of|sounds?\s+like|imitat(?:e|ing)|copy(?:ing)?|clone(?:d|ing)?|artist)\b",
    re.IGNORECASE,
)
CONTRADICTIONS = {
    "bright": "dark",
    "dark": "bright",
    "sparse": "dense",
    "dense": "sparse",
    "polished": "raw",
    "raw": "polished",
    "major-key": "minor-key",
    "minor-key": "major-key",
}
SAFE_USER_TEXT_TERMS = {
    "a", "a-flat", "a-sharp", "acoustic", "afrobeat", "airy", "alternative", "ambient", "an", "analog",
    "abrupt", "and", "art", "atmospheric", "avoid", "balanced", "bass", "blues", "breathy",
    "b", "b-flat", "b-sharp", "bright", "but", "c", "c-flat", "c-sharp", "cello", "choral", "chops", "chorus", "cinematic", "clarinet", "classical", "clean",
    "bluegrass", "controlled", "country", "dance", "dark", "deep", "dense", "density", "disco", "distorted", "dubstep",
    "d", "d-flat", "d-sharp", "dream", "dreamy", "driving", "drum", "drums", "dynamic", "e", "e-flat", "e-sharp", "electric", "electronic", "ending",
    "energetic", "experimental", "f", "f-flat", "f-sharp", "female", "flute", "focused", "folk", "forceful", "four", "funk",
    "g", "g-flat", "g-sharp", "garage", "gentle", "glitch", "gospel", "gritty", "groove", "grunge", "guitar", "hard",
    "harmonies", "harsh", "high", "hip", "hop", "house", "indie", "instrumental", "intimate",
    "intro", "jazz", "latin", "layered", "lead", "light", "lo-fi", "long", "low",
    "low-register", "major", "make", "male", "mastering", "melancholic", "metal", "mid",
    "mandolin", "minimal", "minor", "modern", "nasal", "neo", "no", "nocturnal", "nonbinary", "oboe", "organic",
    "orchestral", "orchestration", "outro", "overly", "pads", "percussion", "piano", "polished", "pop",
    "post", "powerful", "progressive", "punk", "r&b", "rap", "raw", "reggae", "register",
    "raspy", "reverb", "rising", "rock", "saxophone", "shoegaze", "sibilance", "smooth", "soft", "soul", "sparse",
    "spoken", "steady", "strings", "synth", "synths", "synthwave", "techno", "tense", "textured", "the",
    "three", "track", "trap", "trombone", "trumpet", "vibrato", "viola", "violin", "vocal", "vocals", "warm", "whispered", "wide", "with",
    "without", "world",
    "melodic", "contemporary", "neo-soul", "breakbeat", "drum-and-bass", "songwriter",
    # Brief-listed instrumentation, vocal-performance, timbre, and mix terms.
    # Unknown words still reject the entire user-controlled phrase, which keeps
    # named artists and other identity-bearing references out of prompts.
    "ambience", "arpeggiator", "arpeggiators", "backing", "bass-guitar", "brass",
    "breathiness", "call-and-response", "choir", "choir-like", "clarity", "clipping",
    "congestion", "crisp", "delay", "distortion", "doubles", "dynamics", "electric-piano",
    "evolving", "expansive", "formant", "formant-processing", "found-sound", "harshness",
    "layering", "leads", "legato", "low-end", "melismatic", "midrange", "or", "organ",
    "percussive", "phrasing", "placement", "pluck", "plucks", "processed", "punch", "punchy", "range",
    "rounded", "saturated", "saturation", "shouted", "stacked", "staccato", "stereo",
    "stripped-back", "sub-bass", "sung", "sustained", "syncopated", "synthetic",
    "synthesized", "synthesizer", "synthesizers", "texture", "textures", "transient",
    "tendency", "transients", "tuning", "versus", "vocoder", "weight", "width", "woodwind", "woodwinds",
}


@dataclass(frozen=True, slots=True)
class Phrase:
    text: str
    facts: tuple[str, ...]
    priority: int
    group: str


def _clean_text(value: str, *, maximum: int = 160) -> str | None:
    value = "".join(character for character in value if character.isprintable())
    value = re.sub(r"\s+", " ", value).strip(" ,.;:-")
    if not value or PROHIBITED_REFERENCE.search(value):
        return None
    return value[:maximum].rstrip()


def _clean_user_text(value: str, *, maximum: int = 160) -> str | None:
    cleaned = _clean_text(value, maximum=maximum)
    if cleaned is None:
        return None
    tokens = re.findall(r"[^\W\d_]+(?:['’&-][^\W\d_]+)*", cleaned, flags=re.UNICODE)
    # User-controlled prose is never treated as an open vocabulary. This
    # deliberately omits unfamiliar descriptors so a named artist cannot be
    # smuggled beside an otherwise safe word (for example, "dark <name>").
    if any(token.casefold() not in SAFE_USER_TEXT_TERMS for token in tokens):
        return None
    return cleaned


def _clean_user_key(value: str, *, maximum: int = 20) -> str | None:
    cleaned = _clean_text(value, maximum=maximum)
    if cleaned is None:
        return None
    if re.fullmatch(r"[A-Ga-g](?:[#b♯♭]|-flat|-sharp)?", cleaned) is None:
        return None
    return cleaned


def _clean_reviewed_genre_candidate(candidate: Any, *, maximum: int = 100) -> str | None:
    """Keep exact taxonomy labels authoritative while sanitizing edited labels.

    Taxonomy labels are reviewed application data, not open-vocabulary user
    input. A custom or changed label still goes through the closed user-text
    vocabulary used by the genre PATCH boundary.
    """

    taxonomy = load_music_style_taxonomy()
    reviewed = {
        item.id: item.prompt_safe_label
        for item in [*taxonomy.broad_genres, *taxonomy.subgenres]
    }.get(candidate.id)
    if (
        reviewed is not None
        and not candidate.custom
        and candidate.label == candidate.canonical_label == reviewed
    ):
        return _clean_text(reviewed, maximum=maximum)
    return _clean_user_text(candidate.label, maximum=maximum)


def _is_eligible(
    value: FeatureValue[Any],
    path: str,
    disabled: set[str],
    omitted: list[OmittedFact],
) -> bool:
    if path in disabled:
        omitted.append(OmittedFact(path=path, reason="disabled by the user"))
        return False
    if value.value is None:
        omitted.append(OmittedFact(path=path, reason="no usable value"))
        return False
    if (
        value.confidence not in {Confidence.MEDIUM, Confidence.HIGH, "medium", "high"}
        and not value.user_edited
        and not value.user_accepted
    ):
        omitted.append(OmittedFact(path=path, reason="confidence below the prompt threshold"))
        return False
    return True


def _descriptors(
    values: list[str],
    *,
    user_supplied: bool = False,
    on_rejected: Callable[[], None] | None = None,
) -> list[str]:
    selected: list[str] = []
    lowered: set[str] = set()
    for raw in values:
        cleaner = _clean_user_text if user_supplied else _clean_text
        cleaned = cleaner(str(raw), maximum=80)
        if not cleaned:
            if user_supplied and str(raw).strip() and on_rejected is not None:
                on_rejected()
            continue
        words = {word.lower() for word in re.findall(r"[\w-]+", cleaned)}
        if any(CONTRADICTIONS.get(word) in lowered for word in words):
            continue
        if cleaned.lower() in {value.lower() for value in selected}:
            continue
        selected.append(cleaned)
        lowered.update(words)
    return selected


def _safe_analysis_values(analysis: AnalysisResult) -> set[str]:
    display_name = analysis.file.display_name.lower()
    stem = analysis.file.display_name.rsplit(".", 1)[0].lower()
    unsafe = {display_name}
    # A filename such as ``rock.wav`` is not evidence that the word "rock" is
    # a source identity. Treat a stem made entirely from the same closed musical
    # vocabulary accepted for user preferences as generic. Distinctive stems
    # remain identity blockers, and the full display name is always blocked.
    if _clean_user_text(stem, maximum=160) is None:
        unsafe.add(stem)
    unsafe.update(value.lower() for value in analysis.file.private_metadata.values() if value)
    return {value for value in unsafe if len(value) >= 3}


def _contains_source_identity(text: str, unsafe_values: set[str]) -> bool:
    lowered = text.lower()
    return any(value in lowered for value in unsafe_values)


def _select_synonym(seed: int | None, creativity: float, choices: tuple[str, ...]) -> str:
    if seed is None:
        return choices[min(len(choices) - 1, int(creativity * len(choices)))]
    return choices[(seed + int(creativity * 10)) % len(choices)]


def _build_phrases(
    analysis: AnalysisResult,
    preferences: PromptPreferences,
    omitted: list[OmittedFact],
    warnings: list[str],
) -> list[Phrase]:
    disabled = set(analysis.disabled_feature_paths) | set(preferences.disabled_feature_paths)
    phrases: list[Phrase] = []
    unsafe_values = _safe_analysis_values(analysis)

    def record_rejected_user_edit(path: str) -> None:
        if not any(item.path == path and "safe musical vocabulary" in item.reason for item in omitted):
            omitted.append(
                OmittedFact(
                    path=path,
                    reason="user-edited value was outside the safe musical vocabulary",
                )
            )
        warning = f"A user-edited value at {path} was omitted because it was outside the safe musical vocabulary."
        if warning not in warnings:
            warnings.append(warning)

    target_genre = _clean_user_text(preferences.target_genre or "", maximum=100)
    if preferences.target_genre and target_genre is None:
        warnings.append("A named-style or unsafe target-genre reference was omitted.")
    genre = analysis.genre_analysis
    if target_genre:
        phrases.append(Phrase(target_genre, ("preferences.targetGenre",), 100, "style"))
    elif (
        genre is not None
        and not genre.disabled_for_prompt
        and preferences.include_detected_genre
        and preferences.genre_interpretation_mode == GenreInterpretationMode.DETECTED_LAYERED
    ):
        layered = genre.overall_genre_blend
        layered_text = (
            _clean_text(str(layered.value), maximum=220)
            if layered is not None and isinstance(layered.value, str)
            else None
        )
        if layered_text and layered_text != "genre blend unavailable":
            layer_paths = ["genreAnalysis.overallGenreBlend"]
            if genre.primary_production_genre is not None:
                layer_paths.append("genreAnalysis.primaryProductionGenre")
            if genre.secondary_production_genres is not None and genre.secondary_production_genres.value:
                layer_paths.append("genreAnalysis.secondaryProductionGenres")
            if genre.vocal_delivery_style is not None and genre.vocal_delivery_style.value:
                layer_paths.append("genreAnalysis.vocalDeliveryStyle")
            if genre.vocal_genre_influences is not None and genre.vocal_genre_influences.value:
                layer_paths.append("genreAnalysis.vocalGenreInfluences")
            phrases.append(Phrase(layered_text, tuple(layer_paths), 94, "style"))
            if genre.ambiguity:
                warnings.append(
                    "Genre analysis is available but ambiguous; the eligible layered blend was included with explicit uncertainty."
                )
        else:
            warnings.append("Genre analysis is available, but no eligible layered blend could be constructed.")
    elif (
        genre is not None
        and not genre.disabled_for_prompt
        and preferences.include_detected_genre
        and preferences.genre_interpretation_mode != GenreInterpretationMode.DISABLED
    ):
        detected_candidates = [
            candidate
            for candidate in (genre.broad_candidates + genre.subgenre_candidates)
            if not candidate.rejected and candidate.accepted
        ]
        if preferences.accepted_genre_ids:
            accepted_id_filter = set(preferences.accepted_genre_ids)
            detected_candidates = [
                candidate for candidate in detected_candidates if candidate.id in accepted_id_filter
            ]
        if preferences.genre_interpretation_mode == GenreInterpretationMode.USER_SELECTED_ONLY:
            detected_candidates = [
                candidate for candidate in detected_candidates if candidate.user_edited or candidate.custom
            ]
        elif preferences.genre_interpretation_mode == GenreInterpretationMode.BLEND:
            detected_candidates = detected_candidates[:2]
        else:
            detected_candidates = detected_candidates[:1]
        safe_detected: list[tuple[Any, str]] = []
        for candidate in detected_candidates:
            cleaned_candidate = _clean_reviewed_genre_candidate(candidate, maximum=100)
            if cleaned_candidate is not None:
                safe_detected.append((candidate, cleaned_candidate))
        if safe_detected:
            if (
                preferences.genre_interpretation_mode == GenreInterpretationMode.BLEND
                and genre.overall_genre_blend is not None
                and isinstance(genre.overall_genre_blend.value, str)
            ):
                style_text = _clean_text(genre.overall_genre_blend.value, maximum=220) or safe_detected[0][1]
                facts = (
                    *(f"genreAnalysis.accepted.{candidate.id}" for candidate, _ in safe_detected),
                    "genreAnalysis.overallGenreBlend",
                )
                if genre.vocal_genre_influences is not None and genre.vocal_genre_influences.value:
                    facts = (*facts, "genreAnalysis.vocalGenreInfluences")
            else:
                style_text = (
                    safe_detected[0][1]
                    if len(safe_detected) == 1
                    else f"{safe_detected[0][1]} with {' and '.join(label for _, label in safe_detected[1:])} influence"
                )
                facts = tuple(f"genreAnalysis.accepted.{candidate.id}" for candidate, _ in safe_detected)
            phrases.append(Phrase(style_text, facts, 92, "style"))
        else:
            warnings.append(
                "Detected genre candidates were not included because none were accepted for prompt use."
            )
    elif (
        analysis.genre_analysis is None
        and preferences.include_detected_genre
        and preferences.genre_interpretation_mode != GenreInterpretationMode.DISABLED
        and _is_eligible(
            analysis.style_and_mood.broad_style,
            "styleAndMood.broadStyle",
            disabled,
            omitted,
        )
    ):
        values = _descriptors(
            [str(value) for value in analysis.style_and_mood.broad_style.value or []],
            user_supplied=analysis.style_and_mood.broad_style.user_edited,
            on_rejected=lambda: record_rejected_user_edit("styleAndMood.broadStyle"),
        )
        if values:
            text = " / ".join(values[:2])
            if not _contains_source_identity(text, unsafe_values):
                phrases.append(Phrase(text, ("styleAndMood.broadStyle",), 90, "style"))
            else:
                omitted.append(OmittedFact(path="styleAndMood.broadStyle", reason="matched private source identity"))
    if genre is not None and not target_genre:
        selected_genre_ids = {
            fact.rsplit(".", 1)[-1]
            for phrase in phrases
            for fact in phrase.facts
            if fact.startswith("genreAnalysis.accepted.")
        }
        layered_values: set[str] = set()
        if (
            preferences.genre_interpretation_mode == GenreInterpretationMode.DETECTED_LAYERED
            and not genre.disabled_for_prompt
            and preferences.include_detected_genre
        ):
            for layer in (
                genre.primary_production_genre,
                genre.secondary_production_genres,
                genre.vocal_genre_influences,
            ):
                if layer is None:
                    continue
                values = [layer.value] if isinstance(layer.value, str) else layer.value
                layered_values.update(str(value).casefold() for value in values)
        for candidate in genre.broad_candidates + genre.subgenre_candidates:
            if candidate.id in selected_genre_ids or candidate.label.casefold() in layered_values:
                continue
            if genre.disabled_for_prompt or preferences.genre_interpretation_mode == GenreInterpretationMode.DISABLED:
                reason = "genre evidence was disabled for prompt use"
            elif candidate.rejected:
                reason = "genre candidate was rejected by the user"
            elif preferences.genre_interpretation_mode != GenreInterpretationMode.DETECTED_LAYERED and not candidate.accepted:
                reason = "detected genre candidate was not accepted for this prompt mode"
            else:
                reason = "lower-ranked genre alternative was not selected by the active prompt mode"
            omitted.append(OmittedFact(path=f"genreAnalysis.candidates.{candidate.id}", reason=reason))
    if not any(phrase.group == "style" for phrase in phrases):
        phrases.append(
            Phrase(
                "Original production guided by the measured groove, timbre, and arrangement",
                tuple(),
                20,
                "style",
            )
        )
        if genre is not None and genre.disabled_for_prompt:
            warnings.append("Genre analysis is available but disabled for prompt use.")
        elif preferences.genre_interpretation_mode == GenreInterpretationMode.DISABLED or not preferences.include_detected_genre:
            warnings.append("Genre analysis was not used because detected genre evidence is disabled in prompt preferences.")
        elif genre is None and any("adapter failed" in warning.casefold() for warning in analysis.warnings):
            warnings.append("Genre analysis is unavailable because the enabled adapter did not return a usable result.")
        elif genre is None:
            warnings.append("The local genre adapter is unavailable; measured non-genre evidence was used instead.")

    rhythm_parts: list[str] = []
    rhythm_facts: list[str] = []
    if preferences.include_bpm and _is_eligible(analysis.rhythm.bpm, "rhythm.bpm", disabled, omitted):
        qualifier = _select_synonym(preferences.variation_seed, preferences.creativity, ("around", "approximately", "near"))
        rhythm_parts.append(f"{qualifier} {round(float(analysis.rhythm.bpm.value or 0))} BPM")
        rhythm_facts.append("rhythm.bpm")
    if _is_eligible(analysis.rhythm.meter, "rhythm.meter", disabled, omitted):
        meter_cleaner = _clean_user_text if analysis.rhythm.meter.user_edited else _clean_text
        meter = meter_cleaner(str(analysis.rhythm.meter.value), maximum=40)
        if meter is None and analysis.rhythm.meter.user_edited:
            record_rejected_user_edit("rhythm.meter")
        if meter and meter != "unknown":
            rhythm_parts.append(meter)
            rhythm_facts.append("rhythm.meter")
    if preferences.preserve_groove and _is_eligible(
        analysis.rhythm.groove_descriptors, "rhythm.grooveDescriptors", disabled, omitted
    ):
        groove = _descriptors(
            [str(value) for value in analysis.rhythm.groove_descriptors.value or []],
            user_supplied=analysis.rhythm.groove_descriptors.user_edited,
            on_rejected=lambda: record_rejected_user_edit("rhythm.grooveDescriptors"),
        )
        if groove:
            rhythm_parts.append(f"a {', '.join(groove[:2])} groove")
            rhythm_facts.append("rhythm.grooveDescriptors")
    if rhythm_parts:
        phrases.append(Phrase(", ".join(rhythm_parts), tuple(rhythm_facts), 85, "rhythm"))

    mood_parts: list[str] = []
    mood_facts: list[str] = []
    target_mood = _clean_user_text(preferences.target_mood or "", maximum=100)
    if preferences.target_mood and target_mood is None:
        warnings.append("An unsafe target-mood reference was omitted.")
    if target_mood:
        mood_parts.append(target_mood)
        mood_facts.append("preferences.targetMood")
    elif _is_eligible(analysis.style_and_mood.mood, "styleAndMood.mood", disabled, omitted):
        mood_parts.extend(
            _descriptors(
                [str(value) for value in analysis.style_and_mood.mood.value or []],
                user_supplied=analysis.style_and_mood.mood.user_edited,
                on_rejected=lambda: record_rejected_user_edit("styleAndMood.mood"),
            )[:2]
        )
        mood_facts.append("styleAndMood.mood")
    if _is_eligible(analysis.style_and_mood.energy, "styleAndMood.energy", disabled, omitted):
        energy_cleaner = _clean_user_text if analysis.style_and_mood.energy.user_edited else _clean_text
        energy = energy_cleaner(str(analysis.style_and_mood.energy.value), maximum=40)
        if energy is None and analysis.style_and_mood.energy.user_edited:
            record_rejected_user_edit("styleAndMood.energy")
        if energy:
            mood_parts.append(f"{energy} energy")
            mood_facts.append("styleAndMood.energy")
    if mood_parts:
        phrases.append(Phrase(", ".join(_descriptors(mood_parts)), tuple(mood_facts), 80, "mood"))

    if preferences.preserve_instrumentation and _is_eligible(
        analysis.instrumentation.candidates, "instrumentation.candidates", disabled, omitted
    ):
        names = _descriptors(
            [candidate.name for candidate in analysis.instrumentation.candidates.value or []],
            user_supplied=analysis.instrumentation.candidates.user_edited,
            on_rejected=lambda: record_rejected_user_edit("instrumentation.candidates"),
        )
        if names:
            phrases.append(Phrase(f"centered on {', '.join(names[:5])}", ("instrumentation.candidates",), 78, "instruments"))

    harmony_parts: list[str] = []
    harmony_facts: list[str] = []
    if preferences.include_key and _is_eligible(analysis.harmony.key, "harmony.key", disabled, omitted) and _is_eligible(
        analysis.harmony.mode, "harmony.mode", disabled, omitted
    ):
        key_cleaner = _clean_user_key if analysis.harmony.key.user_edited else _clean_text
        mode_cleaner = _clean_user_text if analysis.harmony.mode.user_edited else _clean_text
        key = key_cleaner(str(analysis.harmony.key.value), maximum=20)
        mode = mode_cleaner(str(analysis.harmony.mode.value), maximum=20)
        if key is None and analysis.harmony.key.user_edited:
            record_rejected_user_edit("harmony.key")
        if mode is None and analysis.harmony.mode.user_edited:
            record_rejected_user_edit("harmony.mode")
        if key and mode:
            harmony_parts.append(f"a {key} {mode} tonal center")
            harmony_facts.extend(("harmony.key", "harmony.mode"))
    if _is_eligible(analysis.harmony.character, "harmony.character", disabled, omitted):
        harmony_parts.extend(
            _descriptors(
                [str(value) for value in analysis.harmony.character.value or []],
                user_supplied=analysis.harmony.character.user_edited,
                on_rejected=lambda: record_rejected_user_edit("harmony.character"),
            )[:2]
        )
        harmony_facts.append("harmony.character")
    if harmony_parts:
        phrases.append(Phrase(", ".join(_descriptors(harmony_parts)), tuple(harmony_facts), 68, "harmony"))

    if not preferences.instrumental:
        desired_vocal = _clean_user_text(preferences.desired_vocal_presentation or "", maximum=160)
        if preferences.desired_vocal_presentation and desired_vocal is None:
            warnings.append("An unsafe named-style vocal preference was omitted.")
        if desired_vocal:
            phrases.append(Phrase(f"vocals with {desired_vocal}", ("preferences.desiredVocalPresentation",), 74, "vocals"))
        elif _is_eligible(analysis.vocals.presence, "vocals.presence", disabled, omitted):
            presence = str(analysis.vocals.presence.value).lower()
            if presence == "present":
                delivery: list[str] = []
                if _is_eligible(analysis.vocals.delivery, "vocals.delivery", disabled, omitted):
                    delivery = _descriptors(
                        [str(value) for value in analysis.vocals.delivery.value or []],
                        user_supplied=analysis.vocals.delivery.user_edited,
                        on_rejected=lambda: record_rejected_user_edit("vocals.delivery"),
                    )
                if delivery:
                    phrases.append(Phrase(f"vocals with {', '.join(delivery[:3])} delivery", ("vocals.presence", "vocals.delivery"), 72, "vocals"))
                else:
                    density = analysis.vocals.density
                    density_eligible = _is_eligible(
                        density,
                        "vocals.density",
                        disabled,
                        omitted,
                    )
                    density_value = (
                        str(density.value)
                        if density_eligible
                        else "audible"
                    )
                    phrases.append(
                        Phrase(
                            f"{density_value} vocals without inventing register or delivery",
                            (
                                ("vocals.presence", "vocals.density")
                                if density_eligible
                                else ("vocals.presence",)
                            ),
                            72,
                            "vocals",
                        )
                    )
    else:
        phrases.append(Phrase("fully instrumental, with no vocals", ("preferences.instrumental",), 100, "vocals"))

    if not preferences.instrumental and analysis.lyrics_summary is not None:
        if (
            preferences.lyrics_influence_mode == LyricsInfluenceMode.PROSODY_ONLY
            and analysis.lyrics_summary.vocal_word_density in {"sparse", "moderate", "dense"}
        ):
            phrases.append(
                Phrase(
                    f"Use {analysis.lyrics_summary.vocal_word_density} vocal phrasing without quoting the source words",
                    ("lyricsSummary.vocalWordDensity",),
                    60,
                    "lyrics-direction",
                )
            )
        elif (
            preferences.lyrics_influence_mode == LyricsInfluenceMode.ABSTRACT_THEMES
            and preferences.include_lyrical_themes
            and analysis.lyrics_summary.themes_user_approved
        ):
            themes = _descriptors(analysis.lyrics_summary.abstract_themes)[:3]
            if themes:
                phrases.append(
                    Phrase(
                        f"Write wholly original lyrics around the abstract themes of {', '.join(themes)}",
                        ("lyricsSummary.abstractThemes",),
                        60,
                        "lyrics-direction",
                    )
                )
        elif preferences.lyrics_influence_mode == LyricsInfluenceMode.USER_WRITTEN_DIRECTION:
            lyrical_direction = _clean_user_text(
                preferences.user_written_lyrical_direction or "",
                maximum=200,
            )
            if lyrical_direction:
                phrases.append(
                    Phrase(
                        f"Use wholly original lyrics with this direction: {lyrical_direction}",
                        ("preferences.userWrittenLyricalDirection",),
                        62,
                        "lyrics-direction",
                    )
                )

    if preferences.preserve_energy_arc and _is_eligible(
        analysis.structure.energy_arc, "structure.energyArc", disabled, omitted
    ):
        arc_cleaner = _clean_user_text if analysis.structure.energy_arc.user_edited else _clean_text
        arc = arc_cleaner(str(analysis.structure.energy_arc.value), maximum=80)
        if arc is None and analysis.structure.energy_arc.user_edited:
            record_rejected_user_edit("structure.energyArc")
        if arc:
            verb = _select_synonym(preferences.variation_seed, preferences.creativity, ("Shape the arrangement with", "Develop the track through", "Let the arrangement follow"))
            phrases.append(Phrase(f"{verb} a {arc} energy arc", ("structure.energyArc",), 70, "arrangement"))
    if preferences.preserve_structure and len(analysis.structure.sections) >= 2:
        first_label_path = "structure.sections.0.inferredLabel"
        last_label_path = f"structure.sections.{len(analysis.structure.sections) - 1}.inferredLabel"
        first_label = (
            None
            if first_label_path in disabled
            else analysis.structure.sections[0].inferred_label
        )
        last_label = (
            None
            if last_label_path in disabled
            else analysis.structure.sections[-1].inferred_label
        )
        shape = "then ".join(
            filter(
                None,
                [
                    "a restrained opening, " if first_label == "intro" else "",
                    "contrasting neutral sections, ",
                    "a resolved outro" if last_label == "outro" else "a decisive ending",
                ],
            )
        )
        phrases.append(Phrase(f"Arrange it with {shape}", ("structure.sections",), 58, "structure"))
        deep_sections = [
            section
            for section in analysis.structure.sections
            if section.deep_evidence is not None
        ]
        if deep_sections:
            vocal_sections = sum(
                section.deep_evidence is not None
                and section.deep_evidence.activity.get("vocals") in {"present", "prominent"}
                for section in deep_sections
            )
            drum_sections = sum(
                section.deep_evidence is not None
                and section.deep_evidence.activity.get("drums") == "prominent"
                for section in deep_sections
            )
            details: list[str] = []
            if 0 < vocal_sections < len(deep_sections):
                details.append("let vocals enter and recede between sections")
            if drum_sections:
                details.append("use drum prominence to articulate the strongest sections")
            if details:
                phrases.append(
                    Phrase(
                        "From the stem-aware section map, " + " and ".join(details),
                        ("structure.sections",),
                        66,
                        "deep-arrangement",
                    )
                )
        if _is_eligible(
            analysis.structure.repetition_summary,
            "structure.repetitionSummary",
            disabled,
            omitted,
        ):
            repetition = str(analysis.structure.repetition_summary.value or "").casefold()
            if "repeat" in repetition:
                phrases.append(
                    Phrase(
                        "Use a deliberately repetitive sectional framework",
                        ("structure.repetitionSummary",),
                        62,
                        "repetition",
                    )
                )

    production_parts: list[str] = []
    production_facts: list[str] = []
    if _is_eligible(analysis.production.production_character, "production.productionCharacter", disabled, omitted):
        production_parts.extend(
            _descriptors(
                [str(value) for value in analysis.production.production_character.value or []],
                user_supplied=analysis.production.production_character.user_edited,
                on_rejected=lambda: record_rejected_user_edit("production.productionCharacter"),
            )[:3]
        )
        production_facts.append("production.productionCharacter")
    if _is_eligible(analysis.timbre.descriptors, "timbre.descriptors", disabled, omitted):
        production_parts.extend(
            _descriptors(
                [str(value) for value in analysis.timbre.descriptors.value or []],
                user_supplied=analysis.timbre.descriptors.user_edited,
                on_rejected=lambda: record_rejected_user_edit("timbre.descriptors"),
            )[:2]
        )
        production_facts.append("timbre.descriptors")
    if _is_eligible(analysis.production.low_end_weight, "production.lowEndWeight", disabled, omitted):
        low_end = _clean_text(str(analysis.production.low_end_weight.value), maximum=60)
        if low_end:
            production_parts.append(f"{low_end} low end")
            production_facts.append("production.lowEndWeight")
    if _is_eligible(
        analysis.production.spaciousness_proxy,
        "production.spaciousnessProxy",
        disabled,
        omitted,
    ):
        stereo = _clean_text(str(analysis.production.spaciousness_proxy.value), maximum=60)
        if stereo:
            production_parts.append(f"{stereo} stereo image")
            production_facts.append("production.spaciousnessProxy")
    if production_parts:
        phrases.append(Phrase(f"{', '.join(_descriptors(production_parts))} production", tuple(production_facts), 64, "production"))

    if preferences.target_duration is not None:
        phrases.append(
            Phrase(
                f"Aim for roughly {round(preferences.target_duration)} seconds",
                ("preferences.targetDuration",),
                52,
                "duration",
            )
        )
    if preferences.creativity >= 0.67:
        phrases.append(
            Phrase(
                "Favor bolder textural turns and less predictable transitions",
                ("preferences.creativity",),
                54,
                "creativity",
            )
        )
    elif preferences.creativity <= 0.2:
        phrases.append(
            Phrase(
                "Keep the musical language focused and controlled",
                ("preferences.creativity",),
                54,
                "creativity",
            )
        )

    intent_phrases = {
        "inspired_variation": "Keep the musical character while taking the composition in a clearly new direction",
        "more_original": "Use the analysis only as a loose springboard and favor unexpected choices",
        "genre_transfer": "Translate the groove and energy arc into the requested genre",
        "instrumental_reinterpretation": "Reimagine the material as a fully instrumental composition",
        "change_mood_preserve_groove": "Retain the rhythmic feel while transforming the mood",
        "change_instrumentation_preserve_structure": "Retain the broad sectional shape while replacing the instrumental palette",
        "custom": "Follow the selected creative preferences while remaining compositionally distinct",
    }
    intent = str(preferences.generation_intent)
    if intent == "genre_transfer" and target_genre is None:
        warning = "Genre transfer was omitted because it requires a safe nonblank target genre."
        if warning not in warnings:
            warnings.append(warning)
    elif intent in intent_phrases:
        phrases.append(Phrase(intent_phrases[intent], ("preferences.generationIntent",), 88, "intent"))

    # Enforce source-identity exclusion after every phrase is assembled.
    filtered: list[Phrase] = []
    seen_text: set[str] = set()
    seen_groups: dict[str, int] = {}
    for phrase in phrases:
        if _contains_source_identity(phrase.text, unsafe_values) or PROHIBITED_REFERENCE.search(phrase.text):
            warnings.append("A phrase matching source identity or named-style language was omitted.")
            continue
        normalized = phrase.text.casefold()
        if normalized in seen_text:
            continue
        # User/target phrases with higher priority replace an analyzer phrase in
        # the same exclusive group; multi-part groups are composed beforehand.
        if phrase.group in seen_groups:
            previous_index = seen_groups[phrase.group]
            if phrase.priority > filtered[previous_index].priority:
                filtered[previous_index] = phrase
            continue
        seen_groups[phrase.group] = len(filtered)
        seen_text.add(normalized)
        filtered.append(phrase)
    # Resolve contradictions across phrase groups. Higher-salience phrases win;
    # equal-priority ties preserve canonical musical ordering.
    removed: set[int] = set()
    token_sets = [{word.lower() for word in re.findall(r"[\w-]+", phrase.text)} for phrase in filtered]
    for left_index, left_tokens in enumerate(token_sets):
        for right_index in range(left_index + 1, len(filtered)):
            right_tokens = token_sets[right_index]
            conflict = any(CONTRADICTIONS.get(token) in right_tokens for token in left_tokens)
            if not conflict:
                continue
            if filtered[right_index].priority > filtered[left_index].priority:
                removed.add(left_index)
                loser = filtered[left_index]
            else:
                removed.add(right_index)
                loser = filtered[right_index]
            omitted.extend(OmittedFact(path=fact, reason="contradicted a higher-salience fact") for fact in loser.facts)
    return [phrase for index, phrase in enumerate(filtered) if index not in removed]


def _render(
    phrases: list[Phrase],
    limit: int,
    *,
    max_phrases: int | None = None,
) -> tuple[str, list[Phrase]]:
    limit = max(limit, len(ORIGINALITY_CLAUSE) + 4)
    selected_indices: list[int] = []
    ranked_indices = sorted(range(len(phrases)), key=lambda index: (-phrases[index].priority, index))
    for index in ranked_indices:
        if max_phrases is not None and len(selected_indices) >= max_phrases:
            break
        trial_indices = sorted(selected_indices + [index])
        body = "; ".join(phrases[item].text for item in trial_indices)
        candidate = f"{body}. {ORIGINALITY_CLAUSE}" if body else ORIGINALITY_CLAUSE
        if len(candidate) <= limit:
            selected_indices = trial_indices
    selected = [phrases[index] for index in selected_indices]
    body = "; ".join(item.text for item in selected)
    prompt = f"{body}. {ORIGINALITY_CLAUSE}" if body else ORIGINALITY_CLAUSE
    return prompt, selected


def _arrangement_blueprint(
    analysis: AnalysisResult,
    disabled_paths: set[str],
) -> list[str]:
    blueprint: list[str] = []
    for index, section in enumerate(analysis.structure.sections):
        base = f"structure.sections.{index}"
        if (
            base in disabled_paths
            or f"{base}.startSeconds" in disabled_paths
            or f"{base}.endSeconds" in disabled_paths
        ):
            continue
        inferred_disabled = f"{base}.inferredLabel" in disabled_paths
        neutral_disabled = f"{base}.neutralLabel" in disabled_paths
        # Disabling the inferred label removes all semantic-label influence for
        # that section. Keep its useful timing in the blueprint, but do not
        # silently substitute the neutral label for the value the user excluded.
        label = (
            "section"
            if inferred_disabled
            else section.inferred_label
            or (None if neutral_disabled else section.neutral_label)
            or "section"
        )
        energy = (
            f", energy {section.energy:.2f}"
            if section.energy is not None and f"{base}.energy" not in disabled_paths
            else ""
        )
        repetition = (
            f", repeats {section.repetition_group}"
            if section.repetition_group and f"{base}.repetitionGroup" not in disabled_paths
            else ""
        )
        blueprint.append(
            f"{label}: {section.start_seconds:.1f}-{section.end_seconds:.1f}s{energy}{repetition}"
        )
    return blueprint


def _feature_fact(value: FeatureValue[Any], path: str) -> PromptFact:
    role = "user-entered" if value.user_edited else "user-accepted" if value.user_accepted else "observed"
    return PromptFact(path=path, value=value.value, role=role)


def _genre_layer_fact(analysis: AnalysisResult, path: str) -> PromptFact | None:
    genre = analysis.genre_analysis
    if genre is None:
        return None
    mapping = {
        "genreAnalysis.primaryProductionGenre": genre.primary_production_genre,
        "genreAnalysis.secondaryProductionGenres": genre.secondary_production_genres,
        "genreAnalysis.vocalDeliveryStyle": genre.vocal_delivery_style,
        "genreAnalysis.vocalGenreInfluences": genre.vocal_genre_influences,
        "genreAnalysis.overallGenreBlend": genre.overall_genre_blend,
        "genreAnalysis.sectionGenreEvidence": genre.section_genre_evidence,
    }
    layer = mapping.get(path)
    if layer is None:
        return None
    if isinstance(layer, list):
        value: Any = [item.value for item in layer]
        ambiguous = any(item.ambiguity for item in layer)
        accepted = any(item.accepted for item in layer)
    else:
        value = layer.value
        ambiguous = bool(layer.ambiguity) or layer.confidence in {Confidence.LOW, Confidence.UNKNOWN}
        accepted = layer.accepted
    if accepted:
        role = "user-accepted"
    elif path in {"genreAnalysis.vocalDeliveryStyle", "genreAnalysis.vocalGenreInfluences"}:
        role = "detected-component-influence"
    else:
        role = "detected-ambiguous" if ambiguous else "detected"
    return PromptFact(path=path, value=value, role=role)


def resolve_prompt_facts(
    analysis: AnalysisResult,
    preferences: PromptPreferences,
    paths: list[str],
) -> list[PromptFact]:
    """Resolve only approved prompt paths to bounded, non-private structured values."""

    feature_mapping: dict[str, FeatureValue[Any]] = {
        "rhythm.bpm": analysis.rhythm.bpm,
        "rhythm.meter": analysis.rhythm.meter,
        "rhythm.grooveDescriptors": analysis.rhythm.groove_descriptors,
        "styleAndMood.mood": analysis.style_and_mood.mood,
        "styleAndMood.energy": analysis.style_and_mood.energy,
        "instrumentation.candidates": analysis.instrumentation.candidates,
        "vocals.presence": analysis.vocals.presence,
        "vocals.density": analysis.vocals.density,
        "vocals.delivery": analysis.vocals.delivery,
        "production.productionCharacter": analysis.production.production_character,
        "production.lowEndWeight": analysis.production.low_end_weight,
        "production.spaciousnessProxy": analysis.production.spaciousness_proxy,
        "timbre.descriptors": analysis.timbre.descriptors,
        "harmony.character": analysis.harmony.character,
        "structure.energyArc": analysis.structure.energy_arc,
        "structure.repetitionSummary": analysis.structure.repetition_summary,
    }
    preference_values: dict[str, Any] = {
        "preferences.targetGenre": preferences.target_genre,
        "preferences.targetMood": preferences.target_mood,
        "preferences.targetDuration": preferences.target_duration,
        "preferences.creativity": preferences.creativity,
        "preferences.generationIntent": str(preferences.generation_intent),
        "preferences.instrumental": preferences.instrumental,
        "preferences.userWrittenLyricalDirection": preferences.user_written_lyrical_direction,
    }
    facts: list[PromptFact] = []
    for path in dict.fromkeys(paths):
        if path.startswith("genreAnalysis.accepted.") and analysis.genre_analysis is not None:
            candidate_id = path.rsplit(".", 1)[-1]
            candidate = next(
                (
                    item
                    for item in analysis.genre_analysis.broad_candidates
                    + analysis.genre_analysis.subgenre_candidates
                    + analysis.genre_analysis.descriptive_tags
                    if item.id == candidate_id and item.accepted and not item.rejected
                ),
                None,
            )
            if candidate is not None:
                facts.append(
                    PromptFact(
                        path=path,
                        value=candidate.label,
                        role="user-entered" if candidate.custom else "user-accepted",
                    )
                )
            continue
        layer_fact = _genre_layer_fact(analysis, path)
        if layer_fact is not None:
            facts.append(layer_fact)
            continue
        feature_value = feature_mapping.get(path)
        if feature_value is not None and feature_value.value is not None:
            facts.append(_feature_fact(feature_value, path))
            continue
        if path == "structure.sections":
            facts.append(
                PromptFact(
                    path=path,
                    value=[
                        {
                            "id": section.id,
                            "startSeconds": section.start_seconds,
                            "endSeconds": section.end_seconds,
                            "label": section.inferred_label or section.neutral_label,
                        }
                        for section in analysis.structure.sections[:10]
                    ],
                    role="observed",
                )
            )
            continue
        if path == "lyricsSummary.abstractThemes" and analysis.lyrics_summary is not None:
            facts.append(
                PromptFact(
                    path=path,
                    value=analysis.lyrics_summary.abstract_themes,
                    role="user-accepted" if analysis.lyrics_summary.themes_user_approved else "observed",
                )
            )
            continue
        if path in preference_values and preference_values[path] is not None:
            facts.append(PromptFact(path=path, value=preference_values[path], role="preference"))
    return facts


def compose_prompt(analysis: AnalysisResult, preferences: PromptPreferences) -> PromptPackage:
    if (
        str(preferences.generation_intent) == "instrumental_reinterpretation"
        and not preferences.instrumental
    ):
        preferences = preferences.model_copy(update={"instrumental": True})
    omitted: list[OmittedFact] = []
    warnings: list[str] = []
    if preferences.output_language.casefold() != "english":
        warnings.append("The deterministic base composer currently emits English; no external translator was used.")
    phrases = _build_phrases(analysis, preferences, omitted, warnings)
    compact, compact_used = _render(phrases, 450, max_phrases=5)
    balanced, balanced_used = _render(phrases, 1000, max_phrases=9)
    detailed, detailed_used = _render(phrases, 1800)
    if preferences.prompt_length == PromptLength.COMPACT:
        primary, primary_used = compact, compact_used
    elif preferences.prompt_length == PromptLength.DETAILED:
        primary, primary_used = detailed, detailed_used
    elif preferences.prompt_length == PromptLength.CUSTOM:
        primary, primary_used = _render(phrases, preferences.custom_max_characters or 1000)
    else:
        primary, primary_used = balanced, balanced_used
    rationale = [PromptRationale(phrase=phrase.text, fact_paths=list(phrase.facts)) for phrase in primary_used]
    fact_paths_used = list(dict.fromkeys(fact for phrase in primary_used for fact in phrase.facts))
    facts_used = resolve_prompt_facts(analysis, preferences, fact_paths_used)
    selected_facts = set(fact_paths_used)
    for phrase in detailed_used:
        for fact in phrase.facts:
            if fact not in selected_facts and not any(item.path == fact for item in omitted):
                omitted.append(OmittedFact(path=fact, reason="omitted by selected prompt length budget"))
    unsafe_values = _safe_analysis_values(analysis)
    exclusions: list[str] = []
    for exclusion in preferences.exclusions:
        cleaned = _clean_user_text(exclusion, maximum=200)
        if cleaned is None or _contains_source_identity(cleaned, unsafe_values):
            warnings.append("An unsafe, named-style, or source-identifying exclusion was omitted.")
            continue
        if cleaned.casefold() not in {item.casefold() for item in exclusions}:
            exclusions.append(cleaned)
    return PromptPackage(
        primary_prompt=primary,
        compact_prompt=compact,
        detailed_prompt=detailed,
        exclusions=exclusions,
        arrangement_blueprint=_arrangement_blueprint(
            analysis,
            set(analysis.disabled_feature_paths) | set(preferences.disabled_feature_paths),
        ),
        rationale=rationale,
        facts_used=facts_used,
        facts_omitted=omitted,
        warnings=warnings,
    )
