from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic.alias_generators import to_camel

from .generative.audio_mapping import AudioMappingConfig
from .generative.choreography import GeometryChoreography
from .generative.composition import GeometryComposition
from .generative.contracts import IndexedDomainSpec, ShapeId

HexColor = Annotated[str, Field(pattern=r"^#[0-9A-Fa-f]{6}$")]
SpectrumSectionId = Literal["intro", "main", "outro"]
SpectrumPreviewSection = SpectrumSectionId | None
SpectrumRuntimeStateId = Literal["intro", "main", "outro", "post-grid-tail", "end"]


class SpectrumDesignError(ValueError):
    """Raised when the canonical visual preset cannot be used safely."""


class SpectrumDesignModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        allow_inf_nan=False,
    )


class SpectrumRenderFrame(SpectrumDesignModel):
    width: Literal[1920]
    height: Literal[1080]
    fps: Literal[60]
    safe_margin: int = Field(ge=48, le=160)


class SpectrumLogoLayout(SpectrumDesignModel):
    x: int = Field(ge=0, le=1920)
    y: int = Field(ge=0, le=1080)
    max_width: int = Field(ge=96, le=480)
    scale: float = Field(ge=0.5, le=1.5)
    opacity: float = Field(ge=0, le=1)


class SpectrumLayout(SpectrumDesignModel):
    x: int = Field(ge=0, le=1920)
    baseline_y: int = Field(ge=200, le=1080)
    bar_count: int = Field(ge=24, le=100)
    bar_width: int = Field(ge=3, le=32)
    bar_gap: int = Field(ge=1, le=24)
    max_height: int = Field(ge=80, le=640)
    scale: float = Field(ge=0.5, le=1.5)
    sensitivity: float = Field(ge=20, le=60)
    fft_size: Literal[1024, 2048, 4096, 8192]
    fft_attack: int = Field(ge=0, le=1000)
    fft_decay: int = Field(ge=0, le=2000)


class SpectrumTypography(SpectrumDesignModel):
    artist_font: str = Field(min_length=1, max_length=80)
    title_font: str = Field(min_length=1, max_length=80)
    artist_size: int = Field(ge=20, le=120)
    title_size: int = Field(ge=14, le=90)
    metadata_size: int = Field(ge=10, le=48)
    x: int = Field(ge=0, le=1920)
    y: int = Field(ge=0, le=1080)


class SpectrumPalette(SpectrumDesignModel):
    background: HexColor
    background_secondary: HexColor
    spectrum: HexColor
    text: HexColor
    accent: HexColor
    glow: HexColor
    shadow: HexColor


class SpectrumBackground(SpectrumDesignModel):
    mode: Literal["static-structured", "generative-geometry"]
    intensity: float = Field(ge=0, le=1)
    fragment_opacity: float = Field(ge=0, le=1)
    vignette_opacity: float = Field(ge=0, le=1)
    fragment_seed: int = Field(ge=0, le=2_147_483_647)
    fragment_count: int = Field(ge=12, le=36)
    depth_layers: Literal[3]
    max_motion_pixels: float = Field(ge=0, le=18)
    stroke_width: float = Field(ge=0.5, le=3)


class GenerativePerformanceProfile(SpectrumDesignModel):
    id: Literal["preview", "production", "high"]
    point_count: int = Field(ge=1_024, le=8_192)
    target_fps: Literal[30, 60]
    minimum_sustained_fps: float = Field(ge=24, le=60)
    maximum_average_frame_time_ms: float = Field(ge=8, le=42)


class GenerativeCamera(SpectrumDesignModel):
    position: tuple[float, float, float]
    target: tuple[float, float, float]
    fov_degrees: float = Field(ge=28, le=72)
    near: float = Field(gt=0, le=2)
    far: float = Field(gt=4, le=100)
    orbit_amplitude_degrees: float = Field(ge=0, le=18)
    orbit_speed: float = Field(ge=0, le=0.25)
    dolly_amplitude: float = Field(ge=0, le=0.8)

    @model_validator(mode="after")
    def validate_camera_planes(self) -> Self:
        if self.far <= self.near:
            raise ValueError("the generative camera far plane must follow its near plane")
        return self


class GenerativePropagation(SpectrumDesignModel):
    enabled: bool
    speed: float = Field(gt=0, le=8)
    decay: float = Field(ge=0, le=8)
    width: float = Field(gt=0, le=1)


class GenerativeGeometryDesign(SpectrumDesignModel):
    schema_version: Literal["1.0.0"]
    subsystem_id: Literal["wzhk-generative-geometry"]
    enabled: bool
    render_mode: Literal["neopixel-points"]
    fallback_mode: Literal["static-structured"]
    seed: int = Field(ge=0, le=2_147_483_647)
    point_domain: IndexedDomainSpec
    performance_profile: Literal["preview", "production", "high"]
    performance_profiles: list[GenerativePerformanceProfile] = Field(
        min_length=3,
        max_length=3,
    )
    shape_library: list[ShapeId] = Field(min_length=6, max_length=32)
    point_size: float = Field(ge=1, le=18)
    global_scale: float = Field(gt=0, le=4)
    camera: GenerativeCamera
    audio_mapping: AudioMappingConfig
    propagation: GenerativePropagation
    choreography: GeometryChoreography

    @model_validator(mode="after")
    def validate_geometry_design(self) -> Self:
        profiles = {profile.id: profile for profile in self.performance_profiles}
        if set(profiles) != {"preview", "production", "high"}:
            raise ValueError("generative performance profiles must be preview, production, and high")
        if profiles["production"].point_count != self.point_domain.point_count:
            raise ValueError("the indexed point domain must match the production point count")
        if not (
            profiles["preview"].point_count
            < profiles["production"].point_count
            < profiles["high"].point_count
        ):
            raise ValueError("generative point counts must increase from preview to production to high")
        if len(set(self.shape_library)) != len(self.shape_library):
            raise ValueError("generative shape-library entries must be unique")
        used_shapes = {
            transition.shape_a.shape_id
            for transition in self.choreography.transitions
        } | {
            transition.shape_b.shape_id
            for transition in self.choreography.transitions
        }
        if not used_shapes.issubset(set(self.shape_library)):
            raise ValueError("every choreographed shape must be in the trusted shape library")
        if self.point_domain.columns is None:
            raise ValueError("the NeoPixel matrix requires explicit deterministic columns")
        if self.point_domain.point_count % self.point_domain.columns != 0:
            raise ValueError("the NeoPixel point count must divide evenly into matrix rows")
        return self


class SpectrumProgress(SpectrumDesignModel):
    visible: bool
    height: int = Field(ge=1, le=12)
    opacity: float = Field(ge=0, le=1)


class SpectrumVisualState(SpectrumDesignModel):
    spectrum_color: HexColor
    accent_color: HexColor
    glow_color: HexColor
    spectrum_intensity: float = Field(ge=0, le=1)
    spectrum_scale: float = Field(ge=0.5, le=1.5)
    sensitivity: float = Field(ge=20, le=60)
    logo_opacity: float = Field(ge=0, le=1)
    text_opacity: float = Field(ge=0, le=1)
    background_intensity: float = Field(ge=0, le=1)
    glow_opacity: float = Field(ge=0, le=1)
    fragment_density: float = Field(ge=0, le=1)
    fragment_motion: float = Field(ge=0, le=1)
    line_intensity: float = Field(ge=0, le=1)


class SpectrumSectionDesign(SpectrumDesignModel):
    id: SpectrumSectionId
    label: str = Field(min_length=1, max_length=80)
    start_seconds: float = Field(ge=0, le=192)
    end_seconds: float = Field(gt=0, le=192)
    state: SpectrumVisualState


class SpectrumPostGridTail(SpectrumDesignModel):
    id: Literal["post-grid-tail"]
    label: Literal["Post-grid tail"]
    start_seconds: float = Field(ge=192, le=192)
    state: SpectrumVisualState
    end_state: SpectrumVisualState


class SpectrumTransitions(SpectrumDesignModel):
    intro_to_main_seconds: float = Field(gt=0, le=8)
    main_to_outro_seconds: float = Field(gt=0, le=8)
    final_fade_seconds: float = Field(gt=0, le=16)


class SpectrumController(SpectrumDesignModel):
    version: Literal["3.0.0"]
    preview_timing_source: Literal["external-media-player-position"]
    production_timing_source: Literal["trackprompt-production-clock"]
    update_milliseconds: int = Field(ge=10, le=250)
    preview_accuracy: Literal["preview-level"]
    production_accuracy: Literal["host-monotonic-process-boundary"]


class SpectrumDesignPreset(SpectrumDesignModel):
    schema_version: Literal["3.1.0"]
    preset_id: Literal["scattered"]
    display_name: str = Field(min_length=1, max_length=120)
    render: SpectrumRenderFrame
    logo: SpectrumLogoLayout
    spectrum: SpectrumLayout
    typography: SpectrumTypography
    palette: SpectrumPalette
    background: SpectrumBackground
    composition: GeometryComposition
    generative_geometry: GenerativeGeometryDesign
    progress: SpectrumProgress
    sections: list[SpectrumSectionDesign] = Field(min_length=3, max_length=3)
    post_grid_tail: SpectrumPostGridTail
    transitions: SpectrumTransitions
    controller: SpectrumController

    @model_validator(mode="after")
    def validate_scattered_timeline(self) -> SpectrumDesignPreset:
        expected = (
            ("intro", "Intro", 0.0, 64.0),
            ("main", "Main", 64.0, 176.0),
            ("outro", "Outro", 176.0, 192.0),
        )
        actual = tuple(
            (section.id, section.label, section.start_seconds, section.end_seconds)
            for section in self.sections
        )
        if actual != expected:
            raise ValueError("the Scattered visual timeline must be 0-64, 64-176, and 176-192 seconds")
        if self.post_grid_tail.start_seconds != 192:
            raise ValueError("the post-grid tail must begin at the 192-second musical-grid boundary")
        spectrum_width = (
            self.spectrum.bar_count * self.spectrum.bar_width
            + (self.spectrum.bar_count - 1) * self.spectrum.bar_gap
        )
        if self.spectrum.x + spectrum_width > self.render.width - self.render.safe_margin:
            raise ValueError("the spectrum must remain inside the 1920x1080 safe area")
        if self.logo.x + self.logo.max_width * self.logo.scale > self.render.width - self.render.safe_margin:
            raise ValueError("the logo must remain inside the 1920x1080 safe area")
        intro, main, outro = self.sections
        tail = self.post_grid_tail
        geometry = self.generative_geometry
        if not math.isclose(
            self.composition.envelope[-1].time_seconds,
            geometry.choreography.master_duration_seconds,
            abs_tol=0.000001,
        ):
            raise ValueError("the composition envelope must resolve at the choreography master EOF")
        if self.background.mode == "generative-geometry" and not geometry.enabled:
            raise ValueError("the generative background mode requires the geometry engine")
        if not math.isclose(
            geometry.choreography.grid_duration_seconds,
            self.post_grid_tail.start_seconds,
            abs_tol=0.001,
        ):
            raise ValueError("the geometry choreography must share the 192-second grid boundary")
        if not (
            main.state.fragment_density > intro.state.fragment_density
            and main.state.fragment_density > outro.state.fragment_density
            and outro.state.fragment_density >= tail.state.fragment_density
            and tail.state.fragment_density >= tail.end_state.fragment_density
        ):
            raise ValueError(
                "the Scattered fragment field must peak in Main and disperse through Outro and the tail"
            )
        return self


class SpectrumVisualOverrides(SpectrumDesignModel):
    spectrum_scale: float | None = Field(default=None, ge=0.75, le=1.25)
    sensitivity: float | None = Field(default=None, ge=24, le=52)
    logo_scale: float | None = Field(default=None, ge=0.7, le=1.15)
    accent_color: HexColor | None = None
    background_intensity: float | None = Field(default=None, ge=0.1, le=0.8)


class ResolvedSpectrumState(SpectrumDesignModel):
    section_id: SpectrumRuntimeStateId
    section_complete: bool
    transition_progress: float = Field(ge=0, le=1)
    state: SpectrumVisualState


def load_design_preset(path: Path) -> SpectrumDesignPreset:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        return SpectrumDesignPreset.model_validate(payload)
    except (OSError, ValueError, json.JSONDecodeError, ValidationError) as exc:
        raise SpectrumDesignError("The Scattered visual preset is invalid.") from exc


def resolve_design_preset(
    preset: SpectrumDesignPreset,
    overrides: SpectrumVisualOverrides,
    background_mode: Literal["static-structured", "generative-geometry"] | None = None,
) -> SpectrumDesignPreset:
    payload = preset.model_dump(mode="json", by_alias=True)
    spectrum = payload["spectrum"]
    logo = payload["logo"]
    background = payload["background"]
    palette = payload["palette"]
    sections = payload["sections"]
    tail = payload["postGridTail"]
    if background_mode is not None:
        background["mode"] = background_mode
    if overrides.spectrum_scale is not None:
        spectrum["scale"] = overrides.spectrum_scale
    if overrides.sensitivity is not None:
        spectrum["sensitivity"] = overrides.sensitivity
        for section in sections:
            section["state"]["sensitivity"] = min(
                60.0,
                max(
                    20.0,
                    section["state"]["sensitivity"]
                    * overrides.sensitivity
                    / preset.spectrum.sensitivity,
                ),
            )
        for state_name in ("state", "endState"):
            tail[state_name]["sensitivity"] = min(
                60.0,
                max(
                    20.0,
                    tail[state_name]["sensitivity"]
                    * overrides.sensitivity
                    / preset.spectrum.sensitivity,
                ),
            )
    if overrides.logo_scale is not None:
        logo["scale"] = overrides.logo_scale
    if overrides.accent_color is not None:
        normalized = overrides.accent_color.upper()
        palette["accent"] = normalized
        sections[1]["state"]["accentColor"] = normalized
        tail["state"]["accentColor"] = normalized
    if overrides.background_intensity is not None:
        background["intensity"] = overrides.background_intensity
        for section in sections:
            section["state"]["backgroundIntensity"] = min(
                1.0,
                section["state"]["backgroundIntensity"]
                * overrides.background_intensity
                / preset.background.intensity,
            )
        for state_name in ("state", "endState"):
            tail[state_name]["backgroundIntensity"] = min(
                1.0,
                tail[state_name]["backgroundIntensity"]
                * overrides.background_intensity
                / preset.background.intensity,
            )
    return SpectrumDesignPreset.model_validate(payload)


def _interpolate_number(start: float, end: float, progress: float) -> float:
    return start + (end - start) * progress


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    return tuple(int(value[index : index + 2], 16) for index in (1, 3, 5))  # type: ignore[return-value]


def _rgb_to_hex(value: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{round(channel):02X}" for channel in value)


def _interpolate_color(start: str, end: str, progress: float) -> str:
    start_rgb = _hex_to_rgb(start)
    end_rgb = _hex_to_rgb(end)
    return _rgb_to_hex(
        (
            _interpolate_number(float(start_rgb[0]), float(end_rgb[0]), progress),
            _interpolate_number(float(start_rgb[1]), float(end_rgb[1]), progress),
            _interpolate_number(float(start_rgb[2]), float(end_rgb[2]), progress),
        )
    )


def _interpolate_state(
    start: SpectrumVisualState,
    end: SpectrumVisualState,
    progress: float,
) -> SpectrumVisualState:
    return SpectrumVisualState(
        spectrum_color=_interpolate_color(start.spectrum_color, end.spectrum_color, progress),
        accent_color=_interpolate_color(start.accent_color, end.accent_color, progress),
        glow_color=_interpolate_color(start.glow_color, end.glow_color, progress),
        spectrum_intensity=_interpolate_number(start.spectrum_intensity, end.spectrum_intensity, progress),
        spectrum_scale=_interpolate_number(start.spectrum_scale, end.spectrum_scale, progress),
        sensitivity=_interpolate_number(start.sensitivity, end.sensitivity, progress),
        logo_opacity=_interpolate_number(start.logo_opacity, end.logo_opacity, progress),
        text_opacity=_interpolate_number(start.text_opacity, end.text_opacity, progress),
        background_intensity=_interpolate_number(start.background_intensity, end.background_intensity, progress),
        glow_opacity=_interpolate_number(start.glow_opacity, end.glow_opacity, progress),
        fragment_density=_interpolate_number(start.fragment_density, end.fragment_density, progress),
        fragment_motion=_interpolate_number(start.fragment_motion, end.fragment_motion, progress),
        line_intensity=_interpolate_number(start.line_intensity, end.line_intensity, progress),
    )


def resolve_state_at_milliseconds(
    preset: SpectrumDesignPreset,
    milliseconds: int,
    master_duration_seconds: float = 192.0,
    preview_section: SpectrumPreviewSection = None,
) -> ResolvedSpectrumState:
    if milliseconds < 0:
        raise SpectrumDesignError("Timeline positions cannot be negative.")
    section_by_id = {section.id: section for section in preset.sections}
    if preview_section is not None:
        return ResolvedSpectrumState(
            section_id=preview_section,
            section_complete=False,
            transition_progress=1,
            state=section_by_id[preview_section].state,
        )

    if not math.isfinite(master_duration_seconds) or master_duration_seconds < 192:
        raise SpectrumDesignError("The resolved master duration cannot be shorter than the musical grid.")
    seconds = min(milliseconds / 1000, master_duration_seconds)
    intro, main, outro = preset.sections
    if seconds < main.start_seconds:
        return ResolvedSpectrumState(
            section_id="intro",
            section_complete=False,
            transition_progress=1,
            state=intro.state,
        )
    if seconds < main.start_seconds + preset.transitions.intro_to_main_seconds:
        progress = (seconds - main.start_seconds) / preset.transitions.intro_to_main_seconds
        return ResolvedSpectrumState(
            section_id="main",
            section_complete=False,
            transition_progress=progress,
            state=_interpolate_state(intro.state, main.state, progress),
        )
    if seconds < outro.start_seconds:
        return ResolvedSpectrumState(
            section_id="main",
            section_complete=False,
            transition_progress=1,
            state=main.state,
        )
    if seconds < outro.start_seconds + preset.transitions.main_to_outro_seconds:
        progress = (seconds - outro.start_seconds) / preset.transitions.main_to_outro_seconds
        return ResolvedSpectrumState(
            section_id="outro",
            section_complete=False,
            transition_progress=progress,
            state=_interpolate_state(main.state, outro.state, progress),
        )
    if seconds < preset.post_grid_tail.start_seconds:
        return ResolvedSpectrumState(
            section_id="outro",
            section_complete=False,
            transition_progress=1,
            state=outro.state,
        )

    if seconds >= master_duration_seconds:
        return ResolvedSpectrumState(
            section_id="end",
            section_complete=True,
            transition_progress=1,
            state=preset.post_grid_tail.end_state,
        )

    final_fade_start = max(
        preset.post_grid_tail.start_seconds,
        master_duration_seconds - preset.transitions.final_fade_seconds,
    )
    if seconds < final_fade_start:
        return ResolvedSpectrumState(
            section_id="post-grid-tail",
            section_complete=False,
            transition_progress=0,
            state=preset.post_grid_tail.state,
        )
    fade_progress = min(1.0, (seconds - final_fade_start) / max(0.001, master_duration_seconds - final_fade_start))
    return ResolvedSpectrumState(
        section_id="post-grid-tail",
        section_complete=False,
        transition_progress=fade_progress,
        state=_interpolate_state(
            preset.post_grid_tail.state,
            preset.post_grid_tail.end_state,
            fade_progress,
        ),
    )
