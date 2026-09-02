from __future__ import annotations

from pathlib import PurePosixPath
from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel


class SpectrumContractModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class SpectrumProject(SpectrumContractModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,79}$")
    artist: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=200)


class SpectrumTimeSignature(SpectrumContractModel):
    numerator: int = Field(gt=0, le=32)
    denominator: int = Field(gt=0, le=32)


class SpectrumTrack(SpectrumContractModel):
    bpm: float = Field(gt=0, le=400)
    time_signature: SpectrumTimeSignature
    total_bars: int = Field(gt=0, le=100_000)
    grid_duration_seconds: float = Field(
        gt=0,
        le=86_400,
        validation_alias=AliasChoices("gridDurationSeconds", "expectedDurationSeconds"),
        serialization_alias="gridDurationSeconds",
    )
    audio_asset_directory: str
    preferred_master_extensions: list[str] = Field(min_length=1, max_length=12)

    @field_validator("audio_asset_directory")
    @classmethod
    def validate_audio_directory(cls, value: str) -> str:
        return validate_relative_contract_path(value)

    @field_validator("preferred_master_extensions")
    @classmethod
    def validate_extensions(cls, value: list[str]) -> list[str]:
        normalized = [item.lower() for item in value]
        if any(not item.startswith(".") or len(item) > 10 for item in normalized):
            raise ValueError("master extensions must be short dotted suffixes")
        if len(set(normalized)) != len(normalized):
            raise ValueError("master extensions must be unique")
        return normalized


class SpectrumSection(SpectrumContractModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,39}$")
    label: str = Field(min_length=1, max_length=80)
    start_bar_inclusive: int = Field(gt=0)
    end_bar_exclusive: int = Field(gt=0)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    visual_cue: str = Field(pattern=r"^[a-z][a-z0-9-]{0,79}$")


class SpectrumBranding(SpectrumContractModel):
    brand: str = Field(min_length=1, max_length=200)
    logo_asset_directory: str
    remove_monstercat_identity_from_generated_workspace: bool

    @field_validator("logo_asset_directory")
    @classmethod
    def validate_logo_directory(cls, value: str) -> str:
        return validate_relative_contract_path(value)


class SpectrumCaptureSettings(SpectrumContractModel):
    width: int = Field(gt=0, le=7680)
    height: int = Field(gt=0, le=4320)
    fps: int = Field(gt=0, le=240)


class SpectrumAudioPolicy(SpectrumContractModel):
    visualize_system_output: bool
    replace_captured_audio_with_original_master: bool


class SpectrumRendererConfig(SpectrumContractModel):
    platform: Literal["windows"]
    engine: Literal["rainmeter-audio-spectrum"]
    vendor_source_directory: str
    workspace_root: str
    capture: SpectrumCaptureSettings
    audio_policy: SpectrumAudioPolicy

    @field_validator("vendor_source_directory", "workspace_root")
    @classmethod
    def validate_renderer_directory(cls, value: str) -> str:
        return validate_relative_contract_path(value)


class SpectrumRenderRequest(SpectrumContractModel):
    schema_version: Literal["1.0.0"]
    renderer_id: Literal["wzhk-spectrum"]
    project: SpectrumProject
    track: SpectrumTrack
    sections: list[SpectrumSection] = Field(min_length=1, max_length=1000)
    branding: SpectrumBranding
    renderer: SpectrumRendererConfig

    @model_validator(mode="after")
    def validate_timing_contract(self) -> SpectrumRenderRequest:
        if self.project.artist != "DJ WaZaHaKa" or self.project.title != "Scattered":
            raise ValueError("the Milestone 1 contract must identify DJ WaZaHaKa - Scattered")
        if self.track.bpm != 120:
            raise ValueError("the Scattered contract must use 120 BPM")
        if (
            self.track.time_signature.numerator,
            self.track.time_signature.denominator,
        ) != (4, 4):
            raise ValueError("the Scattered contract must use 4/4 meter")
        if self.track.total_bars != 96 or self.track.grid_duration_seconds != 192:
            raise ValueError("the Scattered contract must cover 96 bars and 192 seconds")

        expected = (
            ("intro", "Intro", 1, 33, 0.0, 64.0),
            ("main", "Main", 33, 89, 64.0, 176.0),
            ("outro", "Outro", 89, 97, 176.0, 192.0),
        )
        if len(self.sections) != len(expected):
            raise ValueError("the Scattered contract must contain exactly three sections")

        covered_bars = 0
        next_bar = 1
        next_second = 0.0
        for section, expected_section in zip(self.sections, expected, strict=True):
            identity = (
                section.id,
                section.label,
                section.start_bar_inclusive,
                section.end_bar_exclusive,
                section.start_seconds,
                section.end_seconds,
            )
            if identity != expected_section:
                raise ValueError("the Scattered section ranges or timestamps are invalid")
            if section.start_bar_inclusive != next_bar or section.start_seconds != next_second:
                raise ValueError("section ranges must be ordered, contiguous, and non-overlapping")
            if (
                section.end_bar_exclusive <= section.start_bar_inclusive
                or section.end_seconds <= section.start_seconds
            ):
                raise ValueError("section ranges must have positive duration")
            covered_bars += section.end_bar_exclusive - section.start_bar_inclusive
            next_bar = section.end_bar_exclusive
            next_second = section.end_seconds

        if covered_bars != self.track.total_bars or next_bar != 97 or next_second != 192:
            raise ValueError("section ranges must cover exactly 96 bars and 192 seconds")
        return self


def validate_relative_contract_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    candidate = PurePosixPath(normalized)
    if (
        not normalized
        or candidate.is_absolute()
        or ".." in candidate.parts
        or any(part in {"", "."} for part in candidate.parts)
        or ":" in normalized
    ):
        raise ValueError("contract paths must be safe repository-relative paths")
    return candidate.as_posix()
