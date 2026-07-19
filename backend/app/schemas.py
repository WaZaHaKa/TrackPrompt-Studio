from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

T = TypeVar("T")

VISUAL_CUE_SHEET_SCHEMA_VERSION = "1.1.0"
VISUAL_FEATURE_ARTIFACT_SCHEMA_VERSION = "1.0.0"
BLENDER_VISUALIZER_PRESET = "abstract-geometry"


class APIModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class LyricsSegmentQualityDecision(StrEnum):
    ACCEPTED = "accepted"
    UNCERTAIN = "uncertain"
    REJECTED_AS_LIKELY_HALLUCINATION = "rejected_as_likely_hallucination"
    NON_LEXICAL = "non_lexical"


class EvidenceKind(StrEnum):
    DIRECT_MEASUREMENT = "direct_measurement"
    STRONG_ESTIMATE = "strong_estimate"
    HEURISTIC = "heuristic"
    PROXY = "proxy"
    UNAVAILABLE = "unavailable"
    AMBIGUOUS = "ambiguous"


class FeatureValue(APIModel, Generic[T]):
    value: T | None = None
    confidence: Confidence = Confidence.UNKNOWN
    score: float | None = None
    method: str
    alternatives: list[Any] = Field(default_factory=list)
    warning: str | None = None
    evidence_kind: EvidenceKind = EvidenceKind.HEURISTIC
    user_edited: bool = False
    user_accepted: bool = False

    @field_validator("score")
    @classmethod
    def finite_score(cls, value: float | None) -> float | None:
        if value is not None and not (-1_000_000.0 < value < 1_000_000.0):
            raise ValueError("score must be finite and bounded")
        return value


class ErrorDetail(APIModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class ErrorResponse(APIModel):
    error: ErrorDetail


class FFmpegCapability(APIModel):
    available: bool
    version: str | None = None


class DeepAdapterCapability(APIModel):
    id: str
    name: str
    available: bool
    reason: str | None = None
    disk_impact_mb: int | None = None
    license: str | None = None
    enabled: bool = False
    torch_installed: bool = False
    torch_version: str | None = None
    cuda_build_support: bool = False
    cuda_runtime_available: bool = False
    gpu_device_name: str | None = None
    selected_device: str = "cpu"
    fallback_reason: str | None = None


class OptionalAnalyzerCapability(APIModel):
    id: str
    name: str
    features: list[str] = Field(default_factory=list)
    available: bool = False
    reason: str
    license: str | None = None


class ModelAdapterCapability(APIModel):
    id: str
    name: str
    installed: bool = False
    model_ready: bool = False
    available: bool = False
    enabled: bool = False
    reason: str
    model_id: str | None = None
    model_revision: str | None = None
    selected_device: str = "cpu"
    effective_device: str = "unavailable"
    gpu_device_name: str | None = None
    disk_impact_mb: int | None = None
    taxonomy_version: str | None = None
    languages_supported: str | None = None
    privacy_behavior: str | None = None
    fallback_reason: str | None = None
    features: list[str] = Field(default_factory=list)
    license: str | None = None


class PromptWriterCapability(ModelAdapterCapability):
    service_reachable: bool = False
    reliable_available: bool = True
    creative_available: bool = False
    experimental_available: bool = False
    supports_seed: bool = False
    supports_sampling: bool = False
    fallback_behavior: str = "Reliable deterministic composition"


class GPUTaskQueueCapability(APIModel):
    workers: int
    active: int = 0
    waiting: int = 0
    policy: str = "single-heavy-task"


class ModeCapability(APIModel):
    available: bool
    features: list[str] = Field(default_factory=list)
    will_fallback: bool = False
    adapters: list[DeepAdapterCapability] = Field(default_factory=list)


class LimitsCapability(APIModel):
    max_upload_mb: int
    max_duration_seconds: int
    job_ttl_minutes: int
    max_pending_jobs: int


class CapabilitiesResponse(APIModel):
    fast_mode: ModeCapability
    deep_mode: ModeCapability
    ffmpeg: FFmpegCapability
    ffprobe: FFmpegCapability
    limits: LimitsCapability
    optional_analyzers: list[OptionalAnalyzerCapability] = Field(default_factory=list)
    genre_tagger: ModelAdapterCapability | None = None
    lyrics_adapter: ModelAdapterCapability | None = None
    prompt_writer: PromptWriterCapability | None = None
    gpu_task_queue: GPUTaskQueueCapability | None = None
    visual_cue_export_available: bool = True
    visual_cue_sheet_schema_version: str = VISUAL_CUE_SHEET_SCHEMA_VERSION
    visual_feature_artifact_schema_version: str = VISUAL_FEATURE_ARTIFACT_SCHEMA_VERSION
    blender_visualizer_preset: str = BLENDER_VISUALIZER_PRESET
    network_features_enabled: bool = False


class HealthResponse(APIModel):
    status: str
    service_version: str
    schema_version: str
    ffmpeg: FFmpegCapability
    ffprobe: FFmpegCapability
    database_available: bool
    analysis_workers: int
    deep_mode_available: bool
    genre_tagger_available: bool = False
    lyrics_adapter_available: bool = False
    local_prompt_writer_available: bool = False
    visual_cue_export_available: bool = True
    visual_cue_sheet_schema_version: str = VISUAL_CUE_SHEET_SCHEMA_VERSION
    visual_feature_artifact_schema_version: str = VISUAL_FEATURE_ARTIFACT_SCHEMA_VERSION
    blender_visualizer_preset: str = BLENDER_VISUALIZER_PRESET
    network_features_enabled: bool = False


class FileInfo(APIModel):
    display_name: str
    duration_seconds: float
    sample_rate: int
    channels: int
    codec: str
    container: str
    bit_rate: int | None = None
    size_bytes: int
    private_metadata: dict[str, str] = Field(default_factory=dict)


class SignalQuality(APIModel):
    leading_silence_seconds: FeatureValue[float]
    trailing_silence_seconds: FeatureValue[float]
    clipping: FeatureValue[bool]
    dc_offset: FeatureValue[float]
    noise_floor_dbfs: FeatureValue[float]
    effective_level_dbfs: FeatureValue[float]
    phase_correlation: FeatureValue[float]
    sufficient_signal: FeatureValue[bool]
    activity_threshold_dbfs: FeatureValue[float] | None = None
    decoded_sample_range: FeatureValue[list[float]] | None = None


class RhythmAnalysis(APIModel):
    bpm: FeatureValue[float]
    tempo_stability: FeatureValue[str]
    beat_timestamps: FeatureValue[list[float]]
    downbeat_likelihood: FeatureValue[str]
    meter: FeatureValue[str]
    onset_density: FeatureValue[float]
    rhythmic_regularity: FeatureValue[str]
    swing_tendency: FeatureValue[str]
    syncopation_tendency: FeatureValue[str]
    percussiveness: FeatureValue[str]
    groove_descriptors: FeatureValue[list[str]]
    onset_timestamps: FeatureValue[list[float]] | None = None
    beat_grid_alignment: FeatureValue[float] | None = None


class ChordSegment(APIModel):
    chord: str | None
    start_seconds: float
    end_seconds: float
    confidence: Confidence


class HarmonyAnalysis(APIModel):
    key: FeatureValue[str]
    mode: FeatureValue[str]
    tonal_confidence: FeatureValue[str]
    chords: FeatureValue[list[ChordSegment]]
    chord_vocabulary: FeatureValue[list[str]]
    harmonic_rhythm: FeatureValue[str]
    major_minor_balance: FeatureValue[str]
    stability: FeatureValue[str]
    character: FeatureValue[list[str]]


class MelodyAnalysis(APIModel):
    pitch_contour_available: FeatureValue[bool]
    melodic_range: FeatureValue[str]
    register_value: FeatureValue[str] = Field(alias="register", serialization_alias="register")
    phrase_length: FeatureValue[str]
    movement: FeatureValue[str]
    repetition: FeatureValue[str]
    density: FeatureValue[str]
    ornamentation: FeatureValue[str]
    call_and_response: FeatureValue[str]
    hook_prominence: FeatureValue[str]


class SectionStemEvidence(APIModel):
    relative_rms: dict[str, float] = Field(default_factory=dict)
    activity: dict[str, str] = Field(default_factory=dict)
    method: str
    confidence: Confidence


class Section(APIModel):
    id: str
    neutral_label: str
    inferred_label: str | None = None
    start_seconds: float
    end_seconds: float
    confidence: Confidence
    repetition_group: str | None = None
    energy: float | None = None
    loudness: float | None = None
    density: float | None = None
    instruments: list[str] = Field(default_factory=list)
    vocal_activity: str | None = None
    harmony_summary: str | None = None
    transition_in: str | None = None
    transition_out: str | None = None
    boundary_confidence: Confidence | None = None
    deep_evidence: SectionStemEvidence | None = None


class StructureAnalysis(APIModel):
    sections: list[Section]
    energy_arc: FeatureValue[str]
    important_transitions: FeatureValue[list[float]]
    repetition_summary: FeatureValue[str]


class TimbreAnalysis(APIModel):
    spectral_centroid_hz: FeatureValue[float]
    spectral_bandwidth_hz: FeatureValue[float]
    spectral_rolloff_hz: FeatureValue[float]
    spectral_flatness: FeatureValue[float]
    mfcc_summary: FeatureValue[list[float]]
    zero_crossing_rate: FeatureValue[float]
    harmonic_percussive_balance: FeatureValue[str]
    transient_sharpness: FeatureValue[str]
    descriptors: FeatureValue[list[str]]
    texture: FeatureValue[list[str]]


class InstrumentCandidate(APIModel):
    name: str
    prominence: str
    confidence: Confidence
    sections: list[str] = Field(default_factory=list)


class InstrumentationAnalysis(APIModel):
    candidates: FeatureValue[list[InstrumentCandidate]]
    coarse_categories_only: bool = True


class VocalsAnalysis(APIModel):
    presence: FeatureValue[str]
    register_value: FeatureValue[str] = Field(alias="register", serialization_alias="register")
    delivery: FeatureValue[list[str]]
    phrasing: FeatureValue[list[str]]
    density: FeatureValue[str]
    layering: FeatureValue[str]
    processing: FeatureValue[list[str]]
    mix_placement: FeatureValue[str]

    @field_validator("phrasing", mode="before")
    @classmethod
    def migrate_legacy_phrasing(cls, value: Any) -> Any:
        if isinstance(value, dict) and isinstance(value.get("value"), str):
            return {**value, "value": [value["value"]]}
        return value


class ProductionAnalysis(APIModel):
    integrated_loudness_lufs: FeatureValue[float]
    loudness_range_lu: FeatureValue[float]
    peak_dbfs: FeatureValue[float]
    true_peak_dbfs: FeatureValue[float]
    crest_factor_db: FeatureValue[float]
    macro_dynamic_range_db: FeatureValue[float]
    compression_tendency: FeatureValue[str]
    stereo_width: FeatureValue[float]
    mono_compatibility: FeatureValue[str]
    frequency_balance: FeatureValue[list[float]]
    low_end_weight: FeatureValue[str]
    midrange_focus: FeatureValue[str]
    high_frequency_brightness: FeatureValue[str]
    spaciousness_proxy: FeatureValue[str]
    sidechain_pumping_proxy: FeatureValue[str]
    transient_emphasis: FeatureValue[str]
    mix_density: FeatureValue[str]
    production_character: FeatureValue[list[str]]


class StyleAndMoodAnalysis(APIModel):
    broad_style: FeatureValue[list[str]]
    genre_blend: FeatureValue[list[str]]
    production_era_resemblance: FeatureValue[str]
    mood: FeatureValue[list[str]]
    energy: FeatureValue[str]
    valence: FeatureValue[str]
    intensity: FeatureValue[str]
    danceability_tendency: FeatureValue[str]
    cinematic_quality: FeatureValue[str]
    organic_synthetic: FeatureValue[str]
    commercial_experimental: FeatureValue[str]


class GenreCandidate(APIModel):
    id: str
    label: str = Field(min_length=1, max_length=120)
    canonical_label: str = Field(min_length=1, max_length=120)
    parent: str | None = Field(default=None, max_length=120)
    similarity: float = Field(ge=-1.0, le=1.0)
    confidence: Confidence = Confidence.UNKNOWN
    accepted: bool = False
    rejected: bool = False
    locked: bool = False
    user_edited: bool = False
    custom: bool = False


class GenreWindowEvidence(APIModel):
    id: str
    kind: str
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    top_labels: list[str] = Field(default_factory=list, max_length=8)
    similarities: dict[str, float] = Field(default_factory=dict)
    weight: float = Field(default=1.0, ge=0.0, le=1.0)
    representativeness: float = Field(default=1.0, ge=0.0, le=1.0)
    vocal_dominant: bool = False
    percussion_dominant: bool = False
    section_ids: list[str] = Field(default_factory=list)
    analysis_view: str = Field(default="full_mix", max_length=40)

    @model_validator(mode="after")
    def ordered_window(self) -> GenreWindowEvidence:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("genre window end must be after start")
        if any(not -1.0 <= value <= 1.0 for value in self.similarities.values()):
            raise ValueError("genre window similarities must be bounded")
        return self


class GenreLayerEvidence(APIModel):
    value: str | list[str]
    confidence: Confidence = Confidence.UNKNOWN
    method: str = Field(min_length=1, max_length=500)
    supporting_window_ids: list[str] = Field(default_factory=list, max_length=24)
    supporting_section_ids: list[str] = Field(default_factory=list, max_length=24)
    alternatives: list[str] = Field(default_factory=list, max_length=12)
    ambiguity: str | None = Field(default=None, max_length=300)
    source: str = Field(default="detected", pattern=r"^(detected|user_entered)$")
    accepted: bool = False
    enabled_for_prompt: bool = True


class GenreAnalysis(APIModel):
    broad_candidates: list[GenreCandidate] = Field(default_factory=list, max_length=8)
    subgenre_candidates: list[GenreCandidate] = Field(default_factory=list, max_length=12)
    blend_candidates: list[str] = Field(default_factory=list, max_length=4)
    descriptive_tags: list[GenreCandidate] = Field(default_factory=list, max_length=12)
    window_evidence: list[GenreWindowEvidence] = Field(default_factory=list, max_length=24)
    section_evidence: dict[str, list[str]] = Field(default_factory=dict)
    primary_production_genre: GenreLayerEvidence | None = None
    secondary_production_genres: GenreLayerEvidence | None = None
    vocal_delivery_style: GenreLayerEvidence | None = None
    vocal_genre_influences: GenreLayerEvidence | None = None
    section_genre_evidence: list[GenreLayerEvidence] = Field(default_factory=list, max_length=24)
    overall_genre_blend: GenreLayerEvidence | None = None
    confidence: Confidence = Confidence.UNKNOWN
    ambiguity: str | None = Field(default=None, max_length=240)
    method: str
    model_id: str
    taxonomy_version: str
    selected_device: str
    agreement_across_windows: float | None = Field(default=None, ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)
    user_edited: bool = False
    user_accepted: bool = False
    disabled_for_prompt: bool = False


class LyricsAnalysisSummary(APIModel):
    enabled: bool = False
    status: str = "not_requested"
    adapter_id: str | None = None
    model_id: str | None = None
    selected_device: str = "unavailable"
    language: str | None = None
    language_confidence: Confidence = Confidence.UNKNOWN
    transcript_available: bool = False
    segment_count: int = Field(default=0, ge=0)
    active_section_ids: list[str] = Field(default_factory=list)
    vocal_word_density: str | None = None
    non_lexical_vocalization_tendency: str | None = None
    abstract_themes: list[str] = Field(default_factory=list, max_length=8)
    theme_confidence: Confidence = Confidence.UNKNOWN
    themes_user_approved: bool = False
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime | None = None


class LyricsSegment(APIModel):
    id: str
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    text: str = Field(max_length=1000)
    confidence: Confidence = Confidence.UNKNOWN
    quality_decision: LyricsSegmentQualityDecision = LyricsSegmentQualityDecision.UNCERTAIN
    avg_log_probability: float | None = Field(default=None, ge=-100.0, le=10.0)
    no_speech_score: float | None = Field(default=None, ge=0.0, le=1.0)
    compression_ratio: float | None = Field(default=None, ge=0.0, le=100.0)
    repeated_token_ratio: float | None = Field(default=None, ge=0.0, le=1.0)
    active_section_ids: list[str] = Field(default_factory=list, max_length=4)
    quality_flags: list[str] = Field(default_factory=list, max_length=12)
    user_edited: bool = False

    @model_validator(mode="after")
    def ordered_segment(self) -> LyricsSegment:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("lyrics segment end must be after start")
        return self


class PrivateLyricsTranscript(APIModel):
    schema_version: str = "1.1.0"
    job_id: str
    language: str | None = None
    segments: list[LyricsSegment] = Field(default_factory=list, max_length=5000)
    model_id: str
    selected_device: str
    warnings: list[str] = Field(default_factory=list)
    user_edited: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DeepDiagnostics(APIModel):
    adapter_id: str | None = None
    method: str | None = None
    selected_device: str = "cpu"
    torch_installed: bool = False
    torch_version: str | None = None
    cuda_build_support: bool = False
    cuda_runtime_available: bool = False
    gpu_device_name: str | None = None
    fallback_reason: str | None = None
    stem_relative_rms: dict[str, float] = Field(default_factory=dict)


class AnalysisResult(APIModel):
    schema_version: str = "1.4.0"
    analysis_version: str = "0.5.0"
    job_id: str
    capabilities: list[str]
    requested_mode: str
    effective_mode: str
    file: FileInfo
    waveform_peaks: list[float]
    signal_quality: SignalQuality
    rhythm: RhythmAnalysis
    harmony: HarmonyAnalysis
    melody: MelodyAnalysis
    structure: StructureAnalysis
    timbre: TimbreAnalysis
    instrumentation: InstrumentationAnalysis
    vocals: VocalsAnalysis
    production: ProductionAnalysis
    style_and_mood: StyleAndMoodAnalysis
    genre_analysis: GenreAnalysis | None = None
    lyrics_summary: LyricsAnalysisSummary | None = None
    deep_diagnostics: DeepDiagnostics | None = None
    warnings: list[str] = Field(default_factory=list)
    analyzer_versions: dict[str, str] = Field(default_factory=dict)
    disabled_feature_paths: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class JobStatus(StrEnum):
    QUEUED = "queued"
    VALIDATING = "validating"
    DECODING = "decoding"
    ANALYZING_CORE = "analyzing_core"
    SEPARATING_STEMS = "separating_stems"
    ANALYZING_DEEP = "analyzing_deep"
    TRANSCRIBING_LYRICS = "transcribing_lyrics"
    DERIVING_LYRICAL_THEMES = "deriving_lyrical_themes"
    TAGGING_GENRE = "tagging_genre"
    GENERATING_PROMPT = "generating_prompt"
    GENERATING_CANDIDATES = "generating_candidates"
    VALIDATING_CANDIDATES = "validating_candidates"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    EXPIRED = "expired"


class AnalysisMode(StrEnum):
    FAST = "fast"
    DEEP = "deep"


class JobResponse(APIModel):
    job_id: str
    status: JobStatus
    requested_mode: AnalysisMode
    mode: AnalysisMode
    stage: str
    message: str
    progress: int = Field(default=0, ge=0, le=100)
    analysis: AnalysisResult | None = None
    prompt_package: PromptPackage | None = None
    error: ErrorDetail | None = None
    created_at: datetime
    updated_at: datetime


class JobEvent(APIModel):
    job_id: str
    status: JobStatus
    mode: AnalysisMode
    stage: str
    message: str
    sequence: int
    progress: int = Field(default=0, ge=0, le=100)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class GenerationIntent(StrEnum):
    PRESERVE = "preserve_core_character"
    INSPIRED = "inspired_variation"
    LOOSER = "more_original"
    GENRE_TRANSFER = "genre_transfer"
    INSTRUMENTAL = "instrumental_reinterpretation"
    CHANGE_MOOD = "change_mood_preserve_groove"
    CHANGE_INSTRUMENTATION = "change_instrumentation_preserve_structure"
    CUSTOM = "custom"


class PromptLength(StrEnum):
    COMPACT = "compact"
    BALANCED = "balanced"
    DETAILED = "detailed"
    CUSTOM = "custom"


class PromptEngineMode(StrEnum):
    RELIABLE = "reliable"
    CREATIVE = "creative"
    EXPERIMENTAL = "experimental"


class GenreInterpretationMode(StrEnum):
    STRICT_TOP = "strict_top"
    BLEND = "blend"
    DETECTED_LAYERED = "detected_layered"
    USER_SELECTED_ONLY = "user_selected_only"
    DISABLED = "disabled"


class LyricsInfluenceMode(StrEnum):
    NONE = "none"
    PROSODY_ONLY = "prosody_only"
    ABSTRACT_THEMES = "abstract_themes"
    USER_WRITTEN_DIRECTION = "user_written_direction"


class PromptPreferences(APIModel):
    output_language: str = "English"
    generation_intent: GenerationIntent = GenerationIntent.PRESERVE
    prompt_length: PromptLength = PromptLength.BALANCED
    custom_max_characters: int | None = Field(default=None, ge=200, le=4000)
    include_bpm: bool = True
    include_key: bool = True
    instrumental: bool = False
    desired_vocal_presentation: str | None = Field(default=None, max_length=200)
    creativity: float = Field(default=0.35, ge=0.0, le=1.0)
    preserve_energy_arc: bool = True
    preserve_instrumentation: bool = True
    preserve_structure: bool = True
    preserve_groove: bool = True
    target_genre: str | None = Field(default=None, max_length=120)
    target_mood: str | None = Field(default=None, max_length=120)
    target_duration: float | None = Field(default=None, gt=0, le=7200)
    exclusions: list[str] = Field(default_factory=list, max_length=20)
    disabled_feature_paths: list[str] = Field(default_factory=list)
    user_overrides: dict[str, Any] = Field(default_factory=dict)
    variation_seed: int | None = Field(default=None, ge=0, le=2_147_483_647)
    prompt_engine_mode: PromptEngineMode = PromptEngineMode.RELIABLE
    genre_interpretation_mode: GenreInterpretationMode = GenreInterpretationMode.STRICT_TOP
    lyrics_influence_mode: LyricsInfluenceMode = LyricsInfluenceMode.NONE
    candidate_count: int = Field(default=1)
    lock_seed: bool = False
    locked_feature_paths: list[str] = Field(default_factory=list, max_length=100)
    include_detected_genre: bool = True
    accepted_genre_ids: list[str] = Field(default_factory=list, max_length=12)
    include_lyrical_themes: bool = False
    desired_transformations: list[str] = Field(default_factory=list, max_length=12)
    user_written_lyrical_direction: str | None = Field(default=None, max_length=240)

    @field_validator("exclusions")
    @classmethod
    def safe_exclusions(cls, values: list[str]) -> list[str]:
        return [value.strip()[:200] for value in values if value.strip()]

    @field_validator("desired_transformations")
    @classmethod
    def safe_transformations(cls, values: list[str]) -> list[str]:
        return [" ".join(value.split())[:200] for value in values if value.strip()]

    @field_validator("accepted_genre_ids")
    @classmethod
    def safe_genre_ids(cls, values: list[str]) -> list[str]:
        if any(len(value) > 120 or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value) is None for value in values):
            raise ValueError("acceptedGenreIds contains an invalid identifier")
        return list(dict.fromkeys(values))

    @model_validator(mode="after")
    def derive_instrumental_intent(self) -> PromptPreferences:
        if self.generation_intent == GenerationIntent.INSTRUMENTAL:
            self.instrumental = True
        if self.generation_intent == GenerationIntent.CHANGE_INSTRUMENTATION:
            self.preserve_instrumentation = False
        if self.generation_intent == GenerationIntent.GENRE_TRANSFER and not (
            self.target_genre and self.target_genre.strip()
        ):
            raise ValueError("genre_transfer requires a nonblank targetGenre")
        if self.candidate_count not in {1, 3}:
            raise ValueError("candidateCount must be 1 or 3")
        if self.prompt_engine_mode == PromptEngineMode.RELIABLE:
            self.candidate_count = 1
        if self.instrumental:
            self.lyrics_influence_mode = LyricsInfluenceMode.NONE
            self.include_lyrical_themes = False
        if (
            self.lyrics_influence_mode == LyricsInfluenceMode.USER_WRITTEN_DIRECTION
            and not (self.user_written_lyrical_direction or "").strip()
        ):
            raise ValueError("user_written_direction requires bounded user text")
        return self


class PromptRationale(APIModel):
    phrase: str
    fact_paths: list[str]


class OmittedFact(APIModel):
    path: str
    reason: str


class PromptFact(APIModel):
    path: str = Field(min_length=1, max_length=160)
    value: Any
    role: str = Field(
        pattern=(
            r"^(observed|user-entered|user-accepted|preference|detected|"
            r"detected-ambiguous|detected-component-influence)$"
        )
    )


class PromptGenerationParameters(APIModel):
    sampling: bool = False
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    top_p: float = Field(default=1.0, gt=0.0, le=1.0)
    repetition_penalty: float = Field(default=1.0, ge=0.5, le=2.0)
    maximum_tokens: int = Field(default=512, ge=32, le=2048)
    timeout_seconds: int = Field(default=60, ge=1, le=600)


class LocalPromptCandidate(APIModel):
    id: str
    prompt: str = Field(min_length=1, max_length=4000)
    short_title: str = Field(min_length=1, max_length=80)
    engine_mode: PromptEngineMode
    seed: int | None = None
    model_id: str
    generation_parameters: PromptGenerationParameters
    facts_used: list[PromptFact] = Field(default_factory=list, max_length=100)
    creative_directions_used: list[str] = Field(default_factory=list, max_length=20)
    warnings: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("facts_used", mode="before")
    @classmethod
    def migrate_legacy_fact_paths(cls, values: Any) -> Any:
        if isinstance(values, list):
            return [
                {"path": value, "value": None, "role": "observed"}
                if isinstance(value, str)
                else value
                for value in values
            ]
        return values


class PromptEvidence(APIModel):
    accepted_genre_candidates: list[str] = Field(default_factory=list, max_length=4)
    accepted_genre_blend: list[str] = Field(default_factory=list, max_length=2)
    tempo: float | None = Field(default=None, ge=20, le=300)
    meter: str | None = Field(default=None, max_length=30)
    groove: list[str] = Field(default_factory=list, max_length=6)
    mood: list[str] = Field(default_factory=list, max_length=6)
    energy: str | None = Field(default=None, max_length=60)
    instrumentation: list[str] = Field(default_factory=list, max_length=8)
    vocal_presence: str | None = Field(default=None, max_length=60)
    vocal_density: str | None = Field(default=None, max_length=60)
    approved_vocal_descriptors: list[str] = Field(default_factory=list, max_length=6)
    vocal_genre_influences: list[str] = Field(default_factory=list, max_length=6)
    overall_genre_blend: str | None = Field(default=None, max_length=240)
    structure_summary: list[str] = Field(default_factory=list, max_length=10)
    section_energy_summary: list[str] = Field(default_factory=list, max_length=10)
    production_descriptors: list[str] = Field(default_factory=list, max_length=8)
    low_end_weight: str | None = Field(default=None, max_length=80)
    stereo_character: str | None = Field(default=None, max_length=80)
    energy_arc: str | None = Field(default=None, max_length=80)
    repetition_character: str | None = Field(default=None, max_length=120)
    harmonic_character: list[str] = Field(default_factory=list, max_length=6)
    target_genre: str | None = Field(default=None, max_length=120)
    target_mood: str | None = Field(default=None, max_length=120)
    target_duration: float | None = Field(default=None, gt=0, le=7200)
    generation_intent: GenerationIntent
    creative_freedom: float = Field(ge=0.0, le=1.0)
    locked_facts: list[str] = Field(default_factory=list, max_length=100)
    desired_transformations: list[str] = Field(default_factory=list, max_length=12)
    allowed_lyrical_themes: list[str] = Field(default_factory=list, max_length=8)
    exclusions: list[str] = Field(default_factory=list, max_length=20)
    originality_requirement: str = Field(max_length=240)
    maximum_characters: int = Field(ge=200, le=4000)
    output_language: str = Field(max_length=60)


class PromptPackage(APIModel):
    primary_prompt: str
    compact_prompt: str
    detailed_prompt: str
    exclusions: list[str]
    arrangement_blueprint: list[str]
    rationale: list[PromptRationale]
    facts_used: list[PromptFact]
    facts_omitted: list[OmittedFact]
    warnings: list[str] = Field(default_factory=list)
    engine_mode: PromptEngineMode = PromptEngineMode.RELIABLE
    candidates: list[LocalPromptCandidate] = Field(default_factory=list, max_length=3)
    selected_candidate_id: str | None = None
    model_id: str = "trackprompt-deterministic-composer"
    seed: int | None = None
    generation_parameters: PromptGenerationParameters = Field(default_factory=PromptGenerationParameters)
    validation_warnings: list[str] = Field(default_factory=list)
    deterministic_fallback_used: bool = False

    @field_validator("facts_used", mode="before")
    @classmethod
    def migrate_legacy_fact_paths(cls, values: Any) -> Any:
        if isinstance(values, list):
            return [
                {"path": value, "value": None, "role": "observed"}
                if isinstance(value, str)
                else value
                for value in values
            ]
        return values

    @model_validator(mode="after")
    def selected_candidate_is_consistent(self) -> PromptPackage:
        if not self.candidates:
            if self.selected_candidate_id is not None:
                raise ValueError("selectedCandidateId requires a prompt candidate")
            return self
        candidate_ids = [candidate.id for candidate in self.candidates]
        if len(set(candidate_ids)) != len(candidate_ids):
            raise ValueError("prompt candidate IDs must be unique")
        if self.selected_candidate_id is None:
            raise ValueError("a generated prompt package requires a selected candidate")
        selected = next(
            (
                candidate
                for candidate in self.candidates
                if candidate.id == self.selected_candidate_id
            ),
            None,
        )
        if selected is None:
            raise ValueError("selectedCandidateId must reference a prompt candidate")
        if self.primary_prompt != selected.prompt:
            raise ValueError("primaryPrompt must match the selected prompt candidate")
        if self.engine_mode == PromptEngineMode.RELIABLE:
            if any(candidate.engine_mode != PromptEngineMode.RELIABLE for candidate in self.candidates):
                raise ValueError("Reliable packages may contain only Reliable candidates")
        elif self.deterministic_fallback_used:
            if any(candidate.engine_mode != PromptEngineMode.RELIABLE for candidate in self.candidates):
                raise ValueError("A deterministic fallback may contain only Reliable candidates")
            if not any("fallback" in warning.casefold() for warning in self.validation_warnings):
                raise ValueError("A deterministic fallback must be declared in validationWarnings")
        elif any(candidate.engine_mode != self.engine_mode for candidate in self.candidates):
            raise ValueError("A local-writer package must contain candidates from its reported engine mode")
        return self


class PromptSelection(APIModel):
    candidate_id: str = Field(min_length=1, max_length=160)


class GenreCandidateUpdate(APIModel):
    candidate_id: str = Field(min_length=1, max_length=120)
    label: str | None = Field(default=None, min_length=1, max_length=120)
    accepted: bool | None = None
    rejected: bool | None = None
    locked: bool | None = None
    restore_detected: bool = False


class GenrePatch(APIModel):
    updates: list[GenreCandidateUpdate] = Field(default_factory=list, max_length=20)
    custom_genre: str | None = Field(default=None, min_length=1, max_length=120)
    disabled_for_prompt: bool | None = None
    restore_all: bool = False


class LyricsSegmentUpdate(APIModel):
    segment_id: str = Field(min_length=1, max_length=120)
    text: str | None = Field(default=None, max_length=1000)
    mark_uncertain: bool = False
    delete: bool = False
    restore_detected: bool = False


class LyricsPatch(APIModel):
    updates: list[LyricsSegmentUpdate] = Field(default_factory=list, max_length=100)
    abstract_themes: list[str] | None = Field(default=None, max_length=8)


class FeatureUpdate(APIModel):
    path: str = Field(min_length=1, max_length=160)
    value: Any | None = None
    disabled_for_prompt: bool | None = None
    accepted_for_prompt: bool | None = None
    restore_detected: bool = False


class AnalysisPatch(APIModel):
    updates: list[FeatureUpdate] = Field(default_factory=list, max_length=100)
    disabled_feature_paths: list[str] | None = None
    user_overrides: dict[str, Any] | None = None


JobResponse.model_rebuild()
