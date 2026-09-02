from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

import pytest

from app.renderers.wzhk_spectrum.design import load_design_preset
from app.renderers.wzhk_spectrum.generative.composition import (
    COMPOSITION_MASTER_DURATION_SECONDS,
    resolve_composition_envelope,
)
from app.renderers.wzhk_spectrum.generative.workspace import build_runtime_config
from app.renderers.wzhk_spectrum.production import resolve_master_timing
from app.subprocess_utils import run_process_bounded

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = REPOSITORY_ROOT / "tools" / "wzhk-spectrum" / "runtime"
PRESET_PATH = REPOSITORY_ROOT / "tools" / "wzhk-spectrum" / "config" / "scattered.visual-preset.json"

# Execute the actual browser module in a deliberately non-networked VM. The
# final startup invocation is stripped; no browser, approved master, or private
# asset is opened. Canvas calls are recorded rather than interpreted as pixels.
NODE_HARNESS = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const { performance } = require('node:perf_hooks');
const source = fs.readFileSync(process.argv[1], 'utf8');
const startup = /void main\(\)\.catch\(\(error\) => \{ void showFatal\(error\) \}\)\s*$/;
assert.ok(startup.test(source), 'The bounded VM must remove the runtime startup invocation');
const payload = JSON.parse(process.argv[2]);
const elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, {
    hidden: true, value: '', textContent: '',
    classList: { toggle() {} },
    addEventListener() {}, append() {},
  });
  return elements.get(id);
}
const events = [];
const context = {
  window: {
    addEventListener() {},
    location: { origin: 'http://127.0.0.1:8765', hostname: '127.0.0.1', protocol: 'http:', search: '' },
  },
  document: {
    body: { dataset: {}, style: {} },
    querySelector: element,
    createElement() { return {}; },
    fonts: { ready: Promise.resolve() },
  },
  fetch: async (_path, options) => { events.push(JSON.parse(options.body)); return { ok: true }; },
  Image: class { addEventListener() {} },
  URL, URLSearchParams, performance,
};
vm.createContext(context);
vm.runInContext(source.replace(startup, '') + `
globalThis.audit = {
  normalizeConfig, normalizeComposition, resolveCompositionEnvelope,
  IdentityOverlay, GeometryRenderer, ShapeLaboratory,
  configurePresentation, setStatus, showFatal,
  activate(config) { activeConfig = config; },
};`, context);
const runtime = context.audit;
const config = runtime.normalizeConfig(payload.config);
function drawingHarness(selectedConfig) {
  const calls = { logos: 0, text: [], rectangles: 0, cleared: 0, spectrumReads: 0 };
  const drawing = {
    clearRect() { calls.cleared += 1; }, save() {}, restore() {},
    drawImage() { calls.logos += 1; },
    fillText(text) { calls.text.push(text); },
    fillRect() { calls.rectangles += 1; },
    measureText() { return { width: 20 }; },
    createLinearGradient() { return { addColorStop() {} }; },
  };
  const canvas = { clientWidth: 1920, clientHeight: 1080, getContext() { return drawing; } };
  const overlay = new runtime.IdentityOverlay(canvas, selectedConfig);
  overlay.logoReady = true;
  const audio = { spectrumBands(count) { calls.spectrumReads += 1; return Array(count).fill(0.6); } };
  return { calls, overlay, audio };
}
"""


def _runtime_config() -> dict[str, Any]:
    return build_runtime_config(
        mode="production",
        design=load_design_preset(PRESET_PATH),
        timing=resolve_master_timing(COMPOSITION_MASTER_DURATION_SECONDS),
    )


def _node(body: str, payload: dict[str, Any] | None = None) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for actual runtime-module VM regression checks")
    supplied = {"config": _runtime_config(), **(payload or {})}
    script = NODE_HARNESS + "\n(async () => {\n" + body + "\n})().catch(error => { console.error(error); process.exitCode = 1; });\n"
    result = run_process_bounded(
        [node, "-e", script, str(RUNTIME_ROOT / "runtime.js"), json.dumps(supplied)],
        timeout_seconds=10,
        stdout_limit=64 * 1024,
        stderr_limit=64 * 1024,
    )
    assert not result.stdout_exceeded and not result.stderr_exceeded
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")


def test_runtime_production_draws_only_identity_and_never_reads_spectrum() -> None:
    config = _runtime_config()
    config["branding"]["meta"] = "120 BPM / 4/4 / 96 BARS / MAIN"
    config["developerLab"].update(enabled=True, spectrumDiagnostics=True)
    _node(
        """
assert.equal(config.developerLab.enabled, false);
assert.equal(config.developerLab.spectrumDiagnostics, false);
assert.equal(Object.hasOwn(config.branding, 'meta'), false);
assert.equal(Object.hasOwn(config.branding, 'safeRect'), false);
for (const selectedConfig of [config, {
  ...config, developerLab: { ...config.developerLab, spectrumDiagnostics: true },
}]) {
  const { calls, overlay, audio } = drawingHarness(selectedConfig);
  overlay.draw(audio, { energy: 0.6 }, 120, 'main');
  // A direct diagnostic call must also fail closed in production.
  overlay.drawSpectrumDiagnostics(audio, { energy: 0.6 }, 120, 'main');
  assert.equal(calls.spectrumReads, 0);
  assert.equal(calls.rectangles, 0);
  assert.equal(calls.logos, 1);
  assert.equal(calls.text.join(''), 'DJ WaZaHaKaSCATTERED');
  assert.equal(calls.cleared, 1);
}
""",
        {"config": config},
    )


def test_runtime_diagnostics_require_explicit_preview_opt_in_and_preserve_morph_override() -> None:
    _node(
        """
const preview = structuredClone(payload.config);
preview.mode = 'preview';
preview.developerLab = { enabled: true, spectrumDiagnostics: false, previewOverride: {
  mode: 'morph', shapeA: { shapeId: 'torus' }, shapeB: { shapeId: 'trefoil-knot' },
  morphProgress: 0.4, audioMode: 'disabled',
} };
const normalized = runtime.normalizeConfig(preview);
const lab = new runtime.ShapeLaboratory(normalized);
assert.equal(lab.state.sourceShape, 'torus');
assert.equal(lab.state.targetShape, 'trefoil-knot');
assert.equal(lab.state.morph, 0.4);
assert.equal(lab.state.audioMode, 'disabled');
const clean = drawingHarness(normalized);
clean.overlay.draw(clean.audio, { energy: 0.5 }, 120, 'main');
assert.equal(clean.calls.rectangles, 0);
assert.equal(clean.calls.spectrumReads, 0);
preview.developerLab.spectrumDiagnostics = true;
const diagnostic = drawingHarness(runtime.normalizeConfig(preview));
diagnostic.overlay.draw(diagnostic.audio, { energy: 0.5 }, 120, 'main');
assert.equal(diagnostic.calls.rectangles, 57);
assert.equal(diagnostic.calls.spectrumReads, 1);
assert.equal(diagnostic.calls.text.join(''), 'DJ WaZaHaKaSCATTERED');
preview.mode = 'production';
assert.throws(() => runtime.normalizeConfig(preview));
"""
    )


def test_runtime_rejects_invalid_or_incomplete_composition_and_missing_identity() -> None:
    _node(
        """
const invalid = [
  v => { v.production.spectrumBarsVisible = true; },
  v => { v.production.spectralRibbonVisible = true; },
  v => { v.production.technicalMetadataVisible = true; },
  v => { v.production.sectionLabelsVisible = true; },
  v => { v.production.logoVisible = false; },
  v => { v.production.artistVisible = 'true'; },
  v => { v.production.titleVisible = 1; },
  v => { v.readability.minimumBrightness = 0; },
  v => { v.readability.haloSuppression = 1.01; },
  v => { v.readability.zones[0].radius[0] = 0; },
  v => { v.readability.zones[0].radius[1] = 0.251; },
  v => { v.readability.zones[0].strength = 0.751; },
  v => { v.readability.zones[0].center[0] = NaN; },
  v => { v.readability.zones[1].id = v.readability.zones[0].id; },
  v => { v.readability.zones.push(structuredClone(v.readability.zones[0])); },
  v => { v.framing.center[1] = 2; },
  v => { v.framing.shapeScale = Infinity; },
  v => { v.geometryCoverage = 'right-panel'; },
  v => { v.envelope[0].timeSeconds = 0.1; },
  v => { v.envelope[1].timeSeconds = 0; },
  v => { v.envelope.at(-1).brightness = 0.1; },
  v => { v.envelope.at(-1).density = 0.1; },
  v => { v.envelope.at(-1).timeSeconds -= 0.1; },
  v => { delete v.envelope[0].deformation; },
  v => { v.unrecognized = true; },
];
for (const mutate of invalid) {
  const value = structuredClone(payload.config.composition);
  mutate(value);
  assert.throws(() => runtime.normalizeComposition(value, config.masterDurationSeconds));
}
for (const mutate of [
  v => { v.branding.enabled = false; },
  v => { v.logoUrl = null; },
  v => { v.developerLab.spectrumDiagnostics = 'false'; },
  v => { v.audioMapping.propagationWidth = 0; },
]) {
  const value = structuredClone(payload.config);
  mutate(value);
  assert.throws(() => runtime.normalizeConfig(value));
}
"""
    )


def test_runtime_envelope_matches_backend_at_boundaries_and_clamped_eof() -> None:
    design = load_design_preset(PRESET_PATH)
    assert design.composition is not None
    times = [-2, 0, 10, 63, 64 - 0.000001, 64, 64 + 0.000001, 65, 120, 175, 176, 177, 191, 192, 193, 196.619796, 220]
    expected = [
        {
            "seconds": seconds,
            "value": resolve_composition_envelope(design.composition, seconds).model_dump(
                mode="json", by_alias=True, exclude={"time_seconds"}
            ),
        }
        for seconds in times
    ]
    _node(
        """
for (const sample of payload.expected) {
  const first = runtime.resolveCompositionEnvelope(config.composition, sample.seconds);
  const repeated = runtime.resolveCompositionEnvelope(config.composition, sample.seconds);
  assert.equal(JSON.stringify(first), JSON.stringify(repeated));
  for (const [key, value] of Object.entries(sample.value)) {
    assert.ok(Number.isFinite(first[key]));
    assert.ok(Math.abs(first[key] - value) < 1e-12, `${sample.seconds}:${key}`);
  }
}
for (const seconds of [64, 176, 192]) {
  const left = runtime.resolveCompositionEnvelope(config.composition, seconds - 1e-6);
  const right = runtime.resolveCompositionEnvelope(config.composition, seconds + 1e-6);
  for (const key of Object.keys(left)) assert.ok(Math.abs(left[key] - right[key]) < 1e-5);
}
assert.throws(() => runtime.resolveCompositionEnvelope(config.composition, NaN));
assert.throws(() => runtime.resolveCompositionEnvelope(config.composition, Infinity));
""",
        {"expected": expected},
    )


def test_runtime_geometry_uniforms_bind_masks_envelope_propagation_and_full_point_domain() -> None:
    _node(
        """
const uniforms = {};
const recorded = {};
const noop = () => {};
const gl = {
  POINTS: 0, NO_ERROR: 0, COLOR_BUFFER_BIT: 16384,
  useProgram: noop, bindVertexArray: noop, clearColor: noop, clear: noop,
  uniform1f(name, value) { recorded[name] = value; },
  uniform1i(name, value) { recorded[name] = value; },
  uniform2i(name, ...values) { recorded[name] = values; },
  uniform2f(name, ...values) { recorded[name] = values; },
  uniform3f(name, ...values) { recorded[name] = values; },
  uniform4f(name, ...values) { recorded[name] = values; },
  uniform2fv(name, values) { recorded[name] = Array.from(values); },
  uniform3fv(name, values) { recorded[name] = Array.from(values); },
  uniformMatrix4fv: noop,
  drawArrays(...values) { recorded.draw = values; },
  getError() { return 0; },
};
for (const match of source.matchAll(/'(u[A-Z][A-Za-z0-9]*)'/g)) uniforms[match[1]] = match[1];
const renderer = Object.create(runtime.GeometryRenderer.prototype);
Object.assign(renderer, {
  config, gl, uniforms, program: {}, vertexArray: {}, resize: noop,
  canvas: { width: 1920, height: 1080 },
  background: [0, 0, 0], primary: [0.5, 0.8, 1], secondary: [0.6, 0.5, 1], seedPhase: 0.4,
});
renderer.draw({ seconds: 120, sourceShape: 'torus', targetShape: 'trefoil-knot', morph: 0.4,
  audio: { low: 0.4, mid: 0.5, high: 0.2, energy: 0.3 },
  propagation: { strength: 0.5, origin: [0.2, 0.6], age: 0.2 },
});
assert.equal(JSON.stringify(recorded.uFrameCenter), JSON.stringify(config.composition.framing.center));
assert.equal(recorded.uShapeScale, config.composition.framing.shapeScale);
assert.equal(recorded.uDepthStrength, config.composition.framing.depthStrength);
assert.equal(recorded.uReadabilityMinimum, config.composition.readability.minimumBrightness);
assert.equal(recorded.uHaloSuppression, config.composition.readability.haloSuppression);
for (const [index, zone] of config.composition.readability.zones.entries()) {
  assert.equal(JSON.stringify(recorded[`uReadabilityZone${index}`]), JSON.stringify([...zone.center, ...zone.radius]));
}
assert.equal(JSON.stringify(recorded.uPropagation), JSON.stringify([
  config.audioMapping.propagationSpeed, config.audioMapping.propagationDecay, config.audioMapping.propagationWidth,
]));
const envelope = runtime.resolveCompositionEnvelope(config.composition, 120);
assert.equal(JSON.stringify(recorded.uEnvelope), JSON.stringify([
  envelope.density, envelope.brightness, envelope.scale, envelope.deformation,
]));
assert.equal(JSON.stringify(recorded.draw), JSON.stringify([gl.POINTS, 0, config.pointCount]));
assert.equal(Object.hasOwn(recorded, 'uBrandSafeRect'), false);
"""
    )


def test_gpu_superformula_to_sphere_midpoint_retains_macro_extent() -> None:
    # Evaluate the scalar expressions from the three actual GLSL samplers in
    # Node. This is a correspondence regression, not a claim of GPU execution;
    # shader compilation and pixels remain part of real browser qualification.
    _node(
        r"""
const path = require('node:path');
const shader = fs.readFileSync(path.join(path.dirname(process.argv[1]), 'shaders', 'neopixel.vert.glsl'), 'utf8');
const helpers = {
  PI: Math.PI, TAU: Math.PI * 2,
  sin: Math.sin, cos: Math.cos, acos: Math.acos,
  abs: Math.abs, pow: Math.pow, max: Math.max,
  clamp: (value, low, high) => Math.min(high, Math.max(low, value)),
  vec3: (x, y, z) => [x, y, z],
};
function compileSampler(name, argument) {
  const match = shader.match(new RegExp(`(?:float|vec3) ${name}\\([^)]*\\) \\{([\\s\\S]*?)\\n\\}`));
  assert.ok(match, `Expected a bounded built-in GLSL sampler: ${name}`);
  const scalarBody = match[1].replace(/\bfloat\s+/g, 'const ');
  return new Function(...Object.keys(helpers), argument, scalarBody).bind(null, ...Object.values(helpers));
}
helpers.superformulaRadius = compileSampler('superformulaRadius', 'angle');
const superformula = compileSampler('superformulaShell', 'uv');
const sphere = compileSampler('sphericalLattice', 'uv');
function presentationScale(name) {
  const match = shader.match(new RegExp(`return ${name}\\(uv\\) \\* ([0-9.]+);`));
  assert.ok(match, `Expected the presentation-only extent normalization for ${name}`);
  return Number(match[1]);
}
const sourceScale = presentationScale('superformulaShell');
const targetScale = presentationScale('sphericalLattice');
const sourcePoints = [];
const targetPoints = [];
const midpointPoints = [];
for (let row = 0; row < 64; row += 1) {
  for (let column = 0; column < 64; column += 1) {
    const uv = { x: (column + 0.5) / 64, y: (row + 0.5) / 64 };
    const first = superformula(uv).map(value => value * sourceScale);
    const last = sphere(uv).map(value => value * targetScale);
    sourcePoints.push(first);
    targetPoints.push(last);
    midpointPoints.push(first.map((value, axis) => (value + last[axis]) * 0.5));
  }
}
function rmsRadius(points) {
  return Math.sqrt(points.reduce((sum, point) => sum + point.reduce((norm, value) => norm + value * value, 0), 0) / points.length);
}
function extent(points, axis) {
  return Math.max(...points.map(point => point[axis])) - Math.min(...points.map(point => point[axis]));
}
const sourceRms = rmsRadius(sourcePoints);
const targetRms = rmsRadius(targetPoints);
const midpointRms = rmsRadius(midpointPoints);
assert.ok(midpointRms >= Math.min(sourceRms, targetRms) * 0.85,
  JSON.stringify({ sourceRms, targetRms, midpointRms }));
for (let axis = 0; axis < 3; axis += 1) {
  assert.ok(extent(midpointPoints, axis) >= Math.min(extent(sourcePoints, axis), extent(targetPoints, axis)) * 0.85,
    `Shared point identity collapsed the midpoint extent on axis ${axis}`);
}
for (let index = 0; index < sourcePoints.length; index += 1) {
  const directionDot = sourcePoints[index].reduce((sum, value, axis) => sum + value * targetPoints[index][axis], 0);
  assert.ok(directionDot > 0, 'Corresponding nodes must share a hemisphere instead of cancelling through the origin');
}
"""
    )


def test_identity_canvas_blends_the_untouched_logo_matte_at_the_compositor_boundary() -> None:
    css = (RUNTIME_ROOT / "runtime.css").read_text(encoding="utf-8")
    source = (RUNTIME_ROOT / "runtime.js").read_text(encoding="utf-8")
    identity_rule = re.search(r"#identity-canvas\s*\{([^}]+)\}", css)
    assert identity_rule is not None
    assert "mix-blend-mode: screen;" in identity_rule.group(1)
    assert "isolation: isolate;" in css
    # Screen preserves the backdrop under black source pixels and white
    # identity pixels remain white. Asset bytes are not edited or keyed out.
    for background in (0.0, 0.03, 0.5, 1.0):
        assert 1 - (1 - 0.0) * (1 - background) == pytest.approx(background)
        assert 1 - (1 - 1.0) * (1 - background) == 1.0
    assert "this.logo.src = config.logoUrl" in source
    assert "getImageData" not in source
    assert "putImageData" not in source


def test_runtime_production_hides_startup_status_and_fatal_labels_but_preserves_error_events() -> None:
    html = (RUNTIME_ROOT / "index.html").read_text(encoding="utf-8")
    css = (RUNTIME_ROOT / "runtime.css").read_text(encoding="utf-8")
    assert '<body data-runtime-mode="production">' in html
    assert 'id="runtime-status" class="runtime-status" role="status" aria-live="polite" hidden' in html
    assert 'body:not([data-runtime-mode="preview"]) .runtime-status' in css
    assert 'body:not([data-runtime-mode="preview"]) .fatal-error' in css
    assert '[hidden] {\n  display: none !important;' in css
    assert 'id="identity-canvas"' in html
    assert 'id="spectrum-canvas"' not in html
    assert 'id="brand-meta"' not in html
    _node(
        """
runtime.activate(config);
runtime.configurePresentation(config);
runtime.setStatus('MAIN / 96 BARS / reconnecting');
assert.equal(element('#runtime-status').hidden, true);
assert.equal(context.document.body.dataset.runtimeMode, 'production');
await runtime.showFatal(new Error('synthetic fixture error'));
assert.equal(element('#fatal-error').hidden, true);
assert.equal(element('#runtime-status').hidden, true);
assert.equal(events.length, 1);
assert.equal(events[0].type, 'error');
assert.equal(events[0].runtimeVersion, '1.0.0');
assert.equal(events[0].payload.code, 'RUNTIME_ERROR');
const preview = runtime.normalizeConfig({ ...payload.config, mode: 'preview' });
runtime.activate(preview);
runtime.configurePresentation(preview);
runtime.setStatus('Developer preview');
assert.equal(element('#runtime-status').hidden, false);
"""
    )
