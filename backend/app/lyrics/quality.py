from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from ..schemas import (
    Confidence,
    LyricsSegment,
    LyricsSegmentQualityDecision,
    PrivateLyricsTranscript,
    Section,
)

_TOKEN_PATTERN = re.compile(r"[\w']+", re.UNICODE)
_NON_LEXICAL_TOKENS = frozenset(
    {
        "ah",
        "aah",
        "aaah",
        "da",
        "dum",
        "hm",
        "hmm",
        "la",
        "mhm",
        "mm",
        "na",
        "oh",
        "ooh",
        "oooh",
        "uh",
        "uhh",
    }
)
_KNOWN_HALLUCINATION_PHRASES = (
    "amara org",
    "like and subscribe",
    "please subscribe",
    "subtitles by",
    "thanks for watching",
    "thank you for watching",
)
_USABLE_DECISIONS = frozenset(
    {
        LyricsSegmentQualityDecision.ACCEPTED,
        LyricsSegmentQualityDecision.UNCERTAIN,
    }
)
_UNSAFE_APPROVED_THEME = re.compile(
    r"\b(?:ignore (?:all |the )?(?:previous|prior)|system prompt|developer message|"
    r"follow these instructions|click here|subscribe|download|tell the model)\b",
    re.IGNORECASE,
)
_PRIVATE_PATH_OR_URL = re.compile(
    r"(?:https?://|www\.|\b\w+\.com\b|@[\w.-]+|[A-Za-z]:\\|/(?:data|app)/)",
    re.IGNORECASE,
)
_IMITATION_REQUEST = re.compile(
    r"\b(?:in\s+the\s+style\s+of|sounds?\s+like|imitat(?:e|ing)|copy(?:ing)?|clone(?:d|ing)?)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SegmentQualityResult:
    decision: LyricsSegmentQualityDecision
    confidence: Confidence
    flags: tuple[str, ...]
    repeated_token_ratio: float


def normalize_lyrics_text(text: str) -> str:
    return " ".join(_TOKEN_PATTERN.findall(text.casefold()))


def lyrics_tokens(text: str) -> list[str]:
    return _TOKEN_PATTERN.findall(text.casefold())


def sanitize_user_approved_theme(text: str, maximum: int = 120) -> str | None:
    """Bound open-vocabulary user themes without treating them as instructions.

    Unlike musical fact edits, explicitly approved abstract themes must support
    ordinary concepts such as courage or renewal. They are still rejected when
    they contain control characters, prompt-injection language, paths, URLs, or
    imitation requests.
    """

    if any(not character.isprintable() for character in text):
        return None
    cleaned = re.sub(r"\s+", " ", text).strip(" ,.;:-")
    if not cleaned or len(cleaned) > maximum:
        return None
    if (
        _UNSAFE_APPROVED_THEME.search(cleaned)
        or _PRIVATE_PATH_OR_URL.search(cleaned)
        or _IMITATION_REQUEST.search(cleaned)
    ):
        return None
    if len(lyrics_tokens(cleaned)) > 12:
        return None
    return cleaned


def contains_private_lyrics_fragment(theme: str, transcript: PrivateLyricsTranscript) -> bool:
    """Return whether a proposed abstract theme copies private transcript text."""

    normalized_theme = normalize_lyrics_text(theme)
    if not normalized_theme:
        return False
    ordered_segments = sorted(
        transcript.segments,
        key=lambda segment: (segment.start_seconds, segment.end_seconds, segment.id),
    )
    normalized_segments = [
        normalized
        for segment in ordered_segments
        if (normalized := normalize_lyrics_text(segment.text))
    ]
    for normalized_segment in normalized_segments:
        if len(normalized_segment) >= 8 and normalized_segment in normalized_theme:
            return True
    # Whisper can split an otherwise contiguous phrase at an arbitrary segment
    # boundary. Scan the timestamp-ordered private transcript as one token
    # stream so a four-word fragment cannot evade the privacy boundary merely
    # because two words landed in each adjacent segment.
    transcript_words = " ".join(normalized_segments).split()
    if len(transcript_words) >= 4 and any(
        " ".join(transcript_words[index : index + 4]) in normalized_theme
        for index in range(len(transcript_words) - 3)
    ):
        return True
    return False


def repeated_token_ratio(text: str) -> float:
    tokens = lyrics_tokens(text)
    if len(tokens) < 2:
        return 0.0
    return round((len(tokens) - len(set(tokens))) / len(tokens), 4)


def _looks_non_lexical(tokens: Sequence[str]) -> bool:
    return not tokens or all(token in _NON_LEXICAL_TOKENS for token in tokens)


def _metric_confidence(avg_log_probability: float | None, no_speech_probability: float | None) -> Confidence:
    if no_speech_probability is not None and no_speech_probability >= 0.5:
        return Confidence.LOW
    if avg_log_probability is None:
        return Confidence.UNKNOWN
    if avg_log_probability >= -0.45:
        return Confidence.HIGH
    if avg_log_probability >= -0.9:
        return Confidence.MEDIUM
    return Confidence.LOW


def assess_segment_quality(
    *,
    text: str,
    start_seconds: float,
    end_seconds: float,
    avg_log_probability: float | None,
    no_speech_probability: float | None,
    compression_ratio: float | None,
    language_probability: float | None,
    adjacent_repetition_count: int = 1,
    total_occurrences: int = 1,
) -> SegmentQualityResult:
    """Classify one private segment using only decoder fields and text-derived safeguards."""

    tokens = lyrics_tokens(text)
    repetition = repeated_token_ratio(text)
    flags: list[str] = []

    if _looks_non_lexical(tokens):
        return SegmentQualityResult(
            decision=LyricsSegmentQualityDecision.NON_LEXICAL,
            confidence=Confidence.LOW,
            flags=("non_lexical_vocalization",),
            repeated_token_ratio=repetition,
        )

    duration = end_seconds - start_seconds
    words_per_second = len(tokens) / duration if duration > 0 else math.inf
    punctuation_count = sum(not character.isalnum() and not character.isspace() for character in text)
    punctuation_ratio = punctuation_count / max(len(text), 1)
    normalized = normalize_lyrics_text(text)
    known_hallucination_pattern = any(
        phrase in normalized for phrase in _KNOWN_HALLUCINATION_PHRASES
    )

    if not math.isfinite(start_seconds) or not math.isfinite(end_seconds) or duration <= 0:
        flags.append("invalid_timestamp")
    if no_speech_probability is not None and no_speech_probability >= 0.65:
        flags.append("high_no_speech_probability")
    if avg_log_probability is not None and avg_log_probability < -1.2:
        flags.append("very_low_average_log_probability")
    if compression_ratio is not None and compression_ratio >= 2.4 and repetition >= 0.4:
        flags.append("high_compression_repetition")
    if len(tokens) >= 4 and repetition >= 0.75:
        flags.append("excessive_token_repetition")
    if adjacent_repetition_count >= 3 or total_occurrences >= 3:
        flags.append("repeated_phrase_across_segments")
    if words_per_second > 8.0:
        flags.append("implausible_word_rate")
    if (
        known_hallucination_pattern
        and (
            avg_log_probability is None
            or avg_log_probability < -0.45
            or (no_speech_probability is not None and no_speech_probability >= 0.25)
        )
    ):
        flags.append("known_hallucination_pattern")
    if len(tokens) == 1 and duration >= 3.0 and (
        avg_log_probability is None or avg_log_probability < -0.45
    ):
        flags.append("isolated_single_word")

    if flags:
        return SegmentQualityResult(
            decision=LyricsSegmentQualityDecision.REJECTED_AS_LIKELY_HALLUCINATION,
            confidence=Confidence.LOW,
            flags=tuple(dict.fromkeys(flags)),
            repeated_token_ratio=repetition,
        )

    if avg_log_probability is None:
        flags.append("average_log_probability_unavailable")
    elif avg_log_probability < -0.75:
        flags.append("low_average_log_probability")
    if no_speech_probability is not None and no_speech_probability >= 0.4:
        flags.append("elevated_no_speech_probability")
    if compression_ratio is not None and compression_ratio >= 2.0:
        flags.append("elevated_compression_ratio")
    if len(tokens) >= 4 and repetition >= 0.5:
        flags.append("repeated_tokens")
    if adjacent_repetition_count == 2 or total_occurrences == 2:
        flags.append("adjacent_phrase_repetition")
    if language_probability is None or language_probability < 0.5:
        flags.append("unstable_or_unknown_language")
    if words_per_second > 5.0:
        flags.append("high_word_rate")
    if punctuation_ratio > 0.35:
        flags.append("excessive_punctuation")
    if len(tokens) == 1:
        flags.append("isolated_single_word")
    if known_hallucination_pattern:
        flags.append("known_hallucination_pattern")

    confidence = _metric_confidence(avg_log_probability, no_speech_probability)
    if flags:
        return SegmentQualityResult(
            decision=LyricsSegmentQualityDecision.UNCERTAIN,
            confidence=Confidence.LOW if confidence == Confidence.LOW else Confidence.MEDIUM,
            flags=tuple(dict.fromkeys(flags)),
            repeated_token_ratio=repetition,
        )
    return SegmentQualityResult(
        decision=LyricsSegmentQualityDecision.ACCEPTED,
        confidence=confidence,
        flags=(),
        repeated_token_ratio=repetition,
    )


def usable_transcript_segments(transcript: PrivateLyricsTranscript) -> list[LyricsSegment]:
    return [
        segment
        for segment in transcript.segments
        if segment.quality_decision in _USABLE_DECISIONS
        and bool(normalize_lyrics_text(segment.text))
    ]


def theme_eligible_segments(transcript: PrivateLyricsTranscript) -> list[LyricsSegment]:
    return [
        segment
        for segment in transcript.segments
        if segment.quality_decision == LyricsSegmentQualityDecision.ACCEPTED
        and bool(normalize_lyrics_text(segment.text))
    ]


def _validate_sections(sections: Sequence[Section], track_duration: float) -> None:
    if not math.isfinite(track_duration) or track_duration <= 0:
        raise ValueError("track duration must be finite and positive")
    previous_end = 0.0
    seen_ids: set[str] = set()
    for section in sections:
        if section.id in seen_ids:
            raise ValueError("structural section IDs must be unique")
        seen_ids.add(section.id)
        if not math.isfinite(section.start_seconds) or not math.isfinite(section.end_seconds):
            raise ValueError("structural section bounds must be finite")
        if section.start_seconds < 0 or section.end_seconds <= section.start_seconds:
            raise ValueError("structural section bounds must be ordered")
        if section.start_seconds < previous_end - 0.001:
            raise ValueError("structural sections must not overlap")
        if section.end_seconds > track_duration + 0.001:
            raise ValueError("structural sections must stay within the track")
        previous_end = section.end_seconds


def section_ids_for_interval(
    start_seconds: float,
    end_seconds: float,
    sections: Sequence[Section],
    track_duration: float,
) -> list[str]:
    """Return the dominant section, plus one material boundary-crossing section."""

    _validate_sections(sections, track_duration)
    if (
        not math.isfinite(start_seconds)
        or not math.isfinite(end_seconds)
        or end_seconds <= start_seconds
        or end_seconds <= 0
        or start_seconds >= track_duration
    ):
        return []
    bounded_start = max(0.0, start_seconds)
    bounded_end = min(track_duration, end_seconds)
    duration = bounded_end - bounded_start
    if duration <= 0:
        return []

    overlaps: list[tuple[int, str, float]] = []
    for index, section in enumerate(sections):
        overlap = max(
            0.0,
            min(bounded_end, section.end_seconds) - max(bounded_start, section.start_seconds),
        )
        if overlap > 0:
            overlaps.append((index, section.id, overlap))
    if not overlaps:
        return []

    ranked = sorted(overlaps, key=lambda item: (-item[2], item[0]))
    chosen = [ranked[0]]
    material_overlap = max(0.05, duration * 0.08)
    if len(ranked) > 1 and ranked[1][2] >= material_overlap:
        chosen.append(ranked[1])
    return [item[1] for item in sorted(chosen, key=lambda item: item[0])]


def map_transcript_to_sections(
    transcript: PrivateLyricsTranscript,
    sections: Sequence[Section],
    track_duration: float,
) -> PrivateLyricsTranscript:
    _validate_sections(sections, track_duration)
    mapped: list[LyricsSegment] = []
    for segment in transcript.segments:
        section_ids = (
            section_ids_for_interval(
                segment.start_seconds,
                segment.end_seconds,
                sections,
                track_duration,
            )
            if segment.quality_decision in _USABLE_DECISIONS
            else []
        )
        mapped.append(segment.model_copy(update={"active_section_ids": section_ids}))
    return transcript.model_copy(update={"segments": mapped})


def active_transcript_section_ids(transcript: PrivateLyricsTranscript) -> list[str]:
    active: list[str] = []
    for segment in usable_transcript_segments(transcript):
        for section_id in segment.active_section_ids:
            if section_id not in active:
                active.append(section_id)
    return active


def quality_decision_counts(transcript: PrivateLyricsTranscript) -> dict[str, int]:
    counts = Counter(segment.quality_decision.value for segment in transcript.segments)
    return {decision.value: counts.get(decision.value, 0) for decision in LyricsSegmentQualityDecision}
