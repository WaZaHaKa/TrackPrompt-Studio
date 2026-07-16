from __future__ import annotations

import json
import os
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..adapters import demucs_ready, inspect_torch_device, run_demucs
from ..config import Settings
from ..lyrics import create_lyrics_adapter
from ..privacy import secure_private_file
from ..schemas import (
    AnalysisResult,
    Confidence,
    DeepDiagnostics,
    EvidenceKind,
    FileInfo,
    HarmonyAnalysis,
    InstrumentationAnalysis,
    InstrumentCandidate,
    LyricsAnalysisSummary,
    MelodyAnalysis,
    ProductionAnalysis,
    RhythmAnalysis,
    Section,
    SectionStemEvidence,
    StructureAnalysis,
    StyleAndMoodAnalysis,
    TimbreAnalysis,
    VocalsAnalysis,
)
from ..tagging import create_music_tagger
from .core import (
    AudioData,
    analyze_harmony,
    analyze_instrumentation,
    analyze_melody,
    analyze_production,
    analyze_rhythm,
    analyze_structure,
    analyze_style,
    analyze_timbre,
    analyze_vocals,
    feature,
    load_audio,
    signal_quality,
    spectral_features,
    waveform_peaks,
    with_failure_isolation,
)
from .sanity import validate_analysis_result

WORKER_STAGE_PROGRESS = {
    "inspecting_signal": 25,
    "analyzing_rhythm": 38,
    "analyzing_harmony": 50,
    "segmenting_structure": 62,
    "analyzing_production": 75,
    "separating_stems": 82,
    "running_enhanced_taggers": 87,
    "transcribing_lyrics": 88,
    "tagging_genre": 91,
}


class AnalysisCancelled(RuntimeError):
    pass


def _unknown(method: str, warning: str) -> Any:
    return feature(None, Confidence.UNKNOWN, method, warning=warning)


def _rhythm_fallback(warning: str) -> RhythmAnalysis:
    def value() -> Any:
        return _unknown("analyzer unavailable", warning)
    return RhythmAnalysis(
        bpm=value(), tempo_stability=value(), beat_timestamps=value(), downbeat_likelihood=value(),
        meter=value(), onset_density=value(), rhythmic_regularity=value(), swing_tendency=value(),
        syncopation_tendency=value(), percussiveness=value(), groove_descriptors=value(),
    )


def _harmony_fallback(warning: str) -> HarmonyAnalysis:
    def value() -> Any:
        return _unknown("analyzer unavailable", warning)
    return HarmonyAnalysis(
        key=value(), mode=value(), tonal_confidence=value(), chords=value(), chord_vocabulary=value(),
        harmonic_rhythm=value(), major_minor_balance=value(), stability=value(), character=value(),
    )


def _structure_fallback(audio: AudioData, warning: str) -> StructureAnalysis:
    return StructureAnalysis(
        sections=[
            Section(
                id="section-1",
                neutral_label="A",
                start_seconds=0.0,
                end_seconds=round(audio.duration, 3),
                confidence=Confidence.UNKNOWN,
            )
        ],
        energy_arc=_unknown("analyzer unavailable", warning),
        important_transitions=_unknown("analyzer unavailable", warning),
        repetition_summary=_unknown("analyzer unavailable", warning),
    )


def _timbre_fallback(warning: str) -> TimbreAnalysis:
    def value() -> Any:
        return _unknown("analyzer unavailable", warning)
    return TimbreAnalysis(
        spectral_centroid_hz=value(), spectral_bandwidth_hz=value(), spectral_rolloff_hz=value(),
        spectral_flatness=value(), mfcc_summary=value(), zero_crossing_rate=value(),
        harmonic_percussive_balance=value(), transient_sharpness=value(), descriptors=value(), texture=value(),
    )


def _production_fallback(warning: str) -> ProductionAnalysis:
    def value() -> Any:
        return _unknown("analyzer unavailable", warning)
    return ProductionAnalysis(
        integrated_loudness_lufs=value(), loudness_range_lu=value(), peak_dbfs=value(), true_peak_dbfs=value(),
        crest_factor_db=value(), macro_dynamic_range_db=value(), compression_tendency=value(), stereo_width=value(),
        mono_compatibility=value(), frequency_balance=value(), low_end_weight=value(), midrange_focus=value(),
        high_frequency_brightness=value(), spaciousness_proxy=value(), sidechain_pumping_proxy=value(),
        transient_emphasis=value(), mix_density=value(), production_character=value(),
    )


def _melody_fallback(warning: str) -> MelodyAnalysis:
    def value() -> Any:
        return _unknown("analyzer unavailable", warning)
    return MelodyAnalysis(
        pitch_contour_available=value(), melodic_range=value(), register_value=value(), phrase_length=value(), movement=value(),
        repetition=value(), density=value(), ornamentation=value(), call_and_response=value(), hook_prominence=value(),
    )


def _instrumentation_fallback(warning: str) -> InstrumentationAnalysis:
    return InstrumentationAnalysis(
        candidates=_unknown("analyzer unavailable", warning),
        coarse_categories_only=True,
    )


def _vocals_fallback(warning: str) -> VocalsAnalysis:
    def value() -> Any:
        return _unknown("analyzer unavailable", warning)
    return VocalsAnalysis(
        presence=value(), register_value=value(), delivery=value(), phrasing=value(), density=value(), layering=value(),
        processing=value(), mix_placement=value(),
    )


def _style_fallback(warning: str) -> StyleAndMoodAnalysis:
    def value() -> Any:
        return _unknown("analyzer unavailable", warning)
    return StyleAndMoodAnalysis(
        broad_style=value(), genre_blend=value(), production_era_resemblance=value(), mood=value(), energy=value(),
        valence=value(), intensity=value(), danceability_tendency=value(), cinematic_quality=value(),
        organic_synthetic=value(), commercial_experimental=value(),
    )


def _write_progress(path: Path, stage: str, message: str, progress: int) -> None:
    temporary = path.with_suffix(".tmp")
    payload = {"stage": stage, "message": message, "progress": progress}
    temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    secure_private_file(temporary)
    for attempt in range(8):
        try:
            os.replace(temporary, path)
            break
        except PermissionError:
            if attempt == 7:
                raise
            # Windows can briefly deny replacement while the API process has
            # progress.json open for reading. Retry within a bounded window.
            time.sleep(min(0.01 * (2**attempt), 0.08))
    secure_private_file(path)


def _write_private_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False),
        encoding="utf-8",
    )
    secure_private_file(temporary)
    os.replace(temporary, path)
    secure_private_file(path)


def _check_cancel(cancel_path: Path) -> None:
    if cancel_path.exists():
        raise AnalysisCancelled("Analysis was cancelled.")


def _rms_window(audio: AudioData, start_seconds: float, end_seconds: float) -> float:
    start = min(audio.mono.size, max(0, int(round(start_seconds * audio.sample_rate))))
    end = min(audio.mono.size, max(start + 1, int(round(end_seconds * audio.sample_rate))))
    samples = audio.mono[start:end]
    return float((samples * samples).mean()) ** 0.5 if samples.size else 0.0


def _apply_deep_section_evidence(
    source: AudioData,
    structure: StructureAnalysis,
    stems: dict[str, Path],
) -> tuple[dict[str, float], list[InstrumentCandidate], float]:
    stem_audio = {name: load_audio(str(path)) for name, path in stems.items()}
    source_rms = max(float((source.mono * source.mono).mean()) ** 0.5, 1e-12)
    global_ratios: dict[str, float] = {}
    active_sections: dict[str, list[str]] = {name: [] for name in stem_audio}
    labels = {
        "drums": "drums/percussion",
        "bass": "bass",
        "other": "other tonal accompaniment",
        "vocals": "vocals",
    }
    for name, data in stem_audio.items():
        global_ratios[name] = _rms_window(data, 0.0, data.duration) / source_rms

    for section in structure.sections:
        section_source_rms = _rms_window(source, section.start_seconds, section.end_seconds)
        denominator = max(section_source_rms, source_rms * 0.01, 1e-12)
        ratios: dict[str, float] = {}
        activity: dict[str, str] = {}
        for name, data in stem_audio.items():
            stem_rms = _rms_window(data, section.start_seconds, section.end_seconds)
            ratio = stem_rms / denominator
            ratios[name] = round(ratio, 4)
            audible = stem_rms >= max(source_rms * 0.005, 1e-6) and ratio >= 0.025
            label = "prominent" if audible and ratio >= 0.45 else "present" if audible else "inactive"
            activity[name] = label
            if audible:
                active_sections[name].append(section.id)
        section.deep_evidence = SectionStemEvidence(
            relative_rms=ratios,
            activity=activity,
            method="section-aligned relative RMS from private Demucs coarse stems",
            confidence=Confidence.MEDIUM,
        )
        section.vocal_activity = activity.get("vocals", "inactive")
        section.instruments = [
            labels[name]
            for name in ("drums", "bass", "other", "vocals")
            if activity.get(name) in {"present", "prominent"}
        ]

    candidates: list[InstrumentCandidate] = []
    for name in ("drums", "bass", "other", "vocals"):
        ratio = global_ratios.get(name, 0.0)
        if ratio < 0.025:
            continue
        candidates.append(
            InstrumentCandidate(
                name=labels[name],
                prominence="prominent" if ratio >= 0.45 else "present",
                confidence=Confidence.MEDIUM,
                sections=active_sections[name],
            )
        )
    vocal_active_fraction = (
        len(active_sections.get("vocals", [])) / len(structure.sections)
        if structure.sections
        else 0.0
    )
    return global_ratios, candidates, vocal_active_fraction


def analyze_audio(
    decoded_path: str,
    file_data: dict[str, Any],
    job_id: str,
    requested_mode: str,
    progress_path: str,
    cancel_path: str,
    configured_settings: Settings | None = None,
    enable_genre_analysis: bool = False,
    enable_lyrics_analysis: bool = False,
    lyrics_consent_confirmed: bool = False,
    derive_lyrical_themes: bool = False,
) -> str:
    """Run deterministic analyzers in a worker process and return serialized JSON.

    Progress is communicated via a tiny job-local file so the API can truthfully
    publish worker stage changes without sending a non-picklable callback across
    the process boundary.
    """
    progress_file = Path(progress_path)
    cancel_file = Path(cancel_path)
    warnings: list[str] = []
    settings = configured_settings or Settings.from_env()
    device_status = inspect_torch_device(settings)
    deep_diagnostics = DeepDiagnostics(
        selected_device=device_status.selected_device,
        torch_installed=device_status.torch_installed,
        torch_version=device_status.torch_version,
        cuda_build_support=device_status.cuda_build_support,
        cuda_runtime_available=device_status.cuda_runtime_available,
        gpu_device_name=device_status.gpu_device_name,
        fallback_reason=device_status.fallback_reason if requested_mode == "deep" else None,
    )
    lyrics_summary = LyricsAnalysisSummary(
        enabled=enable_lyrics_analysis,
        status="requested" if enable_lyrics_analysis else "not_requested",
    )
    _check_cancel(cancel_file)
    _write_progress(
        progress_file,
        "inspecting_signal",
        "Inspecting decoded signal quality",
        WORKER_STAGE_PROGRESS["inspecting_signal"],
    )
    audio = load_audio(decoded_path)
    quality = signal_quality(audio)
    if audio.normalization_violation:
        warnings.append(
            "Decoded samples exceeded the expected normalized range; affected peak-derived fields were withheld."
        )
    peaks = waveform_peaks(audio)
    _check_cancel(cancel_file)

    insufficient = quality.sufficient_signal.value is not True
    if insufficient:
        warning = quality.sufficient_signal.warning or "Insufficient signal for reliable musical analysis."
        warnings.append(warning)
        spectral = spectral_features(audio)
        rhythm = _rhythm_fallback(warning)
        harmony = _harmony_fallback(warning)
        structure = _structure_fallback(audio, warning)
        timbre = with_failure_isolation("Timbre", lambda: analyze_timbre(spectral, audio), _timbre_fallback, warnings)
        production = with_failure_isolation("Production", lambda: analyze_production(audio, spectral), _production_fallback, warnings)
        melody = _melody_fallback(warning)
        instrumentation = _instrumentation_fallback(warning)
        vocals = _vocals_fallback(warning)
        style = _style_fallback(warning)
    else:
        spectral = spectral_features(audio)
        _write_progress(progress_file, "analyzing_rhythm", "Estimating tempo, beats, and rhythmic activity", WORKER_STAGE_PROGRESS["analyzing_rhythm"])
        rhythm = with_failure_isolation("Rhythm", lambda: analyze_rhythm(audio, spectral), _rhythm_fallback, warnings)
        _check_cancel(cancel_file)
        _write_progress(progress_file, "analyzing_harmony", "Estimating key and approximate harmony", WORKER_STAGE_PROGRESS["analyzing_harmony"])
        harmony = with_failure_isolation("Harmony", lambda: analyze_harmony(audio, spectral), _harmony_fallback, warnings)
        melody = with_failure_isolation("Melody", analyze_melody, _melody_fallback, warnings)
        _check_cancel(cancel_file)
        _write_progress(progress_file, "segmenting_structure", "Finding neutral section boundaries and repetitions", WORKER_STAGE_PROGRESS["segmenting_structure"])
        structure = with_failure_isolation("Structure", lambda: analyze_structure(audio, spectral), lambda message: _structure_fallback(audio, message), warnings)
        _check_cancel(cancel_file)
        _write_progress(progress_file, "analyzing_production", "Measuring timbre, loudness, dynamics, and stereo image", WORKER_STAGE_PROGRESS["analyzing_production"])
        timbre = with_failure_isolation("Timbre", lambda: analyze_timbre(spectral, audio), _timbre_fallback, warnings)
        production = with_failure_isolation("Production", lambda: analyze_production(audio, spectral), _production_fallback, warnings)
        instrumentation = with_failure_isolation(
            "Instrumentation",
            lambda: analyze_instrumentation(timbre, rhythm),
            _instrumentation_fallback,
            warnings,
        )
        vocals = with_failure_isolation("Vocals", analyze_vocals, _vocals_fallback, warnings)
        style = with_failure_isolation(
            "Style and mood",
            lambda: analyze_style(timbre, rhythm, production),
            _style_fallback,
            warnings,
        )

    effective_mode = "fast"
    capabilities = ["fast-core", "deterministic-prompting"]
    if requested_mode == "deep":
        if demucs_ready(settings) and not insufficient:
            _check_cancel(cancel_file)
            _write_progress(progress_file, "separating_stems", "Running the explicitly enabled local four-stem adapter", WORKER_STAGE_PROGRESS["separating_stems"])
            stems_dir = Path(decoded_path).parent / "stems"
            try:
                selected_device = device_status.selected_device
                try:
                    stems = run_demucs(
                        Path(decoded_path),
                        stems_dir,
                        settings,
                        cancel_requested=cancel_file.exists,
                        device=selected_device,
                    )
                except Exception:
                    _check_cancel(cancel_file)
                    if selected_device != "cuda":
                        raise
                    shutil.rmtree(stems_dir, ignore_errors=True)
                    warnings.append(
                        "CUDA Demucs execution failed safely; the adapter retried on CPU."
                    )
                    selected_device = "cpu"
                    stems = run_demucs(
                        Path(decoded_path),
                        stems_dir,
                        settings,
                        cancel_requested=cancel_file.exists,
                        device="cpu",
                    )
                    deep_diagnostics.fallback_reason = "CUDA execution failed; CPU fallback completed."
                _check_cancel(cancel_file)
                _write_progress(progress_file, "running_enhanced_taggers", "Deriving coarse per-stem energy and vocal-presence descriptors", WORKER_STAGE_PROGRESS["running_enhanced_taggers"])
                stem_ratios, candidates, vocal_active_fraction = _apply_deep_section_evidence(
                    audio,
                    structure,
                    stems,
                )
                instrumentation = InstrumentationAnalysis(
                    candidates=feature(
                        candidates,
                        Confidence.MEDIUM,
                        "relative RMS of locally separated Demucs coarse stems",
                        warning="Coarse stem categories do not establish specific instruments.",
                    ),
                    coarse_categories_only=True,
                )
                vocal_ratio = stem_ratios.get("vocals", 0.0)
                vocal_label = "present" if vocal_ratio >= 0.08 else "weak or absent"
                vocals = vocals.model_copy(
                    update={
                        "presence": feature(
                            vocal_label,
                            Confidence.MEDIUM,
                            "relative RMS of locally separated vocal stem",
                            score=round(vocal_ratio, 3),
                            evidence_kind=EvidenceKind.STRONG_ESTIMATE,
                        ),
                        "density": feature(
                            "throughout"
                            if vocal_active_fraction >= 0.75
                            else "sectional"
                            if vocal_active_fraction >= 0.2
                            else "sparse or absent",
                            Confidence.MEDIUM,
                            "fraction of structural sections with active private vocal-stem energy",
                            score=round(vocal_active_fraction, 3),
                        ),
                    }
                )
                if enable_lyrics_analysis:
                    if not lyrics_consent_confirmed:
                        lyrics_summary.status = "consent_missing"
                        lyrics_summary.warnings.append(
                            "Lyrics analysis was not run because separate transcript consent was not confirmed."
                        )
                    else:
                        lyrics_adapter = create_lyrics_adapter(settings)
                        lyrics_capability = lyrics_adapter.capability()
                        if not lyrics_capability.available:
                            lyrics_summary.status = "unavailable"
                            lyrics_summary.adapter_id = lyrics_capability.id
                            lyrics_summary.model_id = lyrics_capability.model_id
                            lyrics_summary.selected_device = "unavailable"
                            lyrics_summary.warnings.append(lyrics_capability.reason)
                        else:
                            try:
                                _check_cancel(cancel_file)
                                _write_progress(
                                    progress_file,
                                    "transcribing_lyrics",
                                    "Transcribing the private vocal stem with the explicitly enabled local adapter",
                                    WORKER_STAGE_PROGRESS["transcribing_lyrics"],
                                )
                                transcript, lyrics_summary = lyrics_adapter.transcribe(
                                    stems["vocals"],
                                    job_id,
                                    cancel_requested=cancel_file.exists,
                                )
                                _check_cancel(cancel_file)
                                job_directory = Path(decoded_path).resolve().parent
                                transcript_payload = transcript.model_dump(mode="json", by_alias=True)
                                _write_private_json(job_directory / "lyrics.json", transcript_payload)
                                _write_private_json(job_directory / "detected-lyrics.json", transcript_payload)
                                if derive_lyrical_themes:
                                    lyrics_summary.warnings.append(
                                        "Abstract themes use an isolated local-only theme request after analysis; raw transcript is never prompt evidence."
                                    )
                                _write_private_json(
                                    job_directory / "lyrics-summary.json",
                                    lyrics_summary.model_dump(mode="json", by_alias=True),
                                )
                                capabilities.append("private-lyrics-transcript")
                            except Exception:
                                _check_cancel(cancel_file)
                                lyrics_summary.status = "failed"
                                lyrics_summary.warnings.append(
                                    "The local lyrics adapter failed safely; Deep stem evidence was retained and no partial transcript was stored."
                                )
                                warnings.append("Lyrics transcription failed safely; no raw transcript was retained.")
                            finally:
                                lyrics_adapter.cleanup()
                effective_mode = "deep"
                capabilities.append("demucs-four-stem")
                deep_diagnostics.adapter_id = "demucs-four-stem"
                deep_diagnostics.method = "private Demucs stems with global and section-aligned relative RMS"
                deep_diagnostics.selected_device = selected_device
                deep_diagnostics.stem_relative_rms = {
                    name: round(value, 4) for name, value in stem_ratios.items()
                }
                if selected_device == device_status.selected_device and selected_device == "cuda":
                    deep_diagnostics.fallback_reason = None
            except AnalysisCancelled:
                raise
            except Exception:
                _check_cancel(cancel_file)
                warnings.append("The enabled local Deep adapter failed safely; Fast analysis was retained.")
                deep_diagnostics.fallback_reason = "The enabled local adapter failed; Fast analysis was retained."
            finally:
                # The path is constructed internally and checked before recursive removal.
                resolved_stems = stems_dir.resolve()
                resolved_job = Path(decoded_path).resolve().parent
                if resolved_stems.parent == resolved_job and resolved_stems.name == "stems":
                    shutil.rmtree(resolved_stems, ignore_errors=True)
        else:
            if insufficient:
                warnings.append(
                    "Deep analysis was requested, but the decoded signal was insufficient for safe stem analysis; partial Fast analysis was retained."
                )
                deep_diagnostics.fallback_reason = "Insufficient decoded signal for Deep analysis."
            else:
                warnings.append(
                    "Deep analysis was requested, but no explicitly enabled local adapter with reviewed local weights was ready; Fast analysis was used transparently."
                )
                deep_diagnostics.fallback_reason = "No explicitly enabled adapter with reviewed local weights was ready."
            if enable_lyrics_analysis:
                lyrics_summary.status = "requires_deep_stem"
                lyrics_summary.warnings.append(
                    "Lyrics analysis requires a successful Deep vocal stem and did not run."
                )
    _check_cancel(cancel_file)
    result = AnalysisResult(
        job_id=job_id,
        capabilities=capabilities,
        requested_mode=requested_mode,
        effective_mode=effective_mode,
        file=FileInfo.model_validate(file_data),
        waveform_peaks=peaks,
        signal_quality=quality,
        rhythm=rhythm,
        harmony=harmony,
        melody=melody,
        structure=structure,
        timbre=timbre,
        instrumentation=instrumentation,
        vocals=vocals,
        production=production,
        style_and_mood=style,
        lyrics_summary=lyrics_summary,
        deep_diagnostics=deep_diagnostics,
        warnings=warnings,
        analyzer_versions={
            "trackprompt-core": "0.2.0",
            "activity-detector": "2.0.0",
            "rhythm-grid": "2.0.0",
            "tonal-confidence": "2.0.0",
            "structure-segmenter": "2.0.0",
            "analysis-sanity": "1.0.0",
            "numpy": __import__("numpy").__version__,
            "scipy": __import__("scipy").__version__,
            "soundfile": __import__("soundfile").__version__,
        },
        created_at=datetime.now(UTC),
    )
    if enable_genre_analysis:
        _check_cancel(cancel_file)
        genre_adapter = create_music_tagger(settings)
        capability = genre_adapter.capability()
        if capability.available:
            _write_progress(
                progress_file,
                "tagging_genre",
                "Ranking genre and music-description similarities over bounded local windows",
                WORKER_STAGE_PROGRESS["tagging_genre"],
            )
            try:
                result.genre_analysis = genre_adapter.analyze_windows(Path(decoded_path), result)
                result.capabilities.append("clap-music-tagging")
            except Exception:
                _check_cancel(cancel_file)
                result.warnings.append(
                    "The local genre adapter failed safely; no genre result was fabricated."
                )
            finally:
                genre_adapter.cleanup()
        else:
            result.warnings.append(f"Genre tagging was requested but unavailable: {capability.reason}")
    return validate_analysis_result(result).model_dump_json(by_alias=True)
