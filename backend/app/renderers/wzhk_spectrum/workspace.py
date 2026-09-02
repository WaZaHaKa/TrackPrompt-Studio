from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from ...privacy import secure_private_directory, secure_private_file
from ..schemas import (
    RendererContractSummary,
    SpectrumWorkspaceJob,
    SpectrumWorkspacePrepareRequest,
)
from .contracts import SpectrumRenderRequest
from .design import (
    SpectrumDesignPreset,
    SpectrumVisualState,
    resolve_design_preset,
)
from .generative.workspace import materialize_geometry_runtime
from .preflight import (
    EXPECTED_VENDOR_COMMIT,
    SpectrumPaths,
    SpectrumPreflightOutcome,
    ensure_within,
    hash_tree,
    iter_tree_files,
    sha256_file,
)
from .production import SpectrumMasterTiming, SpectrumProductionState


class SpectrumWorkspaceError(RuntimeError):
    """Safe domain error raised while preparing or loading a Spectrum workspace."""


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode(
        "utf-8"
    )


def _write_json(path: Path, value: Any) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(_json_bytes(value))
    secure_private_file(temporary)
    os.replace(temporary, path)
    secure_private_file(path)


def _write_text(path: Path, value: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(value.rstrip() + "\n", encoding="utf-8", newline="\n")
    secure_private_file(temporary)
    os.replace(temporary, path)
    secure_private_file(path)


def _copy_asset(source: Path, destination: Path) -> None:
    shutil.copyfile(source, destination)
    secure_private_file(destination)


def _publish_directory(
    staging_root: Path,
    job_root: Path,
    *,
    retry_permission_errors: bool | None = None,
) -> None:
    retry = os.name == "nt" if retry_permission_errors is None else retry_permission_errors
    deadline = time.monotonic() + 10
    while True:
        try:
            os.replace(staging_root, job_root)
            return
        except PermissionError:
            if not retry or time.monotonic() >= deadline or job_root.exists():
                raise
            time.sleep(0.1)


def _replace_visible_branding(skin_root: Path, logo_filename: str) -> None:
    for path in sorted(skin_root.rglob("*.ini")):
        content = path.read_text(encoding="utf-8-sig")
        updated = re.sub(
            r"Monstercat Visualizer",
            "WZHK Spectrum",
            content,
            flags=re.IGNORECASE,
        )
        updated = re.sub(
            r"MonstercatVisualizer",
            "WZHKSpectrum",
            updated,
            flags=re.IGNORECASE,
        )
        updated = re.sub(r"Monstercat", "WZHK", updated, flags=re.IGNORECASE)
        if updated != content:
            path.write_text(updated, encoding="utf-8", newline="\n")

    variables_path = skin_root / "@Resources" / "variables.ini"
    variables = variables_path.read_text(encoding="utf-8-sig").rstrip()
    variables += (
        "\n\n; Deterministic TrackPrompt WZHK Spectrum job values\n"
        "WZHKArtist=DJ WaZaHaKa\n"
        "WZHKTrack=SCATTERED\n"
        f"WZHKLogo={logo_filename}\n"
        "WZHKBPM=120\n"
        "WZHKMeter=4/4\n"
        "WZHKTotalBars=96\n"
        "WZHKExpectedDurationSeconds=192\n"
    )
    variables_path.write_text(variables, encoding="utf-8", newline="\n")

    for relative in (Path("Song Information") / "Left.ini", Path("Song Information") / "Right.ini"):
        path = skin_root / relative
        content = path.read_text(encoding="utf-8-sig")
        content, artist_replacements = re.subn(
            r"(?m)^MeasureName=MeasureArtist\s*$",
            "Text=#WZHKArtist#",
            content,
            count=1,
        )
        content, track_replacements = re.subn(
            r"(?m)^MeasureName=MeasureTrack\s*$",
            "Text=#WZHKTrack#",
            content,
            count=1,
        )
        if artist_replacements != 1 or track_replacements != 1:
            raise SpectrumWorkspaceError("The copied song-information skin could not be branded safely.")
        path.write_text(content, encoding="utf-8", newline="\n")

    cover_path = skin_root / "Song Information" / "Cover" / "Cover.ini"
    cover = cover_path.read_text(encoding="utf-8-sig")
    cover = re.sub(
        r"(?m)^ImageName=#@#images\\nocover\.png\s*$",
        lambda _match: f"ImageName=#@#images\\{logo_filename}",
        cover,
        count=1,
    )
    cover, cover_replacements = re.subn(
        r"(?m)^MeasureName=MeasureCover\s*$",
        lambda _match: f"ImageName=#@#images\\{logo_filename}",
        cover,
        count=1,
    )
    if cover_replacements != 1:
        raise SpectrumWorkspaceError("The copied cover skin could not be branded safely.")
    cover_path.write_text(cover, encoding="utf-8", newline="\n")

    production_ini_text = "\n".join(
        path.read_text(encoding="utf-8-sig")
        for path in sorted(skin_root.rglob("*.ini"))
    )
    if re.search(r"monstercat", production_ini_text, flags=re.IGNORECASE):
        raise SpectrumWorkspaceError("Visible upstream production branding remained in the copied skin.")


def _replace_variable(content: str, name: str, value: str) -> str:
    updated, replacements = re.subn(
        rf"(?m)^{re.escape(name)}=.*$",
        f"{name}={value}",
        content,
        count=1,
    )
    if replacements != 1:
        raise SpectrumWorkspaceError(
            f"The copied Spectrum variable {name} could not be materialized safely."
        )
    return updated


def _hex_rgb(value: str) -> tuple[int, int, int]:
    return tuple(int(value[index : index + 2], 16) for index in (1, 3, 5))  # type: ignore[return-value]


def _rgb(value: str) -> str:
    return ",".join(str(channel) for channel in _hex_rgb(value))


def _fragment_units(seed: int, count: int) -> list[float]:
    """Return a platform-stable deterministic sequence without touching global RNG state."""
    state = seed & 0x7FFFFFFF
    values: list[float] = []
    for _index in range(count):
        state = (1_103_515_245 * state + 12_345) & 0x7FFFFFFF
        values.append(state / 0x7FFFFFFF)
    return values


def _fragment_field_paths(design: SpectrumDesignPreset) -> dict[str, list[str]]:
    background = design.background
    units = iter(_fragment_units(background.fragment_seed, background.fragment_count * 12))
    zones = (
        (690, 1730, 72, 330),
        (1120, 1780, 250, 720),
        (46, 430, 640, 970),
        (620, 1740, 870, 1015),
        (740, 1180, 340, 560),
    )
    layer_scales = {"far": 1.35, "mid": 0.95, "near": 0.62}
    counts = {
        "far": background.fragment_count // 3,
        "mid": background.fragment_count // 3,
        "near": background.fragment_count - 2 * (background.fragment_count // 3),
    }
    paths: dict[str, list[str]] = {"far": [], "mid": [], "near": [], "lines": []}
    for layer_index, layer in enumerate(("far", "mid", "near")):
        scale = layer_scales[layer]
        for index in range(counts[layer]):
            zone = zones[(index * 2 + layer_index) % len(zones)]
            x = round(zone[0] + next(units) * (zone[1] - zone[0]))
            y = round(zone[2] + next(units) * (zone[3] - zone[2]))
            width = round((74 + next(units) * 190) * scale)
            height = round((28 + next(units) * 118) * scale)
            x = min(x, design.render.width - design.render.safe_margin - width)
            y = min(y, design.render.height - design.render.safe_margin - height)
            leading_cut = round(width * (0.08 + next(units) * 0.24))
            trailing_cut = round(width * (0.06 + next(units) * 0.22))
            vertical_shift = round(height * (next(units) * 0.36 - 0.18))
            paths[layer].append(
                f"{x + leading_cut},{y} | "
                f"LineTo {x + width},{y + vertical_shift} | "
                f"LineTo {x + width - trailing_cut},{y + height} | "
                f"LineTo {x},{y + round(height * 0.72)} | ClosePath 1"
            )

    for index in range(max(8, background.fragment_count // 2)):
        zone = zones[(index * 3 + 1) % len(zones)]
        x = round(zone[0] + next(units) * (zone[1] - zone[0]))
        y = round(zone[2] + next(units) * (zone[3] - zone[2]))
        length = round(70 + next(units) * 260)
        rise = round((next(units) - 0.5) * 82)
        end_x = min(design.render.width - design.render.safe_margin, x + length)
        end_y = min(
            design.render.height - design.render.safe_margin,
            max(design.render.safe_margin, y + rise),
        )
        paths["lines"].append(f"{x},{y},{end_x},{end_y}")
    return paths


def _fragment_meter_lines(
    design: SpectrumDesignPreset,
    layer: str,
    paths: list[str],
) -> list[str]:
    title = layer.title()
    lines = [
        f"[MeterWZHKField{title}]",
        "Meter=Shape",
        f"X=#WZHKField{title}X#",
        f"Y=#WZHKField{title}Y#",
        f"W={design.render.width}",
        f"H={design.render.height}",
        "DynamicVariables=1",
        "Group=WZHKDynamic | WZHKBackgroundMotion",
    ]
    for index, path in enumerate(paths, start=1):
        shape_option = "Shape" if index == 1 else f"Shape{index}"
        path_name = f"WZHK{title}Path{index:02d}"
        lines.extend(
            [
                f"{shape_option}=Path {path_name} | StrokeWidth {design.background.stroke_width:.3f} | Stroke Color #WZHKField{title}Stroke# | Fill Color #WZHKField{title}Fill#",
                f"{path_name}={path}",
            ]
        )
    lines.append("")
    return lines


def _fragment_line_meter_lines(
    design: SpectrumDesignPreset,
    paths: list[str],
) -> list[str]:
    lines = [
        "[MeterWZHKFieldLines]",
        "Meter=Shape",
        "X=#WZHKFieldLinesX#",
        "Y=#WZHKFieldLinesY#",
        f"W={design.render.width}",
        f"H={design.render.height}",
        "DynamicVariables=1",
        "Group=WZHKDynamic | WZHKBackgroundMotion",
    ]
    for index, path in enumerate(paths, start=1):
        shape_option = "Shape" if index == 1 else f"Shape{index}"
        lines.append(
            f"{shape_option}=Line {path} | StrokeWidth {design.background.stroke_width * 0.72:.3f} | Stroke Color #WZHKFieldLineColor#"
        )
    lines.append("")
    return lines


def _lua_state(state: SpectrumVisualState) -> str:
    fields = {
        "spectrum": _hex_rgb(state.spectrum_color),
        "accent": _hex_rgb(state.accent_color),
        "glow": _hex_rgb(state.glow_color),
    }
    return (
        "{"
        f"spectrum={{{','.join(str(value) for value in fields['spectrum'])}}},"
        f"accent={{{','.join(str(value) for value in fields['accent'])}}},"
        f"glow={{{','.join(str(value) for value in fields['glow'])}}},"
        f"spectrumIntensity={state.spectrum_intensity:.6f},"
        f"spectrumScale={state.spectrum_scale:.6f},"
        f"sensitivity={state.sensitivity:.6f},"
        f"logoOpacity={state.logo_opacity:.6f},"
        f"textOpacity={state.text_opacity:.6f},"
        f"backgroundIntensity={state.background_intensity:.6f},"
        f"glowOpacity={state.glow_opacity:.6f},"
        f"fragmentDensity={state.fragment_density:.6f},"
        f"fragmentMotion={state.fragment_motion:.6f},"
        f"lineIntensity={state.line_intensity:.6f}"
        "}"
    )


def _controller_lua(
    design: SpectrumDesignPreset,
    timing: SpectrumMasterTiming,
) -> str:
    intro, main, outro = design.sections
    tail = design.post_grid_tail
    transitions = design.transitions
    return f"""-- Generated deterministically by TrackPrompt Studio. Do not edit this job copy by hand.
local positionMeasure = nil
local lastSignature = ''
local lastLogoAlpha = -1
local lastMotionSignature = ''

local STATES = {{
  intro={_lua_state(intro.state)},
  main={_lua_state(main.state)},
  outro={_lua_state(outro.state)},
  tail={_lua_state(tail.state)},
  ending={_lua_state(tail.end_state)}
}}

local function clamp(value, low, high)
  return math.max(low, math.min(high, value))
end

local function mix(a, b, progress)
  return a + (b - a) * progress
end

local function mixColor(a, b, progress)
  return {{mix(a[1], b[1], progress), mix(a[2], b[2], progress), mix(a[3], b[3], progress)}}
end

local function blend(a, b, progress)
  return {{
    spectrum=mixColor(a.spectrum, b.spectrum, progress),
    accent=mixColor(a.accent, b.accent, progress),
    glow=mixColor(a.glow, b.glow, progress),
    spectrumIntensity=mix(a.spectrumIntensity, b.spectrumIntensity, progress),
    spectrumScale=mix(a.spectrumScale, b.spectrumScale, progress),
    sensitivity=mix(a.sensitivity, b.sensitivity, progress),
    logoOpacity=mix(a.logoOpacity, b.logoOpacity, progress),
    textOpacity=mix(a.textOpacity, b.textOpacity, progress),
    backgroundIntensity=mix(a.backgroundIntensity, b.backgroundIntensity, progress),
    glowOpacity=mix(a.glowOpacity, b.glowOpacity, progress),
    fragmentDensity=mix(a.fragmentDensity, b.fragmentDensity, progress),
    fragmentMotion=mix(a.fragmentMotion, b.fragmentMotion, progress),
    lineIntensity=mix(a.lineIntensity, b.lineIntensity, progress)
  }}
end

local function stateAt(seconds, preview)
  if preview == 'intro' or preview == 'main' or preview == 'outro' then
    return preview, STATES[preview]
  end
  if seconds < {main.start_seconds:.6f} then
    return 'intro', STATES.intro
  end
  if seconds < {main.start_seconds + transitions.intro_to_main_seconds:.6f} then
    local progress = (seconds - {main.start_seconds:.6f}) / {transitions.intro_to_main_seconds:.6f}
    return 'main', blend(STATES.intro, STATES.main, progress)
  end
  if seconds < {outro.start_seconds:.6f} then
    return 'main', STATES.main
  end
  if seconds < {outro.start_seconds + transitions.main_to_outro_seconds:.6f} then
    local progress = (seconds - {outro.start_seconds:.6f}) / {transitions.main_to_outro_seconds:.6f}
    return 'outro', blend(STATES.main, STATES.outro, progress)
  end
  if seconds < {tail.start_seconds:.6f} then
    return 'outro', STATES.outro
  end
  if seconds >= {timing.master_duration_seconds:.6f} then
    return 'end', STATES.ending
  end
  if seconds < {timing.final_fade_start_seconds:.6f} then
    return 'post-grid-tail', STATES.tail
  end
  local progress = clamp((seconds - {timing.final_fade_start_seconds:.6f}) / {max(0.001, timing.master_duration_seconds - timing.final_fade_start_seconds):.6f}, 0, 1)
  return 'post-grid-tail', blend(STATES.tail, STATES.ending, progress)
end

local function productionClock()
  local path = SKIN:ReplaceVariables('#WZHKProductionClockFile#')
  local file = io.open(path, 'r')
  if file == nil then
    return 0
  end
  local raw = file:read('*l')
  file:close()
  local value = tonumber(raw)
  if value == nil then
    return 0
  end
  return value
end

local function rgba(color, alpha)
  return string.format('%d,%d,%d,%d', math.floor(color[1] + 0.5), math.floor(color[2] + 0.5), math.floor(color[3] + 0.5), math.floor(clamp(alpha, 0, 1) * 255 + 0.5))
end

local function apply(section, state)
  local barHeight = {design.spectrum.max_height:.6f} * {design.spectrum.scale:.6f} * state.spectrumScale
  local signature = string.format('%s|%.3f|%.3f|%.3f|%.3f|%.3f|%.3f|%.3f', section, state.spectrumIntensity, state.spectrumScale, state.sensitivity, state.backgroundIntensity, state.glowOpacity, state.fragmentDensity, state.lineIntensity)
  if signature == lastSignature then
    return
  end
  lastSignature = signature
  SKIN:Bang('!SetVariable', 'WZHKSection', string.upper(section))
  SKIN:Bang('!SetVariable', 'WZHKBarHeight', string.format('%.3f', barHeight))
  SKIN:Bang('!SetVariable', 'WZHKBarColor', rgba(state.spectrum, state.spectrumIntensity))
  SKIN:Bang('!SetVariable', 'WZHKGlowColor', rgba(state.glow, state.glowOpacity))
  SKIN:Bang('!SetVariable', 'WZHKAccentColor', rgba(state.accent, {design.background.fragment_opacity:.6f} * state.backgroundIntensity))
  SKIN:Bang('!SetVariable', 'WZHKBackgroundWash', rgba(state.accent, state.backgroundIntensity * {design.background.intensity:.6f}))
  local fragmentBase = {design.background.fragment_opacity:.6f} * state.backgroundIntensity
  local farReveal = 0.38 + state.fragmentDensity * 0.62
  local midReveal = clamp((state.fragmentDensity - 0.18) / 0.62, 0, 1)
  local nearReveal = clamp((state.fragmentDensity - 0.56) / 0.44, 0, 1)
  local nearColor = mixColor(state.accent, state.glow, 0.52)
  SKIN:Bang('!SetVariable', 'WZHKFieldFarFill', rgba(state.accent, fragmentBase * farReveal * 0.22))
  SKIN:Bang('!SetVariable', 'WZHKFieldFarStroke', rgba(state.accent, fragmentBase * farReveal * 0.68))
  SKIN:Bang('!SetVariable', 'WZHKFieldMidFill', rgba(state.glow, fragmentBase * midReveal * 0.18))
  SKIN:Bang('!SetVariable', 'WZHKFieldMidStroke', rgba(state.glow, fragmentBase * midReveal * 0.78))
  SKIN:Bang('!SetVariable', 'WZHKFieldNearFill', rgba(nearColor, fragmentBase * nearReveal * 0.16))
  SKIN:Bang('!SetVariable', 'WZHKFieldNearStroke', rgba(nearColor, fragmentBase * nearReveal * 0.90))
  SKIN:Bang('!SetVariable', 'WZHKFieldLineColor', rgba(nearColor, {design.background.fragment_opacity:.6f} * state.lineIntensity * 0.70))
  SKIN:Bang('!SetVariable', 'WZHKTextColor', '{_rgb(design.palette.text)},' .. math.floor(clamp(state.textOpacity, 0, 1) * 255 + 0.5))
  local logoAlpha = math.floor(clamp({design.logo.opacity:.6f} * state.logoOpacity, 0, 1) * 255 + 0.5)
  if logoAlpha ~= lastLogoAlpha then
    SKIN:Bang('!SetVariable', 'WZHKLogoAlpha', tostring(logoAlpha))
    SKIN:Bang('!UpdateMeter', 'MeterWZHKLogo')
    lastLogoAlpha = logoAlpha
  end
  SKIN:Bang('!SetOption', 'MeasureWZHKAudio', 'Sensitivity', string.format('%.3f', state.sensitivity))
  SKIN:Bang('!UpdateMeasure', 'MeasureWZHKAudio')
  SKIN:Bang('!UpdateMeterGroup', 'WZHKDynamic')
  SKIN:Bang('!Redraw')
end

local function applyMotion(seconds, state)
  local travel = {design.background.max_motion_pixels:.6f} * state.fragmentMotion
  local farX = math.sin(seconds * 0.071 + 0.4) * travel * 0.34
  local farY = math.cos(seconds * 0.053 + 1.1) * travel * 0.24
  local midX = math.sin(seconds * 0.109 + 1.7) * travel * 0.62
  local midY = math.cos(seconds * 0.083 + 2.2) * travel * 0.48
  local nearX = math.sin(seconds * 0.157 + 2.9) * travel
  local nearY = math.cos(seconds * 0.127 + 3.4) * travel * 0.72
  local lineX = math.sin(seconds * 0.091 + 4.2) * travel * 0.46
  local lineY = math.cos(seconds * 0.067 + 4.8) * travel * 0.32
  local signature = string.format('%.2f|%.2f|%.2f|%.2f|%.2f|%.2f|%.2f|%.2f', farX, farY, midX, midY, nearX, nearY, lineX, lineY)
  if signature == lastMotionSignature then
    return
  end
  lastMotionSignature = signature
  SKIN:Bang('!SetVariable', 'WZHKFieldFarX', string.format('%.2f', farX))
  SKIN:Bang('!SetVariable', 'WZHKFieldFarY', string.format('%.2f', farY))
  SKIN:Bang('!SetVariable', 'WZHKFieldMidX', string.format('%.2f', midX))
  SKIN:Bang('!SetVariable', 'WZHKFieldMidY', string.format('%.2f', midY))
  SKIN:Bang('!SetVariable', 'WZHKFieldNearX', string.format('%.2f', nearX))
  SKIN:Bang('!SetVariable', 'WZHKFieldNearY', string.format('%.2f', nearY))
  SKIN:Bang('!SetVariable', 'WZHKFieldLinesX', string.format('%.2f', lineX))
  SKIN:Bang('!SetVariable', 'WZHKFieldLinesY', string.format('%.2f', lineY))
  SKIN:Bang('!UpdateMeterGroup', 'WZHKBackgroundMotion')
  SKIN:Bang('!Redraw')
end

function Initialize()
  positionMeasure = SKIN:GetMeasure('MeasureWZHKPosition')
  lastSignature = ''
  lastLogoAlpha = -1
  lastMotionSignature = ''
end

function Update()
  local preview = SKIN:GetVariable('WZHKPreviewSection', 'timeline')
  local timelineSource = SKIN:GetVariable('WZHKTimelineSource', 'external-media-player-position')
  local seconds = 0
  if timelineSource == 'trackprompt-production-clock' then
    seconds = clamp(productionClock(), 0, {timing.master_duration_seconds:.6f})
  elseif positionMeasure ~= nil then
    seconds = clamp(positionMeasure:GetValue(), 0, {timing.master_duration_seconds:.6f})
  end
  local section, state = stateAt(seconds, preview)
  apply(section, state)
  applyMotion(seconds, state)
  return seconds
end
"""


def _presentation_ini(
    design: SpectrumDesignPreset,
    timing: SpectrumMasterTiming,
    logo_filename: str,
    preview_section: str | None,
    mode: str,
) -> str:
    spectrum = design.spectrum
    typography = design.typography
    logo = design.logo
    preview_value = preview_section or "timeline"
    timeline_source = (
        design.controller.production_timing_source
        if mode == "production"
        else design.controller.preview_timing_source
    )
    total_spectrum_width = (
        spectrum.bar_count * spectrum.bar_width
        + (spectrum.bar_count - 1) * spectrum.bar_gap
    )
    progress_hidden = 0 if design.progress.visible else 1
    metadata_text = "120 BPM  /  4/4  /  #WZHKSection#"
    generative_background = (
        design.background.mode == "generative-geometry"
        and design.generative_geometry.enabled
    )
    field_paths = {} if generative_background else _fragment_field_paths(design)
    lines = [
        "; Generated deterministically by TrackPrompt Studio. This is a private job-local presentation skin.",
        "[Rainmeter]",
        "Group=WZHKSpectrum | WZHKPresentation",
        f"Update={design.controller.update_milliseconds}",
        "AccurateText=1",
        "DynamicWindowSize=0",
        "BackgroundMode=2",
        "SolidColor=0,0,0,0" if generative_background else "SolidColor=0,0,0,1",
        "OnRefreshAction=[!EnableMeasure \"MeasureWZHKPosition\"][!CommandMeasure \"MeasureWZHKSectionController\" \"Initialize()\"]",
        "",
        "[Metadata]",
        "Name=WZHK Spectrum — Scattered Presentation",
        "Author=TrackPrompt Studio / DJ WaZaHaKa",
        "Version=1.0.0",
        "License=Generated job integration; upstream component license is preserved at ..\\LICENSE",
        "Information=Deterministic section-aware preview skin. Capture remains operator-controlled.",
        "",
        "[Variables]",
        "@include=#@#variables.ini",
        f"WZHKPreviewSection={preview_value}",
        f"WZHKTimelineSource={timeline_source}",
        "WZHKProductionClockFile=#@#runtime\\production-clock.txt",
        f"WZHKGridDurationSeconds={timing.grid_duration_seconds:.6f}",
        f"WZHKMasterDurationSeconds={timing.master_duration_seconds:.6f}",
        f"WZHKPostGridTailSeconds={timing.tail_duration_seconds:.6f}",
        "WZHKSection=INTRO",
        f"WZHKCanvasWidth={design.render.width}",
        f"WZHKCanvasHeight={design.render.height}",
        f"WZHKSpectrumX={spectrum.x}",
        f"WZHKSpectrumBaselineY={spectrum.baseline_y}",
        f"WZHKBarWidth={spectrum.bar_width}",
        f"WZHKBarGap={spectrum.bar_gap}",
        f"WZHKBarHeight={spectrum.max_height * spectrum.scale * design.sections[0].state.spectrum_scale:.3f}",
        f"WZHKBarColor={_rgb(design.sections[0].state.spectrum_color)},174",
        f"WZHKGlowColor={_rgb(design.sections[0].state.glow_color)},41",
        f"WZHKAccentColor={_rgb(design.sections[0].state.accent_color)},18",
        f"WZHKBackgroundWash={_rgb(design.sections[0].state.accent_color)},32",
        f"WZHKTextColor={_rgb(design.palette.text)},230",
        f"WZHKLogoAlpha={round(255 * logo.opacity * design.sections[0].state.logo_opacity)}",
        f"WZHKFieldFarFill={_rgb(design.sections[0].state.accent_color)},4",
        f"WZHKFieldFarStroke={_rgb(design.sections[0].state.accent_color)},13",
        f"WZHKFieldMidFill={_rgb(design.sections[0].state.glow_color)},0",
        f"WZHKFieldMidStroke={_rgb(design.sections[0].state.glow_color)},3",
        f"WZHKFieldNearFill={_rgb(design.sections[0].state.glow_color)},0",
        f"WZHKFieldNearStroke={_rgb(design.sections[0].state.glow_color)},0",
        f"WZHKFieldLineColor={_rgb(design.sections[0].state.accent_color)},18",
        "WZHKFieldFarX=0",
        "WZHKFieldFarY=0",
        "WZHKFieldMidX=0",
        "WZHKFieldMidY=0",
        "WZHKFieldNearX=0",
        "WZHKFieldNearY=0",
        "WZHKFieldLinesX=0",
        "WZHKFieldLinesY=0",
        "",
        "[MeasureWZHKPosition]",
        "Measure=NowPlaying",
        "PlayerName=#PlayerName#",
        "PlayerType=Position",
        "DynamicVariables=1",
        "",
        "[MeasureWZHKAudio]",
        "Measure=Plugin",
        "Plugin=AudioLevel",
        "Port=Output",
        f"FFTSize={spectrum.fft_size}",
        f"FFTOverlap={spectrum.fft_size // 2}",
        f"FFTAttack={spectrum.fft_attack}",
        f"FFTDecay={spectrum.fft_decay}",
        f"Bands={spectrum.bar_count}",
        "FreqMin=20",
        "FreqMax=16000",
        f"Sensitivity={design.sections[0].state.sensitivity:.3f}",
        "",
        "[MeasureWZHKSectionController]",
        "Measure=Script",
        "ScriptFile=#@#scripts\\WZHKSectionController.lua",
        "DynamicVariables=1",
        "",
    ]
    for index in range(spectrum.bar_count):
        lines.extend(
            [
                f"[MeasureWZHKBand{index}]",
                "Measure=Plugin",
                "Plugin=AudioLevel",
                "Parent=MeasureWZHKAudio",
                "Type=Band",
                f"BandIdx={index + 1}",
                "Channel=Avg",
                "",
            ]
        )

    if generative_background:
        lines.extend(
            [
                "[MeterWZHKCaptureBounds]",
                "Meter=Image",
                "X=0",
                "Y=0",
                f"W={design.render.width}",
                f"H={design.render.height}",
                "SolidColor=0,0,0,0",
                "AntiAlias=1",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "[MeterWZHKBackground]",
                "Meter=Image",
                "X=0",
                "Y=0",
                f"W={design.render.width}",
                f"H={design.render.height}",
                f"SolidColor={_rgb(design.palette.background)},255",
                "Group=WZHKDynamic",
                "",
                "[MeterWZHKBackgroundWash]",
                "Meter=Image",
                "X=0",
                "Y=0",
                f"W={design.render.width}",
                f"H={design.render.height}",
                "SolidColor=#WZHKBackgroundWash#",
                "DynamicVariables=1",
                "Group=WZHKDynamic",
                "",
            ]
        )
        for layer in ("far", "mid", "near"):
            lines.extend(_fragment_meter_lines(design, layer, field_paths[layer]))
        lines.extend(_fragment_line_meter_lines(design, field_paths["lines"]))
    lines.extend(
        [
            "[MeterWZHKLogo]",
            "Meter=Image",
            f"ImageName=#@#images\\{logo_filename}",
            f"X={logo.x}",
            f"Y={logo.y}",
            f"W={logo.max_width * logo.scale:.3f}",
            "PreserveAspectRatio=1",
            "ImageAlpha=#WZHKLogoAlpha#",
            "DynamicVariables=1",
            "Group=WZHKLogo",
            "",
            "[MeterWZHKArtist]",
            "Meter=String",
            "Text=DJ WaZaHaKa",
            f"X={typography.x}",
            f"Y={typography.y}",
            f"FontFace={typography.artist_font}",
            f"FontSize={typography.artist_size}",
            "FontColor=#WZHKTextColor#",
            "AntiAlias=1",
            "DynamicVariables=1",
            "Group=WZHKDynamic",
            "",
            "[MeterWZHKTitle]",
            "Meter=String",
            "Text=SCATTERED",
            f"X={typography.x}",
            "Y=12R",
            f"FontFace={typography.title_font}",
            f"FontSize={typography.title_size}",
            "FontColor=#WZHKTextColor#",
            "AntiAlias=1",
            "CharacterSpacing=3",
            "DynamicVariables=1",
            "Group=WZHKDynamic",
            "",
        ]
    )
    # Production copies retain only the identity foreground. Audio/timeline
    # measures remain internal; visible diagnostics belong to preview skins.
    if mode == "production":
        return "\n".join(lines)
    lines.extend(
        [
            "[MeterWZHKMeta]",
            "Meter=String",
            f"Text={metadata_text}",
            f"X={typography.x}",
            "Y=22R",
            f"FontFace={typography.title_font}",
            f"FontSize={typography.metadata_size}",
            "FontColor=#WZHKTextColor#",
            "AntiAlias=1",
            "CharacterSpacing=1",
            "DynamicVariables=1",
            "Group=WZHKDynamic",
            "",
            "[MeterWZHKSpectrumBase]",
            "Meter=Image",
            f"X={spectrum.x}",
            f"Y={spectrum.baseline_y + 6}",
            f"W={total_spectrum_width}",
            "H=1",
            "SolidColor=#WZHKAccentColor#",
            "DynamicVariables=1",
            "Group=WZHKDynamic",
            "",
        ]
    )
    for index in range(spectrum.bar_count):
        x = f"(#WZHKSpectrumX#+{index}*(#WZHKBarWidth#+#WZHKBarGap#))"
        lines.extend(
            [
                f"[MeterWZHKGlow{index}]",
                "Meter=Bar",
                f"MeasureName=MeasureWZHKBand{index}",
                f"X=({x}-2)",
                "Y=(#WZHKSpectrumBaselineY#-#WZHKBarHeight#)",
                "W=(#WZHKBarWidth#+4)",
                "H=#WZHKBarHeight#",
                "BarOrientation=Vertical",
                "BarColor=#WZHKGlowColor#",
                "DynamicVariables=1",
                "Group=WZHKDynamic",
                "",
                f"[MeterWZHKBar{index}]",
                "Meter=Bar",
                f"MeasureName=MeasureWZHKBand{index}",
                f"X={x}",
                "Y=(#WZHKSpectrumBaselineY#-#WZHKBarHeight#)",
                "W=#WZHKBarWidth#",
                "H=#WZHKBarHeight#",
                "BarOrientation=Vertical",
                "BarColor=#WZHKBarColor#",
                "DynamicVariables=1",
                "Group=WZHKDynamic",
                "",
            ]
        )

    lines.extend(
        [
            "[MeterWZHKProgress]",
            "Meter=Bar",
            "MeasureName=MeasureWZHKPosition",
            f"X={design.render.safe_margin}",
            f"Y={design.render.height - design.render.safe_margin}",
            f"W={design.render.width - design.render.safe_margin * 2}",
            f"H={design.progress.height}",
            "BarOrientation=Horizontal",
            f"BarColor={_rgb(design.palette.accent)},{round(255 * design.progress.opacity)}",
            "MinValue=0",
            f"MaxValue={timing.master_duration_seconds:.6f}",
            f"Hidden={progress_hidden}",
            "DynamicVariables=1",
            "Group=WZHKDynamic",
        ]
    )
    return "\n".join(lines)


def _materialize_design(
    skin_root: Path,
    design: SpectrumDesignPreset,
    timing: SpectrumMasterTiming,
    logo_filename: str,
    preview_section: str | None,
    mode: str,
) -> None:
    variables_path = skin_root / "@Resources" / "variables.ini"
    variables = variables_path.read_text(encoding="utf-8-sig")
    replacements = {
        "ScaleVisualizer": f"{design.spectrum.scale:.3f}",
        "ScaleSongInformation": f"{design.logo.scale:.3f}",
        "ShowProgressBar": "1" if design.progress.visible else "0",
        "CoverSize": str(design.logo.max_width),
        "BarCount": str(design.spectrum.bar_count),
        "BarWidth": str(design.spectrum.bar_width),
        "BarHeight": str(design.spectrum.max_height),
        "BarGap": str(design.spectrum.bar_gap),
        "Sensitivity": f"{design.spectrum.sensitivity:.3f}",
        "FFTSize": str(design.spectrum.fft_size),
        "FFTAttack": str(design.spectrum.fft_attack),
        "FFTDecay": str(design.spectrum.fft_decay),
        "FontSize1": str(design.typography.artist_size),
        "FontSize2": str(design.typography.title_size),
        "Color": f"{_rgb(design.palette.spectrum)},255",
        "TextColor": _rgb(design.palette.text),
        "EnableDynamicColors": "0",
        "EnableDynamicFontColors": "0",
        "EnableDropShadow": "1",
        "DropShadowColor": f"{_rgb(design.palette.shadow)},90",
        "SkinWidth": str(design.render.width),
    }
    for name, value in replacements.items():
        variables = _replace_variable(variables, name, value)
    _write_text(variables_path, variables)

    scripts_root = skin_root / "@Resources" / "scripts"
    scripts_root.mkdir(parents=True, exist_ok=True)
    _write_text(
        scripts_root / "WZHKSectionController.lua",
        _controller_lua(design, timing),
    )
    runtime_root = skin_root / "@Resources" / "runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    _write_text(runtime_root / "production-clock.txt", "0.000000")
    presentation_root = skin_root / "WZHK Presentation"
    presentation_root.mkdir(parents=True, exist_ok=False)
    _write_text(
        presentation_root / "Scattered.ini",
        _presentation_ini(design, timing, logo_filename, preview_section, mode),
    )


def _deterministic_files(job_root: Path) -> list[Path]:
    files = [
        job_root / "contract.json",
        job_root / "design.json",
        job_root / "PREVIEW-INSTRUCTIONS.md",
    ]
    files.extend(
        path
        for path in iter_tree_files(job_root / "skin")
        if path.relative_to(job_root / "skin").as_posix()
        != "@Resources/runtime/production-clock.txt"
    )
    files.extend(iter_tree_files(job_root / "assets"))
    geometry_root = job_root / "geometry"
    if geometry_root.is_dir():
        files.extend(iter_tree_files(geometry_root))
    return sorted(files, key=lambda path: path.relative_to(job_root).as_posix())


def _hash_deterministic_files(job_root: Path, files: list[Path]) -> str:
    entries = [
        f"{path.relative_to(job_root).as_posix()}\t{sha256_file(path)}"
        for path in files
    ]
    return hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()


def _contract_summary(
    contract: SpectrumRenderRequest,
    timing: SpectrumMasterTiming,
) -> RendererContractSummary:
    return RendererContractSummary(
        artist=contract.project.artist,
        title=contract.project.title,
        bpm=contract.track.bpm,
        meter=(
            f"{contract.track.time_signature.numerator}/"
            f"{contract.track.time_signature.denominator}"
        ),
        total_bars=contract.track.total_bars,
        expected_duration_seconds=contract.track.grid_duration_seconds,
        grid_duration_seconds=contract.track.grid_duration_seconds,
        master_duration_seconds=timing.master_duration_seconds,
        tail_duration_seconds=timing.tail_duration_seconds,
        width=contract.renderer.capture.width,
        height=contract.renderer.capture.height,
        fps=contract.renderer.capture.fps,
    )


def _job_response(manifest: dict[str, Any]) -> SpectrumWorkspaceJob:
    return SpectrumWorkspaceJob.model_validate(
        {
            "schemaVersion": manifest["schemaVersion"],
            "jobId": manifest["jobId"],
            "rendererId": manifest["rendererId"],
            "state": manifest.get("state", "PREPARED"),
            "workspaceRelativePath": manifest["workspaceRelativePath"],
            "contractValid": manifest["contractValid"],
            "brandingApplied": manifest["brandingApplied"],
            "vendorUnchanged": manifest["vendorUnchanged"],
            "generatedWorkspaceHash": manifest["generatedWorkspaceHash"],
            "vendorSourceHash": manifest["vendorSourceHash"],
            "vendorCommit": manifest["vendorCommit"],
            "logoResolved": manifest["logoResolved"],
            "masterAudioResolved": manifest["masterAudioResolved"],
            "warnings": manifest["warnings"],
            "contractSummary": manifest["contractSummary"],
            "mode": manifest.get("mode", "preview"),
            "backgroundMode": manifest.get("backgroundMode", "static-structured"),
            "presetId": manifest.get("presetId"),
            "presetName": manifest.get("presetName"),
            "compositionRevision": manifest.get("compositionRevision"),
            "previewSection": manifest.get("previewSection"),
            "generativePreviewOverride": manifest.get("generativePreviewOverride"),
            "designHash": manifest.get("designHash"),
            "timingSource": manifest.get("timingSource"),
            "timingAccuracy": manifest.get("timingAccuracy"),
            "timelineControllerVersion": manifest.get("timelineControllerVersion"),
            "visualQaRequired": manifest.get("visualQaRequired", True),
            "masterTiming": manifest.get("masterTiming"),
            "productionAvailability": manifest.get("productionAvailability"),
            "capturePreflight": manifest.get("capturePreflight"),
            "artifacts": manifest.get("artifacts", []),
            "synchronization": manifest.get("synchronization"),
            "validationReport": manifest.get("validationReport"),
            "captureProvider": manifest.get("captureProvider"),
            "encoder": manifest.get("encoder"),
            "capturedFrames": manifest.get("capturedFrames"),
            "droppedFrames": manifest.get("droppedFrames"),
            "captureDurationSeconds": manifest.get("captureDurationSeconds"),
            "errorMessage": manifest.get("errorMessage"),
            "geometryCapability": manifest.get("geometryCapability"),
            "geometryTelemetry": manifest.get("geometryTelemetry"),
        }
    )


def prepare_workspace(
    paths: SpectrumPaths,
    outcome: SpectrumPreflightOutcome,
    request: SpectrumWorkspacePrepareRequest | None = None,
) -> SpectrumWorkspaceJob:
    if not outcome.descriptor.preparation_available:
        raise SpectrumWorkspaceError("WZHK Spectrum workspace prerequisites are not ready.")
    if (
        outcome.contract is None
        or outcome.design_preset is None
        or outcome.logo_path is None
        or outcome.master_audio_path is None
        or outcome.vendor_source_hash is None
        or outcome.provenance is None
        or outcome.master_timing is None
    ):
        raise SpectrumWorkspaceError("WZHK Spectrum preflight did not resolve required inputs.")

    contract = outcome.contract
    timing = outcome.master_timing
    prepare_request = request or SpectrumWorkspacePrepareRequest()
    design = resolve_design_preset(
        outcome.design_preset,
        prepare_request.visual_overrides,
        prepare_request.background_mode,
    )
    job_id = str(uuid4())
    jobs_root = ensure_within(paths.data_root, paths.jobs_root)
    job_root = ensure_within(jobs_root, jobs_root / job_id)
    staging_root = ensure_within(jobs_root, jobs_root / f".{job_id}.staging")
    if job_root.exists() or staging_root.exists():
        raise SpectrumWorkspaceError("The generated Spectrum job identity already exists.")

    vendor_hash_before = hash_tree(
        paths.vendor_root,
        prefix="vendor/wzhk-spectrum-visualizer",
    )
    if vendor_hash_before != outcome.vendor_source_hash:
        raise SpectrumWorkspaceError("The vendor snapshot changed after preflight.")

    try:
        staging_root.mkdir(parents=False, exist_ok=False)
        secure_private_directory(staging_root)
        skin_root = staging_root / "skin"
        assets_root = staging_root / "assets"
        for directory in (
            assets_root,
            staging_root / "logs",
            staging_root / "capture",
            staging_root / "output",
        ):
            directory.mkdir()
            secure_private_directory(directory)

        shutil.copytree(paths.vendor_root, skin_root, copy_function=shutil.copyfile)
        secure_private_directory(skin_root)

        logo_filename = f"wzhk-logo{outcome.logo_path.suffix.lower()}"
        master_filename = f"scattered-master{outcome.master_audio_path.suffix.lower()}"
        _copy_asset(outcome.logo_path, assets_root / logo_filename)
        _copy_asset(outcome.master_audio_path, assets_root / master_filename)

        skin_logo = skin_root / "@Resources" / "images" / logo_filename
        skin_logo.parent.mkdir(parents=True, exist_ok=True)
        _copy_asset(outcome.logo_path, skin_logo)
        _replace_visible_branding(skin_root, logo_filename)
        _materialize_design(
            skin_root,
            design,
            timing,
            logo_filename,
            prepare_request.preview_section,
            prepare_request.mode,
        )
        if (
            design.background.mode == "generative-geometry"
            and design.generative_geometry.enabled
        ):
            materialize_geometry_runtime(
                repository_root=paths.repository_root,
                job_root=staging_root,
                mode=prepare_request.mode,
                design=design,
                timing=timing,
                preview_override=prepare_request.generative_preview,
            )

        contract_payload = contract.model_dump(mode="json", by_alias=True)
        _write_json(staging_root / "contract.json", contract_payload)

        timing_source = (
            design.controller.production_timing_source
            if prepare_request.mode == "production"
            else design.controller.preview_timing_source
        )
        timing_accuracy = (
            design.controller.production_accuracy
            if prepare_request.mode == "production"
            else design.controller.preview_accuracy
        )
        design_payload: dict[str, Any] = {
            "schemaVersion": "3.1.0",
            "presetId": design.preset_id,
            "displayName": design.display_name,
            "compositionRevision": design.composition.revision,
            "canonicalTimeline": [
                {
                    "id": section.id,
                    "label": section.label,
                    "startSeconds": section.start_seconds,
                    "endSeconds": section.end_seconds,
                }
                for section in design.sections
            ],
            "postGridTail": {
                "id": design.post_grid_tail.id,
                "label": design.post_grid_tail.label,
                "startSeconds": design.post_grid_tail.start_seconds,
                "endSeconds": timing.master_duration_seconds,
            },
            "timing": timing.model_dump(mode="json", by_alias=True),
            "mode": prepare_request.mode,
            "previewOverride": prepare_request.preview_section,
            "generativePreviewOverride": (
                prepare_request.generative_preview.model_dump(
                    mode="json",
                    by_alias=True,
                )
                if prepare_request.generative_preview is not None
                else None
            ),
            "backgroundMode": design.background.mode,
            "visualOverrides": prepare_request.visual_overrides.model_dump(
                mode="json",
                by_alias=True,
                exclude_none=True,
            ),
            "resolvedPreset": design.model_dump(mode="json", by_alias=True),
            "materialization": {
                "presentationSkin": "skin/WZHK Presentation/Scattered.ini",
                "timelineController": "skin/@Resources/scripts/WZHKSectionController.lua",
                "geometryRuntime": (
                    "geometry/index.html"
                    if design.background.mode == "generative-geometry"
                    else None
                ),
                "geometryRuntimeConfig": (
                    "geometry/config/runtime-config.json"
                    if design.background.mode == "generative-geometry"
                    else None
                ),
                "timingSource": timing_source,
                "timingAccuracy": timing_accuracy,
                "captureAutomated": prepare_request.mode == "production",
            },
        }
        _write_json(staging_root / "design.json", design_payload)
        design_hash = sha256_file(staging_root / "design.json")
        preview_label = (
            "production canonical timeline"
            if prepare_request.mode == "production"
            else prepare_request.preview_section or "canonical full timeline"
        )
        background_instruction = (
            "Use the job-owned loopback browser preview for the complete geometry and "
            "identity composition. Production uses that same single-browser compositor; "
            "do not layer a Rainmeter window over it. Only the logo, artist and title "
            "are drawn over the geometry. Diagnostics are explicit preview-only options. "
            "The browser runtime never uses an external URL."
            if design.background.mode == "generative-geometry"
            else "For the static fallback, install Rainmeter separately, copy this job's "
            "skin directory to a private Rainmeter skin location, and activate "
            "WZHK Presentation\\Scattered.ini. Preview timing follows the player "
            "configured by PlayerName; production uses the owned master clock. "
            "Production copies omit bar, baseline, metadata and progress meters."
        )
        _write_text(
            staging_root / "PREVIEW-INSTRUCTIONS.md",
            f"""# WZHK Spectrum preview

Prepared view: {preview_label}
Background mode: {design.background.mode}

{background_instruction}

Confirm logo/title readability, continuous geometry across the canvas, localized soft dimming, musical response and section transitions. Final production must contain no spectrum bars, ribbon, BPM/meter text or section labels.

The 192-second musical grid is followed by an intentional post-grid tail through the resolved {timing.master_duration_seconds:.3f}-second master EOF. This preparation action does not launch a browser or Rainmeter, record the screen, create an MP4, or upload anything. Real production capture remains operator-controlled; automated validation is not aesthetic approval.
""",
        )

        deterministic_files = _deterministic_files(staging_root)
        generated_hash = _hash_deterministic_files(staging_root, deterministic_files)
        generated_file_names = [
            path.relative_to(staging_root).as_posix() for path in deterministic_files
        ]
        generation_payload = {
            "schemaVersion": "4.0.0",
            "rendererId": "wzhk-spectrum",
            "sourceContract": {
                "logicalPath": "tools/wzhk-spectrum/config/scattered.wzhk-spectrum.json",
                "sha256": sha256_file(paths.contract_path),
            },
            "sourceDesignPreset": {
                "logicalPath": "tools/wzhk-spectrum/config/scattered.visual-preset.json",
                "sha256": sha256_file(paths.design_preset_path),
            },
            "designHash": design_hash,
            "previewSection": prepare_request.preview_section,
            "generativePreviewOverride": design_payload["generativePreviewOverride"],
            "backgroundMode": design.background.mode,
            "compositionRevision": design.composition.revision,
            "mode": prepare_request.mode,
            "masterTiming": timing.model_dump(mode="json", by_alias=True),
            "visualOverrides": design_payload["visualOverrides"],
            "track": contract.project.model_dump(mode="json", by_alias=True),
            "sections": [
                section.model_dump(mode="json", by_alias=True)
                for section in contract.sections
            ],
            "branding": {
                "brand": contract.branding.brand,
                "artist": contract.project.artist,
                "title": contract.project.title.upper(),
                "logo": f"assets/{logo_filename}",
            },
            "capture": contract.renderer.capture.model_dump(mode="json", by_alias=True),
            "masterAudio": f"assets/{master_filename}",
            "vendorRepository": outcome.provenance["repository"],
            "vendorCommit": EXPECTED_VENDOR_COMMIT,
            "vendorSourceHash": vendor_hash_before,
            "generatedWorkspaceHash": generated_hash,
            "filesGenerated": generated_file_names,
        }
        _write_json(staging_root / "generation.json", generation_payload)

        vendor_hash_after = hash_tree(
            paths.vendor_root,
            prefix="vendor/wzhk-spectrum-visualizer",
        )
        vendor_unchanged = vendor_hash_after == vendor_hash_before
        if not vendor_unchanged:
            raise SpectrumWorkspaceError("The vendor snapshot changed during workspace preparation.")

        manifest = {
            "schemaVersion": "4.0.0",
            "jobId": job_id,
            "rendererId": "wzhk-spectrum",
            "state": (
                SpectrumProductionState.WORKSPACE_READY.value
                if prepare_request.mode == "production"
                else SpectrumProductionState.PREVIEW_READY.value
            ),
            "createdAt": datetime.now(UTC).isoformat(),
            "workspaceRelativePath": f"wzhk-spectrum/jobs/{job_id}",
            "sourceContract": generation_payload["sourceContract"],
            "sourceDesignPreset": generation_payload["sourceDesignPreset"],
            "track": contract.track.model_dump(mode="json", by_alias=True),
            "sections": generation_payload["sections"],
            "branding": generation_payload["branding"],
            "resolvedLogo": f"assets/{logo_filename}",
            "resolvedMasterAudio": f"assets/{master_filename}",
            "vendorRepository": outcome.provenance["repository"],
            "vendorCommit": EXPECTED_VENDOR_COMMIT,
            "vendorSourceHash": vendor_hash_before,
            "generatedWorkspaceHash": generated_hash,
            "presetId": design.preset_id,
            "presetName": design.display_name,
            "compositionRevision": design.composition.revision,
            "mode": prepare_request.mode,
            "previewSection": prepare_request.preview_section,
            "generativePreviewOverride": design_payload["generativePreviewOverride"],
            "backgroundMode": design.background.mode,
            "visualOverrides": design_payload["visualOverrides"],
            "designHash": design_hash,
            "timingSource": timing_source,
            "timingAccuracy": timing_accuracy,
            "timelineControllerVersion": design.controller.version,
            "warnings": outcome.descriptor.warnings,
            "toolVersions": {
                "python": sys.version.split()[0],
                "ffmpeg": outcome.ffmpeg_version,
                "workspaceBuilder": "wzhk-spectrum-milestone-3.7",
            },
            "filesGenerated": generated_file_names,
            "contractValid": True,
            "brandingApplied": True,
            "vendorUnchanged": vendor_unchanged,
            "logoResolved": True,
            "masterAudioResolved": True,
            "measuredMasterDurationSeconds": outcome.measured_audio_duration_seconds,
            "contractSummary": _contract_summary(contract, timing).model_dump(
                mode="json", by_alias=True
            ),
            "masterTiming": timing.model_dump(mode="json", by_alias=True),
            "productionAvailability": outcome.descriptor.capture_availability,
            "capturePreflight": None,
            "artifacts": [],
            "synchronization": None,
            "validationReport": None,
            "captureProvider": None,
            "encoder": None,
            "capturedFrames": None,
            "droppedFrames": None,
            "captureDurationSeconds": None,
            "errorMessage": None,
            "geometryCapability": None,
            "geometryTelemetry": None,
            "captureAutomated": prepare_request.mode == "production",
            "visualQaRequired": True,
        }
        _write_json(staging_root / "manifest.json", manifest)
        _publish_directory(staging_root, job_root)
        return _job_response(manifest)
    except Exception:
        if staging_root.exists():
            shutil.rmtree(staging_root)
        raise


def load_workspace_job(paths: SpectrumPaths, job_id: str) -> SpectrumWorkspaceJob:
    try:
        parsed = UUID(job_id)
    except ValueError as exc:
        raise SpectrumWorkspaceError("The Spectrum job identity is invalid.") from exc
    if parsed.version != 4 or str(parsed) != job_id:
        raise SpectrumWorkspaceError("The Spectrum job identity is invalid.")
    jobs_root = ensure_within(paths.data_root, paths.jobs_root)
    job_root = ensure_within(jobs_root, jobs_root / job_id)
    manifest_path = job_root / "manifest.json"
    if not manifest_path.is_file():
        raise SpectrumWorkspaceError("The Spectrum workspace job was not found.")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SpectrumWorkspaceError("The persisted Spectrum workspace is invalid.") from exc
    if not isinstance(payload, dict) or payload.get("jobId") != job_id:
        raise SpectrumWorkspaceError("The persisted Spectrum workspace identity is invalid.")
    return _job_response(payload)
