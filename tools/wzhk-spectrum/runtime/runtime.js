// The authenticated event protocol remains 1.0.0; composition.revision
// identifies the geometry-first visual change independently.
const RUNTIME_VERSION = '1.0.0'
const CONFIG_URL = '/config/runtime-config.json'
const CONTROL_URL = '/api/control'
const EVENT_URL = '/api/event'

const SHAPES = Object.freeze({
  matrix: 0,
  'wave-surface': 1,
  cylinder: 2,
  torus: 3,
  'twisted-torus': 4,
  helix: 5,
  'double-helix': 6,
  lissajous: 7,
  rose: 8,
  hypotrochoid: 9,
  'torus-knot': 10,
  'spherical-lattice': 11,
  'mobius-strip': 12,
  'trefoil-knot': 13,
  superformula: 14,
  'sparse-field': 15,
  'matrix-field': 16,
  'dispersed-field': 17,
})

const SHAPE_LABELS = Object.freeze({
  matrix: 'Matrix plane',
  'matrix-field': 'Matrix field',
  'wave-surface': 'Wave surface',
  cylinder: 'Cylinder',
  torus: 'Torus',
  'twisted-torus': 'Twisted torus',
  helix: 'Helix',
  'double-helix': 'Double helix',
  lissajous: 'Lissajous curve',
  rose: 'Rose curve',
  hypotrochoid: 'Hypotrochoid',
  'torus-knot': 'Torus knot',
  'spherical-lattice': 'Spherical lattice',
  'mobius-strip': 'Möbius strip',
  'trefoil-knot': 'Trefoil knot',
  superformula: 'Superformula shell',
  'sparse-field': 'Sparse field',
  'dispersed-field': 'Dispersed field',
})

const EASINGS = Object.freeze({
  linear: (value) => value,
  smoothstep: (value) => value * value * (3 - 2 * value),
  smootherstep: (value) => value * value * value * (value * (value * 6 - 15) + 10),
  'cubic-in-out': (value) => value < 0.5
    ? 4 * value * value * value
    : 1 - Math.pow(-2 * value + 2, 3) / 2,
  cubic: (value) => value < 0.5
    ? 4 * value * value * value
    : 1 - Math.pow(-2 * value + 2, 3) / 2,
  'sine-in-out': (value) => -(Math.cos(Math.PI * value) - 1) / 2,
  sinusoidal: (value) => -(Math.cos(Math.PI * value) - 1) / 2,
})

const CONTROL_STATES = new Set(['idle', 'playing', 'paused', 'ended'])
const SECTIONS = new Set(['intro', 'main', 'outro', 'post-grid-tail'])

class RuntimeFailure extends Error {
  constructor(code, message, detail = null) {
    super(message)
    this.name = 'RuntimeFailure'
    this.code = code
    this.detail = detail
  }
}

function isRecord(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function clamp(value, minimum = 0, maximum = 1) {
  return Math.min(maximum, Math.max(minimum, value))
}

function requiredRecord(value, label) {
  if (!isRecord(value)) {
    throw new RuntimeFailure('CONFIG_INVALID', `${label} must be an object.`)
  }
  return value
}

function requiredString(value, label, maximumLength = 160) {
  if (typeof value !== 'string' || value.length === 0 || value.length > maximumLength) {
    throw new RuntimeFailure('CONFIG_INVALID', `${label} must be a bounded non-empty string.`)
  }
  return value
}

function canonicalUuid(value, label, errorCode = 'CONFIG_INVALID') {
  const resolved = requiredString(value, label, 36)
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(resolved)) {
    throw new RuntimeFailure(errorCode, `${label} must be a canonical UUIDv4.`)
  }
  return resolved
}

function optionalText(value, label, fallback = '', maximumLength = 160) {
  if (value === undefined || value === null) return fallback
  return requiredString(value, label, maximumLength)
}

function boundedNumber(value, label, minimum, maximum, fallback = undefined) {
  const resolved = value === undefined && fallback !== undefined ? fallback : value
  if (typeof resolved !== 'number' || !Number.isFinite(resolved) || resolved < minimum || resolved > maximum) {
    throw new RuntimeFailure('CONFIG_INVALID', `${label} must be between ${minimum} and ${maximum}.`)
  }
  return resolved
}

function boundedInteger(value, label, minimum, maximum, fallback = undefined) {
  const resolved = boundedNumber(value, label, minimum, maximum, fallback)
  if (!Number.isInteger(resolved)) {
    throw new RuntimeFailure('CONFIG_INVALID', `${label} must be an integer.`)
  }
  return resolved
}

function vec3(value, label, fallback) {
  const resolved = value === undefined ? fallback : value
  if (!Array.isArray(resolved) || resolved.length !== 3) {
    throw new RuntimeFailure('CONFIG_INVALID', `${label} must contain exactly three numbers.`)
  }
  return resolved.map((component, index) => boundedNumber(component, `${label}[${index}]`, -20, 20))
}

function normalizeHex(value, label, fallback) {
  const resolved = value === undefined ? fallback : value
  if (typeof resolved !== 'string' || !/^#[0-9a-fA-F]{6}$/.test(resolved)) {
    throw new RuntimeFailure('CONFIG_INVALID', `${label} must be a six-digit hexadecimal color.`)
  }
  return resolved.toUpperCase()
}

function hexRgb(value) {
  return [
    Number.parseInt(value.slice(1, 3), 16) / 255,
    Number.parseInt(value.slice(3, 5), 16) / 255,
    Number.parseInt(value.slice(5, 7), 16) / 255,
  ]
}

function assertLoopbackRuntime() {
  const loopbackHosts = new Set(['127.0.0.1', 'localhost', '::1', '[::1]'])
  if (!loopbackHosts.has(window.location.hostname)) {
    throw new RuntimeFailure('LOOPBACK_REQUIRED', 'The geometry runtime must be served from loopback.')
  }
  if (!['http:', 'https:'].includes(window.location.protocol)) {
    throw new RuntimeFailure('LOOPBACK_REQUIRED', 'The geometry runtime requires a local HTTP origin.')
  }
}

function localUrl(value, label, { optional = false } = {}) {
  if ((value === undefined || value === null || value === '') && optional) return null
  const raw = requiredString(value, label, 500)
  let resolved
  try {
    resolved = new URL(raw, window.location.origin)
  } catch {
    throw new RuntimeFailure('CONFIG_INVALID', `${label} is not a valid local URL.`)
  }
  if (resolved.origin !== window.location.origin || !['http:', 'https:'].includes(resolved.protocol)) {
    throw new RuntimeFailure('EXTERNAL_URL_REJECTED', `${label} must remain on the runtime origin.`)
  }
  return resolved.href
}

function compositionFields(value, label, fields) {
  const record = requiredRecord(value, label)
  if (Object.keys(record).some((key) => !fields.includes(key))) {
    throw new RuntimeFailure('CONFIG_INVALID', `${label} contains an unsupported field.`)
  }
  if (fields.some((key) => !Object.hasOwn(record, key))) {
    throw new RuntimeFailure('CONFIG_INVALID', `${label} is incomplete.`)
  }
  return record
}

function boundedPair(value, label, minimum, maximum) {
  if (!Array.isArray(value) || value.length !== 2) {
    throw new RuntimeFailure('CONFIG_INVALID', `${label} must contain exactly two numbers.`)
  }
  return Object.freeze(value.map((entry, index) => boundedNumber(entry, `${label}[${index}]`, minimum, maximum)))
}

function normalizeComposition(payload, masterDurationSeconds) {
  const composition = compositionFields(payload, 'composition', [
    'schemaVersion', 'revision', 'geometryCoverage', 'production', 'readability', 'framing', 'envelope',
  ])
  if (
    composition.schemaVersion !== '1.0.0'
    || composition.revision !== 'scattered-geometry-first-3.7'
    || composition.geometryCoverage !== 'full-frame'
  ) {
    throw new RuntimeFailure('CONFIG_INVALID', 'The geometry-first composition identity is invalid.')
  }
  const productionContract = {
    logoVisible: true,
    artistVisible: true,
    titleVisible: true,
    spectrumBarsVisible: false,
    spectralRibbonVisible: false,
    technicalMetadataVisible: false,
    sectionLabelsVisible: false,
  }
  const production = compositionFields(composition.production, 'composition.production', Object.keys(productionContract))
  for (const [key, expected] of Object.entries(productionContract)) {
    if (production[key] !== expected) {
      throw new RuntimeFailure('CONFIG_INVALID', `composition.production.${key} must be ${expected}.`)
    }
  }

  const readability = compositionFields(composition.readability, 'composition.readability', [
    'mode', 'minimumBrightness', 'haloSuppression', 'zones',
  ])
  if (readability.mode !== 'soft-ellipses' || !Array.isArray(readability.zones) || readability.zones.length !== 2) {
    throw new RuntimeFailure('CONFIG_INVALID', 'The readability treatment requires exactly two soft elliptical zones.')
  }
  const zones = readability.zones.map((value, index) => {
    const label = `composition.readability.zones[${index}]`
    const zone = compositionFields(value, label, ['id', 'center', 'radius', 'strength'])
    if (!['logo', 'identity'].includes(zone.id)) {
      throw new RuntimeFailure('CONFIG_INVALID', `${label}.id must identify logo or identity.`)
    }
    return Object.freeze({
      id: zone.id,
      center: boundedPair(zone.center, `${label}.center`, 0, 1),
      radius: boundedPair(zone.radius, `${label}.radius`, 0.02, 0.25),
      strength: boundedNumber(zone.strength, `${label}.strength`, 0, 0.75),
    })
  })
  if (zones[0].id === zones[1].id) {
    throw new RuntimeFailure('CONFIG_INVALID', 'The readability zones must protect logo and identity separately.')
  }
  const framing = compositionFields(composition.framing, 'composition.framing', ['center', 'shapeScale', 'depthStrength'])
  if (!Array.isArray(composition.envelope) || composition.envelope.length < 2 || composition.envelope.length > 64) {
    throw new RuntimeFailure('CONFIG_INVALID', 'The composition envelope requires between 2 and 64 keyframes.')
  }
  let previousTime = -1
  const envelope = composition.envelope.map((value, index) => {
    const label = `composition.envelope[${index}]`
    const point = compositionFields(value, label, ['timeSeconds', 'density', 'brightness', 'scale', 'deformation'])
    const timeSeconds = boundedNumber(point.timeSeconds, `${label}.timeSeconds`, 0, masterDurationSeconds)
    if (timeSeconds <= previousTime) {
      throw new RuntimeFailure('CONFIG_INVALID', 'Composition envelope times must be strictly increasing.')
    }
    previousTime = timeSeconds
    return Object.freeze({
      timeSeconds,
      density: boundedNumber(point.density, `${label}.density`, 0, 1),
      brightness: boundedNumber(point.brightness, `${label}.brightness`, 0, 1),
      scale: boundedNumber(point.scale, `${label}.scale`, 0.25, 2),
      deformation: boundedNumber(point.deformation, `${label}.deformation`, 0, 2),
    })
  })
  const finalPoint = envelope[envelope.length - 1]
  if (
    envelope[0].timeSeconds !== 0
    || Math.abs(finalPoint.timeSeconds - masterDurationSeconds) > 0.000_001
    || finalPoint.density !== 0
    || finalPoint.brightness !== 0
  ) {
    throw new RuntimeFailure('CONFIG_INVALID', 'The composition envelope must span the master and extinguish at EOF.')
  }
  return Object.freeze({
    schemaVersion: composition.schemaVersion,
    revision: composition.revision,
    geometryCoverage: composition.geometryCoverage,
    production: Object.freeze({ ...productionContract }),
    readability: Object.freeze({
      mode: 'soft-ellipses',
      minimumBrightness: boundedNumber(readability.minimumBrightness, 'composition.readability.minimumBrightness', 0.25, 1),
      haloSuppression: boundedNumber(readability.haloSuppression, 'composition.readability.haloSuppression', 0, 1),
      zones: Object.freeze(zones),
    }),
    framing: Object.freeze({
      center: boundedPair(framing.center, 'composition.framing.center', 0, 1),
      shapeScale: boundedNumber(framing.shapeScale, 'composition.framing.shapeScale', 0.25, 2),
      depthStrength: boundedNumber(framing.depthStrength, 'composition.framing.depthStrength', 0, 1),
    }),
    envelope: Object.freeze(envelope),
  })
}

function normalizeConfig(payload) {
  const config = requiredRecord(payload, 'runtime config')
  if (config.schemaVersion !== '1.0.0') {
    throw new RuntimeFailure('CONFIG_INVALID', 'Unsupported runtime config schema version.')
  }
  if (config.rendererId !== 'wzhk-generative-geometry') {
    throw new RuntimeFailure('CONFIG_INVALID', 'Unexpected runtime renderer identity.')
  }
  const jobId = canonicalUuid(config.jobId, 'jobId')
  if (!['preview', 'production'].includes(config.mode)) {
    throw new RuntimeFailure('CONFIG_INVALID', 'mode must be preview or production.')
  }

  if (!Array.isArray(config.trustedShapes) || config.trustedShapes.length < 10) {
    throw new RuntimeFailure('CONFIG_INVALID', 'trustedShapes must contain at least ten built-in shape IDs.')
  }
  const trustedShapes = config.trustedShapes.map((shape, index) => {
    const shapeId = requiredString(shape, `trustedShapes[${index}]`, 40)
    if (!Object.hasOwn(SHAPES, shapeId)) {
      throw new RuntimeFailure('UNTRUSTED_SHAPE', `Shape ${shapeId} is not built into this runtime.`)
    }
    return shapeId
  })
  if (new Set(trustedShapes).size !== trustedShapes.length) {
    throw new RuntimeFailure('CONFIG_INVALID', 'trustedShapes must not contain duplicates.')
  }

  const pointDomain = requiredRecord(config.pointDomain, 'pointDomain')
  const domainWidth = boundedInteger(pointDomain.width, 'pointDomain.width', 1, 1024)
  const domainHeight = boundedInteger(pointDomain.height, 'pointDomain.height', 1, 1024)
  const pointCount = boundedInteger(config.pointCount, 'pointCount', 128, 65_536)
  if (domainWidth * domainHeight < pointCount) {
    throw new RuntimeFailure('CONFIG_INVALID', 'The point domain cannot address the configured point count.')
  }

  if (!Array.isArray(config.choreography) || config.choreography.length === 0 || config.choreography.length > 256) {
    throw new RuntimeFailure('CONFIG_INVALID', 'choreography must contain between 1 and 256 entries.')
  }
  let previousEnd = -Infinity
  const trustedShapeSet = new Set(trustedShapes)
  const masterDurationSeconds = boundedNumber(
    config.masterDurationSeconds,
    'masterDurationSeconds',
    1,
    86_400,
  )
  const composition = normalizeComposition(config.composition, masterDurationSeconds)
  const choreography = config.choreography.map((entryValue, index) => {
    const entry = requiredRecord(entryValue, `choreography[${index}]`)
    const sourceShape = requiredString(entry.sourceShape, `choreography[${index}].sourceShape`, 40)
    const targetShape = requiredString(entry.targetShape, `choreography[${index}].targetShape`, 40)
    if (!trustedShapeSet.has(sourceShape) || !trustedShapeSet.has(targetShape)) {
      throw new RuntimeFailure('UNTRUSTED_SHAPE', 'Choreography may use only trusted built-in shapes.')
    }
    const startSeconds = boundedNumber(
      entry.startSeconds,
      `choreography[${index}].startSeconds`,
      0,
      masterDurationSeconds,
    )
    const durationSeconds = boundedNumber(
      entry.durationSeconds,
      `choreography[${index}].durationSeconds`,
      0.05,
      Math.min(128, masterDurationSeconds),
    )
    if (startSeconds + durationSeconds > masterDurationSeconds + 0.001) {
      throw new RuntimeFailure('CONFIG_INVALID', 'A choreography morph extends beyond master EOF.')
    }
    if (startSeconds + 0.000_001 < previousEnd) {
      throw new RuntimeFailure('CONFIG_INVALID', 'Choreography entries must be ordered and non-overlapping.')
    }
    previousEnd = startSeconds + durationSeconds
    const easing = requiredString(entry.easing, `choreography[${index}].easing`, 30)
    if (!Object.hasOwn(EASINGS, easing)) {
      throw new RuntimeFailure('CONFIG_INVALID', `Unsupported easing ${easing}.`)
    }
    const section = requiredString(entry.section, `choreography[${index}].section`, 30)
    if (!SECTIONS.has(section)) {
      throw new RuntimeFailure('CONFIG_INVALID', `Unsupported section ${section}.`)
    }
    return Object.freeze({ sourceShape, targetShape, startSeconds, durationSeconds, easing, section })
  })

  const paletteValue = requiredRecord(config.palette, 'palette')
  const palette = Object.freeze({
    background: normalizeHex(paletteValue.background, 'palette.background', '#070A12'),
    primary: normalizeHex(paletteValue.primary, 'palette.primary', '#78D8FF'),
    secondary: normalizeHex(paletteValue.secondary, 'palette.secondary', '#9D7CFF'),
    text: normalizeHex(paletteValue.text, 'palette.text', '#F5F7FA'),
  })

  const cameraValue = requiredRecord(config.camera, 'camera')
  const camera = Object.freeze({
    position: Object.freeze(vec3(cameraValue.position, 'camera.position', [0, 0, 3.2])),
    target: Object.freeze(vec3(cameraValue.target, 'camera.target', [0, 0, 0])),
    fovDegrees: boundedNumber(cameraValue.fovDegrees, 'camera.fovDegrees', 28, 75, 46),
    near: boundedNumber(cameraValue.near, 'camera.near', 0.01, 2, 0.1),
    far: boundedNumber(cameraValue.far, 'camera.far', 4, 100, 20),
    orbitAmplitudeDegrees: boundedNumber(
      cameraValue.orbitAmplitudeDegrees,
      'camera.orbitAmplitudeDegrees',
      0,
      12,
      4,
    ),
    orbitSpeed: boundedNumber(cameraValue.orbitSpeed, 'camera.orbitSpeed', 0, 0.2, 0.025),
    dollyAmplitude: boundedNumber(cameraValue.dollyAmplitude, 'camera.dollyAmplitude', 0, 0.16, 0.035),
  })
  if (camera.near >= camera.far) {
    throw new RuntimeFailure('CONFIG_INVALID', 'camera.near must be less than camera.far.')
  }

  const audioValue = requiredRecord(config.audioMapping, 'audioMapping')
  const fftSize = boundedInteger(audioValue.fftSize, 'audioMapping.fftSize', 256, 8192, 2048)
  if ((fftSize & (fftSize - 1)) !== 0) {
    throw new RuntimeFailure('CONFIG_INVALID', 'audioMapping.fftSize must be a power of two.')
  }
  const audioMapping = Object.freeze({
    fftSize,
    smoothingTimeConstant: boundedNumber(
      audioValue.smoothingTimeConstant,
      'audioMapping.smoothingTimeConstant',
      0,
      0.98,
      0.72,
    ),
    lowGain: boundedNumber(audioValue.lowGain, 'audioMapping.lowGain', 0, 3, 1),
    midGain: boundedNumber(audioValue.midGain, 'audioMapping.midGain', 0, 3, 1),
    highGain: boundedNumber(audioValue.highGain, 'audioMapping.highGain', 0, 3, 1),
    energyGain: boundedNumber(audioValue.energyGain, 'audioMapping.energyGain', 0, 3, 1),
    transientGain: boundedNumber(audioValue.transientGain, 'audioMapping.transientGain', 0, 8, 3.2),
    transientThreshold: boundedNumber(
      audioValue.transientThreshold,
      'audioMapping.transientThreshold',
      0,
      0.8,
      0.045,
    ),
    propagationSpeed: boundedNumber(audioValue.propagationSpeed, 'audioMapping.propagationSpeed', 0.0001, 8),
    propagationDecay: boundedNumber(audioValue.propagationDecay, 'audioMapping.propagationDecay', 0, 8),
    propagationWidth: boundedNumber(audioValue.propagationWidth, 'audioMapping.propagationWidth', 0.0001, 1),
  })

  const brandingValue = config.branding === undefined
    ? {}
    : requiredRecord(config.branding, 'branding')
  if (config.mode === 'production' && brandingValue.enabled !== true) {
    throw new RuntimeFailure('CONFIG_INVALID', 'Production composition requires its foreground identity.')
  }
  const branding = Object.freeze({
    enabled: brandingValue.enabled === true,
    artist: optionalText(brandingValue.artist, 'branding.artist', 'DJ WaZaHaKa', 120),
    title: optionalText(brandingValue.title, 'branding.title', 'SCATTERED', 120),
  })

  const developerLabValue = config.developerLab === undefined
    ? {}
    : requiredRecord(config.developerLab, 'developerLab')
  const controlValue = config.control === undefined
    ? {}
    : requiredRecord(config.control, 'control')
  if (developerLabValue.spectrumDiagnostics !== undefined && typeof developerLabValue.spectrumDiagnostics !== 'boolean') {
    throw new RuntimeFailure('CONFIG_INVALID', 'developerLab.spectrumDiagnostics must be a boolean.')
  }
  let previewOverride = null
  if (developerLabValue.previewOverride !== undefined && developerLabValue.previewOverride !== null) {
    if (config.mode !== 'preview') {
      throw new RuntimeFailure('CONFIG_INVALID', 'Preview overrides cannot enter a production composition.')
    }
    const override = requiredRecord(developerLabValue.previewOverride, 'developerLab.previewOverride')
    if (!['shape', 'morph', 'section', 'lab'].includes(override.mode)) {
      throw new RuntimeFailure('CONFIG_INVALID', 'The geometry preview mode is invalid.')
    }
    const previewShape = (value, label) => {
      if (value === undefined || value === null) return null
      const shape = requiredRecord(value, label)
      if (!trustedShapeSet.has(shape.shapeId)) {
        throw new RuntimeFailure('UNTRUSTED_SHAPE', 'A preview shape must belong to the trusted shape library.')
      }
      return shape.shapeId
    }
    const sourceShape = previewShape(override.shapeA, 'previewOverride.shapeA')
    const targetShape = previewShape(override.shapeB, 'previewOverride.shapeB')
    if (override.mode !== 'section' && sourceShape === null) {
      throw new RuntimeFailure('CONFIG_INVALID', 'A shape laboratory override requires its source shape.')
    }
    if (override.mode === 'morph' && (targetShape === null || override.morphProgress === null || override.morphProgress === undefined)) {
      throw new RuntimeFailure('CONFIG_INVALID', 'A morph preview requires its target shape and progress.')
    }
    if (!['disabled', 'simulated'].includes(override.audioMode)) {
      throw new RuntimeFailure('CONFIG_INVALID', 'The preview audio mode is invalid.')
    }
    previewOverride = Object.freeze({
      mode: override.mode,
      sourceShape,
      targetShape: targetShape || sourceShape,
      morph: override.morphProgress === undefined || override.morphProgress === null
        ? 0
        : boundedNumber(override.morphProgress, 'previewOverride.morphProgress', 0, 1),
      audioMode: override.audioMode,
    })
  }
  const logoUrl = localUrl(config.logoUrl, 'logoUrl', { optional: true })
  if (config.mode === 'production' && logoUrl === null) {
    throw new RuntimeFailure('CONFIG_INVALID', 'Production composition requires a local logo asset.')
  }

  return Object.freeze({
    schemaVersion: '1.0.0',
    rendererId: 'wzhk-generative-geometry',
    jobId,
    mode: config.mode,
    seed: boundedInteger(config.seed, 'seed', 0, 2_147_483_647),
    pointCount,
    pointDomain: Object.freeze({ width: domainWidth, height: domainHeight }),
    targetFps: boundedInteger(config.targetFps, 'targetFps', 24, 120),
    masterDurationSeconds,
    pointSize: boundedNumber(config.pointSize, 'pointSize', 1, 14, 5.2),
    globalScale: boundedNumber(config.globalScale, 'globalScale', 0.4, 1.8, 1),
    logoUrl,
    audioUrl: localUrl(config.audioUrl, 'audioUrl'),
    palette,
    camera,
    audioMapping,
    branding,
    composition,
    developerLab: Object.freeze({
      enabled: developerLabValue.enabled === true && config.mode === 'preview',
      spectrumDiagnostics: developerLabValue.spectrumDiagnostics === true && config.mode === 'preview',
      previewOverride,
    }),
    control: Object.freeze({
      pollMilliseconds: boundedInteger(
        controlValue.pollMilliseconds,
        'control.pollMilliseconds',
        100,
        2000,
        250,
      ),
      telemetryIntervalMilliseconds: boundedInteger(
        controlValue.telemetryIntervalMilliseconds,
        'control.telemetryIntervalMilliseconds',
        500,
        10_000,
        1000,
      ),
    }),
    trustedShapes: Object.freeze(trustedShapes),
    choreography: Object.freeze(choreography),
  })
}

async function fetchJson(path, errorCode) {
  let response
  try {
    response = await fetch(path, {
      method: 'GET',
      cache: 'no-store',
      credentials: 'same-origin',
      referrerPolicy: 'no-referrer',
      headers: { Accept: 'application/json' },
    })
  } catch {
    throw new RuntimeFailure(errorCode, `Local request ${path} could not be completed.`)
  }
  if (!response.ok) {
    throw new RuntimeFailure(errorCode, `Local request ${path} returned HTTP ${response.status}.`)
  }
  try {
    return await response.json()
  } catch {
    throw new RuntimeFailure(errorCode, `Local request ${path} did not return valid JSON.`)
  }
}

async function fetchText(path, errorCode) {
  let response
  try {
    response = await fetch(path, {
      method: 'GET',
      cache: 'no-store',
      credentials: 'same-origin',
      referrerPolicy: 'no-referrer',
      headers: { Accept: 'text/plain' },
    })
  } catch {
    throw new RuntimeFailure(errorCode, `Local shader ${path} could not be loaded.`)
  }
  if (!response.ok) {
    throw new RuntimeFailure(errorCode, `Local shader ${path} returned HTTP ${response.status}.`)
  }
  return response.text()
}

let activeConfig = null
let activeSessionJobId = null
let querySessionJobId = null

function updatePageTitle() {
  if (activeSessionJobId) {
    document.title = `TrackPrompt-WZHK-Geometry-${activeSessionJobId}`
  }
}

function initializeSessionIdentity(config) {
  const queryValue = new URLSearchParams(window.location.search).get('sessionJobId')
  querySessionJobId = queryValue === null
    ? null
    : canonicalUuid(queryValue, 'sessionJobId', 'SESSION_ID_INVALID')
  activeSessionJobId = querySessionJobId || config.jobId
  updatePageTitle()
}

function acceptControlSessionIdentity(jobId) {
  if (jobId === null) return true
  if (querySessionJobId !== null && querySessionJobId !== jobId) return false
  if (querySessionJobId === null) {
    activeSessionJobId = jobId
    updatePageTitle()
  }
  return true
}

async function postRuntimeEvent(type, payload = {}) {
  if (!activeConfig) return
  const body = {
    type,
    rendererId: activeConfig.rendererId,
    jobId: activeSessionJobId || activeConfig.jobId,
    runtimeVersion: RUNTIME_VERSION,
    runtimeMilliseconds: Math.round(performance.now()),
    payload,
  }
  try {
    await fetch(EVENT_URL, {
      method: 'POST',
      cache: 'no-store',
      credentials: 'same-origin',
      referrerPolicy: 'no-referrer',
      keepalive: type === 'ended' || type === 'error',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
    })
  } catch {
    // Runtime reporting is best-effort. Rendering must not recurse into another error.
  }
}

function compileShader(gl, type, source, label) {
  const shader = gl.createShader(type)
  if (!shader) {
    throw new RuntimeFailure('SHADER_COMPILE_FAILED', `${label} shader allocation failed.`)
  }
  gl.shaderSource(shader, source)
  gl.compileShader(shader)
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    const detail = (gl.getShaderInfoLog(shader) || 'No shader compiler log was returned.').slice(0, 1600)
    gl.deleteShader(shader)
    throw new RuntimeFailure('SHADER_COMPILE_FAILED', `${label} shader compilation failed.`, detail)
  }
  return shader
}

function linkProgram(gl, vertexSource, fragmentSource) {
  const vertex = compileShader(gl, gl.VERTEX_SHADER, vertexSource, 'NeoPixel vertex')
  const fragment = compileShader(gl, gl.FRAGMENT_SHADER, fragmentSource, 'NeoPixel fragment')
  const program = gl.createProgram()
  if (!program) {
    gl.deleteShader(vertex)
    gl.deleteShader(fragment)
    throw new RuntimeFailure('SHADER_LINK_FAILED', 'WebGL program allocation failed.')
  }
  gl.attachShader(program, vertex)
  gl.attachShader(program, fragment)
  gl.linkProgram(program)
  gl.deleteShader(vertex)
  gl.deleteShader(fragment)
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    const detail = (gl.getProgramInfoLog(program) || 'No program linker log was returned.').slice(0, 1600)
    gl.deleteProgram(program)
    throw new RuntimeFailure('SHADER_LINK_FAILED', 'NeoPixel shader program linking failed.', detail)
  }
  return program
}

function uniformLocations(gl, program, names) {
  return Object.fromEntries(names.map((name) => {
    const location = gl.getUniformLocation(program, name)
    if (location === null) {
      throw new RuntimeFailure('SHADER_LINK_FAILED', `Required shader uniform ${name} is unavailable.`)
    }
    return [name, location]
  }))
}

function perspectiveMatrix(fovRadians, aspect, near, far) {
  const f = 1 / Math.tan(fovRadians / 2)
  const rangeInverse = 1 / (near - far)
  return new Float32Array([
    f / aspect, 0, 0, 0,
    0, f, 0, 0,
    0, 0, (near + far) * rangeInverse, -1,
    0, 0, near * far * 2 * rangeInverse, 0,
  ])
}

function subtract3(left, right) {
  return [left[0] - right[0], left[1] - right[1], left[2] - right[2]]
}

function dot3(left, right) {
  return left[0] * right[0] + left[1] * right[1] + left[2] * right[2]
}

function cross3(left, right) {
  return [
    left[1] * right[2] - left[2] * right[1],
    left[2] * right[0] - left[0] * right[2],
    left[0] * right[1] - left[1] * right[0],
  ]
}

function normalize3(value) {
  const length = Math.hypot(value[0], value[1], value[2])
  if (length < 0.000_001) return [0, 0, 1]
  return value.map((component) => component / length)
}

function lookAtMatrix(eye, target) {
  const zAxis = normalize3(subtract3(eye, target))
  const xAxis = normalize3(cross3([0, 1, 0], zAxis))
  const yAxis = cross3(zAxis, xAxis)
  return new Float32Array([
    xAxis[0], yAxis[0], zAxis[0], 0,
    xAxis[1], yAxis[1], zAxis[1], 0,
    xAxis[2], yAxis[2], zAxis[2], 0,
    -dot3(xAxis, eye), -dot3(yAxis, eye), -dot3(zAxis, eye), 1,
  ])
}

class GeometryRenderer {
  constructor(canvas, config, vertexSource, fragmentSource) {
    this.canvas = canvas
    this.config = config
    const gl = canvas.getContext('webgl2', {
      alpha: false,
      antialias: false,
      depth: true,
      failIfMajorPerformanceCaveat: true,
      powerPreference: 'high-performance',
      premultipliedAlpha: false,
      preserveDrawingBuffer: false,
    })
    if (!gl) {
      throw new RuntimeFailure('WEBGL2_UNAVAILABLE', 'A hardware WebGL2 context could not be created.')
    }
    this.gl = gl
    this.program = linkProgram(gl, vertexSource, fragmentSource)
    this.uniforms = uniformLocations(gl, this.program, [
      'uPointDomain',
      'uSourceShape',
      'uTargetShape',
      'uMorph',
      'uTime',
      'uSeedPhase',
      'uPointSize',
      'uGlobalScale',
      'uShapeScale',
      'uDepthStrength',
      'uFrameCenter',
      'uEnvelope',
      'uMasterDuration',
      'uAudio',
      'uTransient',
      'uImpulseOrigin',
      'uImpulseAge',
      'uPalettePrimary',
      'uPaletteSecondary',
      'uPropagation',
      'uReadabilityZone0',
      'uReadabilityZone1',
      'uReadabilityStrength',
      'uReadabilityMinimum',
      'uHaloSuppression',
      'uView',
      'uProjection',
    ])
    this.vertexArray = gl.createVertexArray()
    if (!this.vertexArray) {
      throw new RuntimeFailure('GPU_RENDERER_UNAVAILABLE', 'A WebGL2 vertex array could not be created.')
    }
    gl.bindVertexArray(this.vertexArray)
    gl.useProgram(this.program)
    gl.enable(gl.BLEND)
    gl.blendEquation(gl.FUNC_ADD)
    gl.blendFunc(gl.ONE, gl.ONE)
    gl.disable(gl.CULL_FACE)
    gl.disable(gl.DEPTH_TEST)
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1)
    this.background = hexRgb(config.palette.background)
    this.primary = hexRgb(config.palette.primary)
    this.secondary = hexRgb(config.palette.secondary)
    this.seedPhase = config.seed / 2_147_483_647
    this.resize()
  }

  resize() {
    // Production capture is exactly 1920x1080. Browser DPI scaling must not
    // silently turn the deterministic render target into a 4K workload.
    const pixelRatio = 1
    const width = Math.max(1, Math.round(this.canvas.clientWidth * pixelRatio))
    const height = Math.max(1, Math.round(this.canvas.clientHeight * pixelRatio))
    if (this.canvas.width !== width || this.canvas.height !== height) {
      this.canvas.width = width
      this.canvas.height = height
    }
    this.gl.viewport(0, 0, width, height)
  }

  cameraAt(seconds, energy, low) {
    const camera = this.config.camera
    const target = camera.target
    const baseOffset = subtract3(camera.position, target)
    const yaw = Math.sin(seconds * camera.orbitSpeed + this.seedPhase * Math.PI * 2)
      * camera.orbitAmplitudeDegrees * Math.PI / 180
    const cosine = Math.cos(yaw)
    const sine = Math.sin(yaw)
    const dolly = 1 + Math.sin(seconds * 0.033 + this.seedPhase) * camera.dollyAmplitude + low * 0.018
    const eye = [
      target[0] + (baseOffset[0] * cosine + baseOffset[2] * sine) * dolly,
      target[1] + baseOffset[1] * dolly + Math.sin(seconds * 0.021) * 0.025 * (1 + energy),
      target[2] + (-baseOffset[0] * sine + baseOffset[2] * cosine) * dolly,
    ]
    return lookAtMatrix(eye, target)
  }

  draw(frame) {
    const gl = this.gl
    this.resize()
    gl.useProgram(this.program)
    gl.bindVertexArray(this.vertexArray)
    gl.clearColor(this.background[0], this.background[1], this.background[2], 1)
    gl.clear(gl.COLOR_BUFFER_BIT)

    const aspect = this.canvas.width / Math.max(1, this.canvas.height)
    const projection = perspectiveMatrix(
      this.config.camera.fovDegrees * Math.PI / 180,
      aspect,
      this.config.camera.near,
      this.config.camera.far,
    )
    const view = this.cameraAt(frame.seconds, frame.audio.energy, frame.audio.low)
    const composition = this.config.composition
    const envelope = resolveCompositionEnvelope(composition, frame.seconds)
    const [firstZone, secondZone] = composition.readability.zones

    gl.uniform2i(this.uniforms.uPointDomain, this.config.pointDomain.width, this.config.pointDomain.height)
    gl.uniform1i(this.uniforms.uSourceShape, SHAPES[frame.sourceShape])
    gl.uniform1i(this.uniforms.uTargetShape, SHAPES[frame.targetShape])
    gl.uniform1f(this.uniforms.uMorph, frame.morph)
    gl.uniform1f(this.uniforms.uTime, frame.seconds)
    gl.uniform1f(this.uniforms.uSeedPhase, this.seedPhase)
    gl.uniform1f(this.uniforms.uPointSize, this.config.pointSize)
    gl.uniform1f(this.uniforms.uGlobalScale, this.config.globalScale)
    gl.uniform1f(this.uniforms.uShapeScale, composition.framing.shapeScale)
    gl.uniform1f(this.uniforms.uDepthStrength, composition.framing.depthStrength)
    gl.uniform2fv(this.uniforms.uFrameCenter, composition.framing.center)
    gl.uniform4f(this.uniforms.uEnvelope, envelope.density, envelope.brightness, envelope.scale, envelope.deformation)
    gl.uniform1f(this.uniforms.uMasterDuration, this.config.masterDurationSeconds)
    gl.uniform4f(
      this.uniforms.uAudio,
      frame.audio.low,
      frame.audio.mid,
      frame.audio.high,
      frame.audio.energy,
    )
    gl.uniform1f(this.uniforms.uTransient, frame.propagation.strength)
    gl.uniform2f(this.uniforms.uImpulseOrigin, frame.propagation.origin[0], frame.propagation.origin[1])
    gl.uniform1f(this.uniforms.uImpulseAge, frame.propagation.age)
    gl.uniform3fv(this.uniforms.uPalettePrimary, this.primary)
    gl.uniform3fv(this.uniforms.uPaletteSecondary, this.secondary)
    gl.uniform3f(
      this.uniforms.uPropagation,
      this.config.audioMapping.propagationSpeed,
      this.config.audioMapping.propagationDecay,
      this.config.audioMapping.propagationWidth,
    )
    gl.uniform4f(this.uniforms.uReadabilityZone0, ...firstZone.center, ...firstZone.radius)
    gl.uniform4f(this.uniforms.uReadabilityZone1, ...secondZone.center, ...secondZone.radius)
    gl.uniform2f(this.uniforms.uReadabilityStrength, firstZone.strength, secondZone.strength)
    gl.uniform1f(this.uniforms.uReadabilityMinimum, composition.readability.minimumBrightness)
    gl.uniform1f(this.uniforms.uHaloSuppression, composition.readability.haloSuppression)
    gl.uniformMatrix4fv(this.uniforms.uView, false, view)
    gl.uniformMatrix4fv(this.uniforms.uProjection, false, projection)
    gl.drawArrays(gl.POINTS, 0, this.config.pointCount)

    const error = gl.getError()
    if (error !== gl.NO_ERROR) {
      throw new RuntimeFailure('GPU_RENDER_ERROR', `WebGL2 returned error code ${error}.`)
    }
  }

  capabilityReport() {
    const gl = this.gl
    const debugInfo = gl.getExtension('WEBGL_debug_renderer_info')
    return {
      webglVersion: String(gl.getParameter(gl.VERSION)).slice(0, 160),
      shadingLanguageVersion: String(gl.getParameter(gl.SHADING_LANGUAGE_VERSION)).slice(0, 160),
      vendor: String(
        debugInfo ? gl.getParameter(debugInfo.UNMASKED_VENDOR_WEBGL) : gl.getParameter(gl.VENDOR),
      ).slice(0, 160),
      renderer: String(
        debugInfo ? gl.getParameter(debugInfo.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER),
      ).slice(0, 200),
      maxPointSize: Number(gl.getParameter(gl.ALIASED_POINT_SIZE_RANGE)[1]),
      canvasWidth: this.canvas.width,
      canvasHeight: this.canvas.height,
    }
  }
}

function averageFrequencyRange(data, sampleRate, minimumHz, maximumHz) {
  const nyquist = sampleRate / 2
  const start = Math.max(0, Math.floor(minimumHz / nyquist * data.length))
  const end = Math.min(data.length, Math.max(start + 1, Math.ceil(maximumHz / nyquist * data.length)))
  let sum = 0
  for (let index = start; index < end; index += 1) {
    const normalized = data[index] / 255
    sum += normalized * normalized
  }
  return Math.sqrt(sum / Math.max(1, end - start))
}

class AudioReactiveEngine {
  constructor(config, onWarning) {
    this.config = config
    this.onWarning = onWarning
    this.audio = new Audio()
    this.audio.preload = 'auto'
    this.audio.src = config.audioUrl
    this.audio.loop = false
    this.audio.controls = false
    this.audio.volume = 1
    this.context = null
    this.analyser = null
    this.frequencyData = null
    this.silentGain = null
    this.playPromise = null
    this.lastRawEnergy = 0
    this.smoothed = { low: 0, mid: 0, high: 0, energy: 0, transient: 0 }
    this.warned = false
    this.lastSyncMilliseconds = -Infinity
  }

  async ensureGraph() {
    if (this.context) return
    const AudioContextClass = window.AudioContext || window.webkitAudioContext
    if (!AudioContextClass) {
      throw new RuntimeFailure('WEB_AUDIO_UNAVAILABLE', 'Web Audio is unavailable in this browser.')
    }
    const context = new AudioContextClass({ latencyHint: 'playback' })
    const analyser = context.createAnalyser()
    analyser.fftSize = this.config.audioMapping.fftSize
    analyser.smoothingTimeConstant = this.config.audioMapping.smoothingTimeConstant
    const source = context.createMediaElementSource(this.audio)
    const silentGain = context.createGain()
    silentGain.gain.value = 0
    source.connect(analyser)
    analyser.connect(silentGain)
    silentGain.connect(context.destination)
    this.context = context
    this.analyser = analyser
    this.silentGain = silentGain
    this.frequencyData = new Uint8Array(analyser.frequencyBinCount)
  }

  warnOnce(error) {
    if (this.warned) return
    this.warned = true
    this.onWarning(error)
  }

  async synchronize(control) {
    const now = performance.now()
    if (now - this.lastSyncMilliseconds < 180) return
    this.lastSyncMilliseconds = now
    try {
      await this.ensureGraph()
      if (control.state === 'playing') {
        if (this.context.state === 'suspended') await this.context.resume()
        if (Math.abs(this.audio.currentTime - control.currentSeconds) > 0.22) {
          this.audio.currentTime = clamp(control.currentSeconds, 0, this.config.masterDurationSeconds)
        }
        if (this.audio.paused && !this.playPromise) {
          this.playPromise = this.audio.play()
            .catch(() => {
              this.warnOnce(new RuntimeFailure(
                'WEB_AUDIO_START_FAILED',
                'The silent analysis player could not start; geometry audio response is unavailable.',
              ))
            })
            .finally(() => { this.playPromise = null })
        }
      } else {
        this.audio.pause()
        if (Math.abs(this.audio.currentTime - control.currentSeconds) > 0.22) {
          this.audio.currentTime = clamp(control.currentSeconds, 0, this.config.masterDurationSeconds)
        }
      }
    } catch (error) {
      this.warnOnce(error)
    }
  }

  liveFeatures(deltaSeconds) {
    if (!this.analyser || !this.frequencyData || !this.context || this.context.state !== 'running') {
      return this.smoothFeatures({ low: 0, mid: 0, high: 0, energy: 0, transient: 0 }, deltaSeconds)
    }
    this.analyser.getByteFrequencyData(this.frequencyData)
    const lowRaw = averageFrequencyRange(this.frequencyData, this.context.sampleRate, 20, 250)
    const midRaw = averageFrequencyRange(this.frequencyData, this.context.sampleRate, 250, 2400)
    const highRaw = averageFrequencyRange(this.frequencyData, this.context.sampleRate, 2400, 14_000)
    const energyRaw = clamp(lowRaw * 0.42 + midRaw * 0.38 + highRaw * 0.20)
    const transientRaw = Math.max(
      0,
      energyRaw - this.lastRawEnergy - this.config.audioMapping.transientThreshold,
    )
    this.lastRawEnergy = energyRaw
    return this.smoothFeatures({
      low: clamp(lowRaw * this.config.audioMapping.lowGain),
      mid: clamp(midRaw * this.config.audioMapping.midGain),
      high: clamp(highRaw * this.config.audioMapping.highGain),
      energy: clamp(energyRaw * this.config.audioMapping.energyGain),
      transient: clamp(transientRaw * this.config.audioMapping.transientGain),
    }, deltaSeconds)
  }

  simulatedFeatures(seconds, deltaSeconds) {
    const beatPhase = (seconds * 2) % 1
    const kick = Math.pow(Math.max(0, 1 - beatPhase), 13)
    const offbeat = Math.pow(Math.max(0, 1 - ((seconds * 4 + 0.5) % 1)), 18)
    return this.smoothFeatures({
      low: clamp(0.16 + kick * 0.72 + Math.sin(seconds * 0.37) * 0.05),
      mid: clamp(0.22 + (Math.sin(seconds * 1.23) * 0.5 + 0.5) * 0.38),
      high: clamp(0.15 + offbeat * 0.58 + (Math.sin(seconds * 4.7) * 0.5 + 0.5) * 0.12),
      energy: clamp(0.26 + kick * 0.34 + (Math.sin(seconds * 0.61) * 0.5 + 0.5) * 0.24),
      transient: clamp(kick * 0.92 + offbeat * 0.38),
    }, deltaSeconds)
  }

  smoothFeatures(target, deltaSeconds) {
    const attack = 1 - Math.exp(-Math.max(0.001, deltaSeconds) * 22)
    const release = 1 - Math.exp(-Math.max(0.001, deltaSeconds) * 7)
    for (const key of Object.keys(this.smoothed)) {
      const coefficient = target[key] >= this.smoothed[key] ? attack : release
      this.smoothed[key] += (target[key] - this.smoothed[key]) * coefficient
      this.smoothed[key] = clamp(this.smoothed[key])
    }
    return { ...this.smoothed }
  }

  features(seconds, deltaSeconds, mode) {
    if (mode === 'disabled') {
      return this.smoothFeatures({ low: 0, mid: 0, high: 0, energy: 0, transient: 0 }, deltaSeconds)
    }
    if (mode === 'simulated') return this.simulatedFeatures(seconds, deltaSeconds)
    return this.liveFeatures(deltaSeconds)
  }

  spectrumBands(count) {
    if (!this.frequencyData || !this.context || this.context.state !== 'running') {
      return Array.from({ length: count }, () => 0)
    }
    const nyquist = this.context.sampleRate / 2
    const minimumHz = 28
    const maximumHz = 16_000
    return Array.from({ length: count }, (_unused, index) => {
      const startRatio = index / count
      const endRatio = (index + 1) / count
      const startHz = minimumHz * Math.pow(maximumHz / minimumHz, startRatio)
      const endHz = minimumHz * Math.pow(maximumHz / minimumHz, endRatio)
      const start = Math.max(0, Math.floor(startHz / nyquist * this.frequencyData.length))
      const end = Math.min(
        this.frequencyData.length,
        Math.max(start + 1, Math.ceil(endHz / nyquist * this.frequencyData.length)),
      )
      let sum = 0
      for (let bin = start; bin < end; bin += 1) {
        const value = this.frequencyData[bin] / 255
        sum += value * value
      }
      const rms = Math.sqrt(sum / Math.max(1, end - start))
      return clamp(Math.pow(rms, 1.45) * 0.72)
    })
  }

  stop() {
    this.audio.pause()
    if (this.context) void this.context.close()
  }
}

class IdentityOverlay {
  constructor(canvas, config) {
    this.canvas = canvas
    this.config = config
    this.context = canvas.getContext('2d', { alpha: true })
    if (!this.context) {
      throw new RuntimeFailure('IDENTITY_CANVAS_UNAVAILABLE', 'The identity compositor could not start.')
    }
    this.values = Array.from({ length: 56 }, () => 0)
    this.primary = hexRgb(config.palette.primary).map((channel) => Math.round(channel * 255))
    this.secondary = hexRgb(config.palette.secondary).map((channel) => Math.round(channel * 255))
    this.text = hexRgb(config.palette.text).map((channel) => Math.round(channel * 255))
    this.logo = new Image()
    this.logoReady = false
    if (config.logoUrl) {
      this.logo.addEventListener('load', () => { this.logoReady = true }, { once: true })
      this.logo.src = config.logoUrl
    }
    this.resize()
  }

  async ready() {
    if (!this.config.branding.enabled || !this.config.logoUrl) return
    let timeoutId
    try {
      await Promise.race([
        this.logo.decode(),
        new Promise((_resolve, reject) => {
          timeoutId = window.setTimeout(() => reject(new Error('logo-timeout')), 5000)
        }),
      ])
      this.logoReady = true
      await document.fonts.ready
    } catch {
      throw new RuntimeFailure('IDENTITY_ASSET_UNAVAILABLE', 'The local foreground identity could not be prepared.')
    } finally {
      window.clearTimeout(timeoutId)
    }
  }

  resize() {
    const width = Math.max(1, Math.round(this.canvas.clientWidth))
    const height = Math.max(1, Math.round(this.canvas.clientHeight))
    if (this.canvas.width !== width || this.canvas.height !== height) {
      this.canvas.width = width
      this.canvas.height = height
    }
  }

  draw(audio, features, seconds, section) {
    this.resize()
    const context = this.context
    context.clearRect(0, 0, this.canvas.width, this.canvas.height)
    const scaleX = this.canvas.width / 1920
    const scaleY = this.canvas.height / 1080
    // Diagnostic bars are deliberately unreachable in production. The
    // identity canvas stays transparent: no opaque patch or reserved panel.
    if (this.config.mode === 'preview' && this.config.developerLab.spectrumDiagnostics === true) {
      this.drawSpectrumDiagnostics(audio, features, seconds, section)
    }
    if (!this.config.branding.enabled) return
    context.save()
    context.shadowColor = 'rgba(7, 10, 18, 0.62)'
    context.shadowBlur = 5 * scaleY
    context.shadowOffsetY = 1 * scaleY
    const text = this.text
    if (this.logoReady && this.config.composition.production.logoVisible) {
      context.drawImage(this.logo, 96 * scaleX, 76 * scaleY, 220 * scaleX, 220 * scaleY)
    }
    context.fillStyle = `rgb(${text[0]}, ${text[1]}, ${text[2]})`
    context.textBaseline = 'alphabetic'
    if (this.config.composition.production.artistVisible) {
      context.font = `600 ${54 * scaleY}px Montserrat, Segoe UI, sans-serif`
      context.fillText(this.config.branding.artist, 96 * scaleX, 422 * scaleY)
    }
    if (this.config.composition.production.titleVisible) {
      context.font = `300 ${34 * scaleY}px Montserrat, Segoe UI, sans-serif`
      this.drawLetterSpacedText(this.config.branding.title, 96 * scaleX, 500 * scaleY, 3 * scaleX)
    }
    context.restore()
  }

  drawSpectrumDiagnostics(audio, features, seconds, section) {
    if (this.config.mode !== 'preview' || this.config.developerLab.spectrumDiagnostics !== true) return
    const context = this.context
    const targets = audio.spectrumBands(this.values.length)
    const sectionIntensity = section === 'intro'
      ? 0.68
      : section === 'outro'
        ? 0.58
        : section === 'post-grid-tail'
          ? 0.42
          : 1
    const tailFade = seconds <= 192
      ? 1
      : clamp(1 - (seconds - 192) / Math.max(0.001, this.config.masterDurationSeconds - 192))
    const scaleX = this.canvas.width / 1920
    const scaleY = this.canvas.height / 1080
    const spectrumX = 260 * scaleX
    const spectrumWidth = 1224 * scaleX
    const spectrumBaseline = 862 * scaleY
    const slot = spectrumWidth / this.values.length
    const barWidth = Math.max(2, slot * 0.62)
    const maximumHeight = 304 * scaleY
    const color = this.primary
    const accent = this.secondary
    context.save()
    context.shadowBlur = 12
    context.shadowColor = `rgba(${color[0]}, ${color[1]}, ${color[2]}, 0.42)`
    for (let index = 0; index < this.values.length; index += 1) {
      const target = clamp(targets[index] * sectionIntensity * tailFade)
      const coefficient = target >= this.values[index] ? 0.54 : 0.14
      this.values[index] += (target - this.values[index]) * coefficient
      const normalized = clamp(this.values[index] + features.energy * 0.045)
      const height = Math.max(1, normalized * maximumHeight)
      const x = spectrumX + index * slot + (slot - barWidth) * 0.5
      const gradient = context.createLinearGradient(0, spectrumBaseline - height, 0, spectrumBaseline)
      gradient.addColorStop(0, `rgba(${color[0]}, ${color[1]}, ${color[2]}, 0.96)`)
      gradient.addColorStop(1, `rgba(${accent[0]}, ${accent[1]}, ${accent[2]}, 0.72)`)
      context.fillStyle = gradient
      context.fillRect(x, spectrumBaseline - height, barWidth, height)
    }
    context.shadowBlur = 0
    context.fillStyle = `rgba(${color[0]}, ${color[1]}, ${color[2]}, ${0.22 * tailFade})`
    context.fillRect(spectrumX, spectrumBaseline + 5 * scaleY, spectrumWidth, Math.max(1, scaleY))
    context.restore()
  }

  drawLetterSpacedText(text, x, y, spacing) {
    let cursor = x
    for (const character of text) {
      this.context.fillText(character, cursor, y)
      cursor += this.context.measureText(character).width + spacing
    }
  }
}

function normalizeControl(payload, masterDurationSeconds) {
  const control = requiredRecord(payload, 'control response')
  if (!CONTROL_STATES.has(control.state)) {
    throw new RuntimeFailure('CONTROL_INVALID', 'The control response has an invalid state.')
  }
  return Object.freeze({
    state: control.state,
    currentSeconds: boundedNumber(
      control.currentSeconds,
      'control.currentSeconds',
      0,
      masterDurationSeconds + 0.5,
    ),
    revision: typeof control.revision === 'string' || typeof control.revision === 'number'
      ? String(control.revision).slice(0, 120)
      : null,
    jobId: control.jobId === undefined || control.jobId === null
      ? null
      : canonicalUuid(control.jobId, 'control.jobId', 'CONTROL_INVALID'),
  })
}

class ControlClock {
  constructor(config, onControl, onWarning) {
    this.config = config
    this.onControl = onControl
    this.onWarning = onWarning
    this.control = Object.freeze({ state: 'idle', currentSeconds: 0, revision: null })
    this.receivedMilliseconds = performance.now()
    this.failureCount = 0
    this.timer = null
    this.polling = false
  }

  async start() {
    await this.poll()
    this.timer = window.setInterval(() => { void this.poll() }, this.config.control.pollMilliseconds)
  }

  async poll() {
    if (this.polling) return
    this.polling = true
    try {
      const next = normalizeControl(
        await fetchJson(CONTROL_URL, 'CONTROL_UNAVAILABLE'),
        this.config.masterDurationSeconds,
      )
      const previous = this.control
      this.control = next
      this.receivedMilliseconds = performance.now()
      this.failureCount = 0
      this.onControl(next, previous)
    } catch (error) {
      this.failureCount += 1
      if (this.failureCount === 4) this.onWarning(error)
    } finally {
      this.polling = false
    }
  }

  secondsAt(nowMilliseconds) {
    const elapsed = this.control.state === 'playing'
      ? Math.max(0, nowMilliseconds - this.receivedMilliseconds) / 1000
      : 0
    return clamp(
      this.control.currentSeconds + elapsed,
      0,
      this.config.masterDurationSeconds,
    )
  }

  stop() {
    if (this.timer !== null) window.clearInterval(this.timer)
    this.timer = null
  }
}

function deterministicUnit(seed, salt) {
  let value = (seed ^ Math.imul((salt | 0) + 1, 0x9e3779b1)) >>> 0
  value ^= value << 13
  value ^= value >>> 17
  value ^= value << 5
  return (value >>> 0) / 4_294_967_295
}

class PropagationWave {
  constructor(seed) {
    this.seed = seed
    this.startedAt = -100
    this.lastSeconds = 0
    this.origin = [0.5, 0.5]
    this.strength = 0
  }

  update(seconds, transient) {
    if (seconds + 0.05 < this.lastSeconds) {
      this.startedAt = -100
      this.strength = 0
    }
    this.lastSeconds = seconds
    if (transient > 0.24 && seconds - this.startedAt > 0.20) {
      const timelineBucket = Math.max(0, Math.round(seconds * 240))
      this.origin = [
        0.12 + deterministicUnit(this.seed, timelineBucket) * 0.76,
        0.12 + deterministicUnit(this.seed, timelineBucket + 1013) * 0.76,
      ]
      this.startedAt = seconds
      this.strength = transient
    }
    const age = seconds - this.startedAt
    return {
      origin: this.origin,
      age,
      strength: age >= 0 && age < 2.5 ? this.strength : 0,
    }
  }
}

function resolveChoreography(config, seconds) {
  const entries = config.choreography
  if (seconds < entries[0].startSeconds) {
    return {
      sourceShape: entries[0].sourceShape,
      targetShape: entries[0].targetShape,
      morph: 0,
      section: entries[0].section,
    }
  }
  let settledShape = entries[0].sourceShape
  let settledSection = entries[0].section
  for (const entry of entries) {
    if (seconds < entry.startSeconds) break
    const endSeconds = entry.startSeconds + entry.durationSeconds
    if (seconds < endSeconds) {
      const linearProgress = clamp((seconds - entry.startSeconds) / entry.durationSeconds)
      return {
        sourceShape: entry.sourceShape,
        targetShape: entry.targetShape,
        morph: EASINGS[entry.easing](linearProgress),
        section: entry.section,
      }
    }
    settledShape = entry.targetShape
    settledSection = entry.section
  }
  return {
    sourceShape: settledShape,
    targetShape: settledShape,
    morph: 1,
    section: settledSection,
  }
}

function resolveCompositionEnvelope(composition, seconds) {
  if (!Number.isFinite(seconds)) {
    throw new RuntimeFailure('CONFIG_INVALID', 'The composition clock must be finite.')
  }
  const points = composition.envelope
  const time = clamp(seconds, points[0].timeSeconds, points[points.length - 1].timeSeconds)
  let first = points[0]
  let last = first
  for (let index = 1; index < points.length; index += 1) {
    last = points[index]
    if (time <= last.timeSeconds) break
    first = last
  }
  const progress = first === last
    ? 0
    : EASINGS.smootherstep(clamp((time - first.timeSeconds) / (last.timeSeconds - first.timeSeconds)))
  return Object.fromEntries(['density', 'brightness', 'scale', 'deformation'].map((key) => [
    key,
    first[key] + (last[key] - first[key]) * progress,
  ]))
}

class ShapeLaboratory {
  constructor(config) {
    this.enabled = config.developerLab.enabled
    this.config = config
    this.element = document.querySelector('#lab')
    this.sourceSelect = document.querySelector('#lab-source-shape')
    this.targetSelect = document.querySelector('#lab-target-shape')
    this.morphInput = document.querySelector('#lab-morph')
    this.morphOutput = document.querySelector('#lab-morph-output')
    this.audioSelect = document.querySelector('#lab-audio-mode')
    this.resetButton = document.querySelector('#lab-reset')
    const first = config.choreography[0]
    const preview = config.developerLab.previewOverride
    this.defaults = Object.freeze({
      sourceShape: preview?.sourceShape || first.sourceShape,
      targetShape: preview?.targetShape || first.targetShape,
      morph: preview?.morph ?? 0,
      audioMode: preview?.audioMode || 'simulated',
    })
    this.state = { ...this.defaults }
    if (this.enabled) this.initialize()
  }

  initialize() {
    for (const shapeId of this.config.trustedShapes) {
      for (const select of [this.sourceSelect, this.targetSelect]) {
        const option = document.createElement('option')
        option.value = shapeId
        option.textContent = SHAPE_LABELS[shapeId]
        select.append(option)
      }
    }
    this.element.hidden = false
    this.sourceSelect.addEventListener('change', () => { this.state.sourceShape = this.sourceSelect.value })
    this.targetSelect.addEventListener('change', () => { this.state.targetShape = this.targetSelect.value })
    this.morphInput.addEventListener('input', () => {
      this.state.morph = clamp(Number(this.morphInput.value))
      this.morphOutput.value = `${Math.round(this.state.morph * 100)}%`
    })
    this.audioSelect.addEventListener('change', () => { this.state.audioMode = this.audioSelect.value })
    this.resetButton.addEventListener('click', () => this.reset())
    this.reset()
  }

  reset() {
    this.state = { ...this.defaults }
    this.sourceSelect.value = this.state.sourceShape
    this.targetSelect.value = this.state.targetShape
    this.morphInput.value = String(this.state.morph)
    this.morphOutput.value = '0%'
    this.audioSelect.value = this.state.audioMode
  }

  override(timelineState) {
    if (!this.enabled) return {
      ...timelineState,
      audioMode: this.config.mode === 'preview'
        ? this.config.developerLab.previewOverride?.audioMode || 'live'
        : 'live',
    }
    return {
      sourceShape: this.state.sourceShape,
      targetShape: this.state.targetShape,
      morph: this.state.morph,
      section: 'developer-lab',
      audioMode: this.state.audioMode,
    }
  }
}

class RuntimeTelemetry {
  constructor(config) {
    this.config = config
    this.startedMilliseconds = performance.now()
    this.intervalStartedMilliseconds = null
    this.lastFrameMilliseconds = null
    this.intervalFrames = 0
    this.intervalFrameCount = 0
    this.intervalRenderMilliseconds = 0
    this.intervalMaxRenderMilliseconds = 0
    this.intervalFrameMilliseconds = 0
    this.totalDroppedUpdates = 0
    this.totalRenderedFrames = 0
  }

  record(frameMilliseconds, renderMilliseconds) {
    if (this.intervalStartedMilliseconds === null) {
      this.intervalStartedMilliseconds = frameMilliseconds
    }
    if (this.lastFrameMilliseconds !== null) {
      const interval = frameMilliseconds - this.lastFrameMilliseconds
      this.intervalFrameMilliseconds += interval
      this.intervalFrameCount += 1
      const expected = 1000 / this.config.targetFps
      this.totalDroppedUpdates += Math.max(0, Math.round(interval / expected) - 1)
    }
    this.lastFrameMilliseconds = frameMilliseconds
    this.intervalFrames += 1
    this.totalRenderedFrames += 1
    this.intervalRenderMilliseconds += renderMilliseconds
    this.intervalMaxRenderMilliseconds = Math.max(this.intervalMaxRenderMilliseconds, renderMilliseconds)
  }

  due(nowMilliseconds) {
    return this.intervalStartedMilliseconds !== null
      && nowMilliseconds - this.intervalStartedMilliseconds >= this.config.control.telemetryIntervalMilliseconds
  }

  snapshot(nowMilliseconds, seconds, geometryState, renderer) {
    const frames = this.intervalFrames
    const averageFrameInterval = this.intervalFrameMilliseconds / Math.max(1, this.intervalFrameCount)
    const report = {
      timelineSeconds: Number(seconds.toFixed(6)),
      section: geometryState.section,
      sourceShape: geometryState.sourceShape,
      targetShape: geometryState.targetShape,
      morph: Number(geometryState.morph.toFixed(6)),
      rendererFps: Number((1000 / Math.max(0.001, averageFrameInterval)).toFixed(3)),
      averageFrameIntervalMs: Number(averageFrameInterval.toFixed(3)),
      averageRenderTimeMs: Number((this.intervalRenderMilliseconds / Math.max(1, frames)).toFixed(3)),
      maximumRenderTimeMs: Number(this.intervalMaxRenderMilliseconds.toFixed(3)),
      droppedRendererUpdates: this.totalDroppedUpdates,
      renderedFrames: this.totalRenderedFrames,
      targetFps: this.config.targetFps,
      pointCount: this.config.pointCount,
      canvasWidth: renderer.canvas.width,
      canvasHeight: renderer.canvas.height,
    }
    this.intervalStartedMilliseconds = nowMilliseconds
    this.intervalFrames = 0
    this.intervalFrameCount = 0
    this.intervalRenderMilliseconds = 0
    this.intervalMaxRenderMilliseconds = 0
    this.intervalFrameMilliseconds = 0
    return report
  }
}

function configurePresentation(config) {
  document.body.dataset.runtimeMode = config.mode
  document.querySelector('#runtime-status').hidden = config.mode !== 'preview'
  document.querySelector('#fatal-error').hidden = true
  document.querySelector('#lab').hidden = !config.developerLab.enabled
}

function setStatus(message, settled = false) {
  const status = document.querySelector('#runtime-status')
  status.hidden = activeConfig?.mode !== 'preview'
  document.querySelector('#runtime-status-text').textContent = message
  status.classList.toggle('is-settled', settled)
}

let fatalShown = false

async function showFatal(error) {
  if (fatalShown) return
  fatalShown = true
  const failure = error instanceof RuntimeFailure
    ? error
    : new RuntimeFailure('RUNTIME_ERROR', 'The geometry runtime encountered an unexpected error.')
  document.querySelector('#fatal-error-message').textContent = failure.message
  document.querySelector('#fatal-error').hidden = activeConfig?.mode !== 'preview'
  setStatus('Geometry unavailable')
  await postRuntimeEvent('error', {
    code: failure.code,
    message: failure.message.slice(0, 400),
    detail: typeof failure.detail === 'string' ? failure.detail.slice(0, 1600) : null,
  })
}

async function main() {
  assertLoopbackRuntime()
  const config = normalizeConfig(await fetchJson(CONFIG_URL, 'CONFIG_UNAVAILABLE'))
  activeConfig = config
  initializeSessionIdentity(config)
  document.body.style.backgroundColor = config.palette.background
  configurePresentation(config)

  const [vertexSource, fragmentSource] = await Promise.all([
    fetchText('./shaders/neopixel.vert.glsl', 'SHADER_SOURCE_UNAVAILABLE'),
    fetchText('./shaders/neopixel.frag.glsl', 'SHADER_SOURCE_UNAVAILABLE'),
  ])
  const canvas = document.querySelector('#geometry-canvas')
  const renderer = new GeometryRenderer(canvas, config, vertexSource, fragmentSource)
  const identity = new IdentityOverlay(document.querySelector('#identity-canvas'), config)
  await identity.ready()
  const lab = new ShapeLaboratory(config)
  const propagation = new PropagationWave(config.seed)
  const telemetry = new RuntimeTelemetry(config)
  let startedReported = false
  let endedReported = false
  let stopped = false
  let runtimeReady = false

  const audio = new AudioReactiveEngine(config, (warning) => {
    const failure = warning instanceof RuntimeFailure
      ? warning
      : new RuntimeFailure('WEB_AUDIO_UNAVAILABLE', 'Web Audio response is unavailable.')
    void postRuntimeEvent('error', {
      code: failure.code,
      message: failure.message,
      recoverable: true,
    })
  })

  const clock = new ControlClock(
    config,
    (next, previous) => {
      if (!acceptControlSessionIdentity(next.jobId)) {
        void postRuntimeEvent('error', {
          code: 'CONTROL_IDENTITY_MISMATCH',
          message: 'The control session identity did not match the launch URL.',
          recoverable: true,
        })
      }
      void audio.synchronize(next)
      if (runtimeReady && next.state === 'playing' && previous.state !== 'playing' && !startedReported) {
        startedReported = true
        void postRuntimeEvent('started', {
          timelineSeconds: next.currentSeconds,
          controlRevision: next.revision,
        })
      }
      if (runtimeReady && next.state === 'ended' && !endedReported) {
        endedReported = true
        void postRuntimeEvent('ended', {
          timelineSeconds: next.currentSeconds,
          reason: 'control-ended',
        })
      }
    },
    (warning) => {
      setStatus('Control channel reconnecting…')
      const failure = warning instanceof RuntimeFailure
        ? warning
        : new RuntimeFailure('CONTROL_UNAVAILABLE', 'The local control channel is unavailable.')
      void postRuntimeEvent('error', {
        code: failure.code,
        message: failure.message,
        recoverable: true,
      })
    },
  )

  canvas.addEventListener('webglcontextlost', (event) => {
    event.preventDefault()
    stopped = true
    void showFatal(new RuntimeFailure('GPU_CONTEXT_LOST', 'The WebGL2 context was lost during rendering.'))
  })
  window.addEventListener('resize', () => {
    renderer.resize()
    identity.resize()
  }, { passive: true })

  await clock.start()

  const initialSeconds = clock.secondsAt(performance.now())
  const initialTimeline = lab.override(resolveChoreography(config, initialSeconds))
  renderer.draw({
    seconds: initialSeconds,
    ...initialTimeline,
    audio: { low: 0, mid: 0, high: 0, energy: 0, transient: 0 },
    propagation: { origin: [0.5, 0.5], age: -1, strength: 0 },
  })
  identity.draw(audio, { low: 0, mid: 0, high: 0, energy: 0, transient: 0 }, initialSeconds, initialTimeline.section)
  await postRuntimeEvent('ready', {
    mode: config.mode,
    designJobId: config.jobId,
    sessionIdentitySource: querySessionJobId !== null
      ? 'sessionJobId-query'
      : clock.control.jobId !== null
        ? 'control-api'
        : 'standalone-config-fallback',
    pointCount: config.pointCount,
    pointDomain: config.pointDomain,
    targetFps: config.targetFps,
    trustedShapes: config.trustedShapes,
    capabilities: renderer.capabilityReport(),
  })
  runtimeReady = true
  if (clock.control.state === 'playing' && !startedReported) {
    startedReported = true
    void postRuntimeEvent('started', {
      timelineSeconds: clock.control.currentSeconds,
      controlRevision: clock.control.revision,
    })
  } else if (clock.control.state === 'ended' && !endedReported) {
    endedReported = true
    void postRuntimeEvent('ended', {
      timelineSeconds: clock.control.currentSeconds,
      reason: 'control-ended',
    })
  }
  setStatus('Geometry ready')
  window.setTimeout(() => setStatus('Geometry ready', true), 1400)

  const targetInterval = 1000 / config.targetFps
  let lastRenderedMilliseconds = performance.now() - targetInterval

  const renderFrame = (nowMilliseconds) => {
    if (stopped || fatalShown) return
    window.requestAnimationFrame(renderFrame)
    const elapsedMilliseconds = nowMilliseconds - lastRenderedMilliseconds
    if (elapsedMilliseconds < targetInterval * 0.88) return
    const deltaSeconds = clamp(elapsedMilliseconds / 1000, 0.001, 0.25)
    lastRenderedMilliseconds = nowMilliseconds

    const controlledSeconds = clock.secondsAt(nowMilliseconds)
    const seconds = lab.enabled && clock.control.state === 'idle'
      ? (nowMilliseconds / 1000) % config.masterDurationSeconds
      : controlledSeconds
    const geometryState = lab.override(resolveChoreography(config, seconds))
    const audioMode = geometryState.audioMode || 'live'
    const audioFeatures = audio.features(seconds, deltaSeconds, audioMode)
    identity.draw(audio, audioFeatures, seconds, geometryState.section)
    const wave = propagation.update(seconds, audioFeatures.transient)
    const renderStarted = performance.now()
    try {
      renderer.draw({
        seconds,
        ...geometryState,
        audio: audioFeatures,
        propagation: wave,
      })
    } catch (error) {
      stopped = true
      void showFatal(error)
      return
    }
    telemetry.record(nowMilliseconds, performance.now() - renderStarted)
    if (telemetry.due(nowMilliseconds)) {
      void postRuntimeEvent(
        'telemetry',
        telemetry.snapshot(nowMilliseconds, seconds, geometryState, renderer),
      )
    }
  }
  window.requestAnimationFrame(renderFrame)

  window.addEventListener('pagehide', () => {
    stopped = true
    clock.stop()
    audio.stop()
    if (!endedReported) {
      endedReported = true
      void postRuntimeEvent('ended', {
        timelineSeconds: clock.secondsAt(performance.now()),
        reason: 'runtime-pagehide',
      })
    }
  }, { once: true })
}

window.addEventListener('error', (event) => {
  if (event.error) void showFatal(event.error)
})
window.addEventListener('unhandledrejection', (event) => {
  void showFatal(event.reason)
})

void main().catch((error) => { void showFatal(error) })
