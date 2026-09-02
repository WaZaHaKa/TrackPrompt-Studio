#version 300 es

precision highp float;
precision highp int;

uniform ivec2 uPointDomain;
uniform int uSourceShape;
uniform int uTargetShape;
uniform float uMorph;
uniform float uTime;
uniform float uSeedPhase;
uniform float uPointSize;
uniform float uGlobalScale;
uniform float uShapeScale;
uniform float uDepthStrength;
uniform vec2 uFrameCenter;
uniform vec4 uEnvelope;
uniform float uMasterDuration;
uniform vec4 uAudio;
uniform float uTransient;
uniform vec2 uImpulseOrigin;
uniform float uImpulseAge;
uniform vec3 uPalettePrimary;
uniform vec3 uPaletteSecondary;
uniform vec3 uPropagation;
uniform vec4 uReadabilityZone0;
uniform vec4 uReadabilityZone1;
uniform vec2 uReadabilityStrength;
uniform float uReadabilityMinimum;
uniform float uHaloSuppression;
uniform mat4 uView;
uniform mat4 uProjection;

out vec3 vColor;
out float vBrightness;
out float vDepthFade;
out float vPointIdentity;
out float vHaloRetention;

const float PI = 3.14159265358979323846;
const float TAU = 6.28318530717958647692;

float saturate(float value) {
  return clamp(value, 0.0, 1.0);
}

float hash11(float value) {
  return fract(sin(value * 127.1 + uSeedPhase * 311.7) * 43758.5453123);
}

vec3 matrixPlane(vec2 uv) {
  vec2 q = uv * 2.0 - 1.0;
  return vec3(q.x * 1.16, q.y * 0.68, 0.025 * sin((q.x + q.y) * PI * 4.0));
}

vec3 waveSurface(vec2 uv) {
  vec2 q = uv * 2.0 - 1.0;
  float wave = sin(q.x * PI * 3.0 + uTime * 0.22) * cos(q.y * PI * 2.0 - uTime * 0.17);
  return vec3(q.x * 1.12, q.y * 0.70, wave * 0.30);
}

vec3 cylinderSurface(vec2 uv) {
  float angle = TAU * uv.x;
  float y = (uv.y * 2.0 - 1.0) * 0.82;
  float radius = 0.72 + 0.06 * sin(y * PI * 3.0);
  return vec3(cos(angle) * radius, y, sin(angle) * radius);
}

vec3 torusSurface(vec2 uv) {
  float majorAngle = TAU * uv.x;
  float minorAngle = TAU * uv.y;
  float majorRadius = 0.70;
  float minorRadius = 0.27;
  float ring = majorRadius + minorRadius * cos(minorAngle);
  return vec3(ring * cos(majorAngle), minorRadius * sin(minorAngle), ring * sin(majorAngle));
}

vec3 twistedTorus(vec2 uv) {
  float majorAngle = TAU * uv.x;
  float minorAngle = TAU * uv.y + majorAngle * 3.0;
  float majorRadius = 0.68;
  float minorRadius = 0.25 * (0.82 + 0.18 * sin(majorAngle * 5.0));
  float ring = majorRadius + minorRadius * cos(minorAngle);
  return vec3(ring * cos(majorAngle), minorRadius * sin(minorAngle), ring * sin(majorAngle));
}

vec3 helixTube(vec2 uv) {
  float t = uv.x * TAU * 2.6 - PI * 2.6;
  float tubeAngle = TAU * uv.y;
  vec3 center = vec3(cos(t) * 0.54, t * 0.092, sin(t) * 0.54);
  vec3 radial = normalize(vec3(cos(t), 0.0, sin(t)));
  vec3 vertical = vec3(0.0, 1.0, 0.0);
  return center + (radial * cos(tubeAngle) + vertical * sin(tubeAngle)) * 0.105;
}

vec3 doubleHelix(vec2 uv) {
  float branch = step(0.5, uv.y);
  float localV = fract(uv.y * 2.0);
  float t = uv.x * TAU * 2.4 - PI * 2.4;
  float phase = branch * PI;
  float tubeAngle = TAU * localV;
  vec3 center = vec3(cos(t + phase) * 0.50, t * 0.10, sin(t + phase) * 0.50);
  vec3 radial = normalize(vec3(cos(t + phase), 0.0, sin(t + phase)));
  vec3 vertical = vec3(0.0, 1.0, 0.0);
  return center + (radial * cos(tubeAngle) + vertical * sin(tubeAngle)) * 0.075;
}

vec3 lissajousTube(vec2 uv) {
  float t = TAU * uv.x;
  float width = (uv.y - 0.5) * 0.16;
  vec3 center = vec3(sin(3.0 * t + PI * 0.5), sin(2.0 * t), sin(5.0 * t) * 0.46) * 0.70;
  vec3 ribbon = normalize(vec3(cos(t), sin(t * 2.0), cos(t * 3.0)));
  return center + ribbon * width;
}

vec3 roseRibbon(vec2 uv) {
  float angle = TAU * uv.x;
  float petal = cos(5.0 * angle);
  float width = (uv.y - 0.5) * 0.17;
  float radius = 0.73 * petal + width;
  return vec3(cos(angle) * radius, sin(angle) * radius, sin(angle * 5.0) * width * 1.6);
}

vec3 hypotrochoidRibbon(vec2 uv) {
  float t = TAU * uv.x;
  float outer = 5.0;
  float inner = 3.0;
  float distance = 4.2;
  float x = (outer - inner) * cos(t) + distance * cos((outer - inner) / inner * t);
  float y = (outer - inner) * sin(t) - distance * sin((outer - inner) / inner * t);
  float width = (uv.y - 0.5) * 0.15;
  return vec3(x * 0.15, y * 0.15, width + 0.08 * sin(t * 6.0));
}

vec3 torusKnotTube(vec2 uv) {
  float t = TAU * uv.x;
  float tubeAngle = TAU * uv.y;
  float radius = 0.56 + 0.22 * cos(3.0 * t);
  vec3 center = vec3(radius * cos(2.0 * t), 0.22 * sin(3.0 * t), radius * sin(2.0 * t));
  vec3 radial = normalize(vec3(cos(2.0 * t), 0.35 * sin(3.0 * t), sin(2.0 * t)));
  vec3 binormal = normalize(cross(radial, vec3(0.0, 1.0, 0.0)) + vec3(0.0, 0.001, 0.0));
  return center + (radial * cos(tubeAngle) + binormal * sin(tubeAngle)) * 0.085;
}

vec3 sphericalLattice(vec2 uv) {
  float longitude = TAU * uv.x;
  float latitude = acos(clamp(1.0 - 2.0 * uv.y, -1.0, 1.0));
  float radius = 0.72 + 0.035 * sin(longitude * 8.0) * sin(latitude * 7.0);
  return vec3(
    radius * sin(latitude) * cos(longitude),
    radius * cos(latitude),
    radius * sin(latitude) * sin(longitude)
  );
}

vec3 mobiusStrip(vec2 uv) {
  float angle = TAU * uv.x;
  float width = (uv.y * 2.0 - 1.0) * 0.30;
  float halfAngle = angle * 0.5;
  float radius = 0.65 + width * cos(halfAngle);
  return vec3(radius * cos(angle), width * sin(halfAngle), radius * sin(angle));
}

vec3 trefoilTube(vec2 uv) {
  float t = TAU * uv.x;
  float width = (uv.y - 0.5) * 0.13;
  vec3 center = vec3(
    sin(t) + 2.0 * sin(2.0 * t),
    cos(t) - 2.0 * cos(2.0 * t),
    -sin(3.0 * t)
  ) * 0.25;
  vec3 ribbon = normalize(vec3(cos(2.0 * t), sin(3.0 * t), cos(t)));
  return center + ribbon * width;
}

float superformulaRadius(float angle) {
  float m = 7.0;
  float n1 = 0.34;
  float n2 = 1.7;
  float n3 = 1.7;
  float partA = pow(abs(cos(m * angle * 0.25)), n2);
  float partB = pow(abs(sin(m * angle * 0.25)), n3);
  return pow(max(partA + partB, 0.0001), -1.0 / n1);
}

vec3 superformulaShell(vec2 uv) {
  // Match the spherical lattice's longitude and north-to-south UV ordering.
  // Opposed directions made persistent corresponding points cancel at the
  // midpoint instead of retaining a coherent, full-size morphing surface.
  float longitude = TAU * uv.x;
  float latitude = (0.5 - uv.y) * PI;
  float radial = clamp(superformulaRadius(longitude), 0.12, 1.25);
  float vertical = clamp(superformulaRadius(latitude), 0.12, 1.25);
  float scale = 0.53;
  return vec3(
    scale * radial * cos(longitude) * vertical * cos(latitude),
    scale * vertical * sin(latitude),
    scale * radial * sin(longitude) * vertical * cos(latitude)
  );
}

vec3 sparseField(vec2 uv) {
  float identity = uv.x * 8191.0 + uv.y * 131071.0;
  vec3 scattered = vec3(
    hash11(identity + 3.0) * 2.0 - 1.0,
    hash11(identity + 19.0) * 2.0 - 1.0,
    hash11(identity + 47.0) * 2.0 - 1.0
  );
  float retain = step(0.56, hash11(identity + 71.0));
  return scattered * vec3(1.08, 0.70, 0.62) * mix(0.18, 1.0, retain);
}

vec3 dispersedField(vec2 uv) {
  vec2 q = uv * 2.0 - 1.0;
  float identity = uv.x * 65537.0 + uv.y * 4099.0;
  vec3 direction = normalize(vec3(
    q.x + (hash11(identity + 5.0) - 0.5) * 0.62,
    q.y + (hash11(identity + 23.0) - 0.5) * 0.62,
    (hash11(identity + 59.0) - 0.5) * 1.25
  ) + vec3(0.0001));
  float radius = 0.48 + hash11(identity + 97.0) * 0.88;
  return direction * radius * vec3(1.18, 0.78, 0.90);
}

vec3 shapePosition(int shapeId, vec2 uv) {
  // Normalize family extents before interpolation. The same indexed points
  // still morph continuously; smaller families no longer collapse the hero.
  if (shapeId == 0) return matrixPlane(uv) * vec3(1.50, 1.20, 1.0);
  if (shapeId == 1) return waveSurface(uv) * vec3(1.50, 1.20, 1.0);
  if (shapeId == 2) return cylinderSurface(uv);
  if (shapeId == 3) return torusSurface(uv) * 1.70;
  if (shapeId == 4) return twistedTorus(uv) * 1.78;
  if (shapeId == 5) return helixTube(uv);
  if (shapeId == 6) return doubleHelix(uv);
  if (shapeId == 7) return lissajousTube(uv) * 1.90;
  if (shapeId == 8) return roseRibbon(uv);
  if (shapeId == 9) return hypotrochoidRibbon(uv);
  if (shapeId == 10) return torusKnotTube(uv);
  if (shapeId == 11) return sphericalLattice(uv) * 1.95;
  if (shapeId == 12) return mobiusStrip(uv);
  if (shapeId == 13) return trefoilTube(uv) * 1.78;
  if (shapeId == 14) return superformulaShell(uv) * 2.30;
  if (shapeId == 15) return sparseField(uv) * vec3(1.55, 1.38, 1.10);
  if (shapeId == 16) return matrixPlane(uv) * vec3(1.50, 1.20, 1.0);
  return dispersedField(uv) * vec3(1.30, 1.22, 1.0);
}

mat3 rotationMatrix(vec3 angles) {
  vec3 sine = sin(angles);
  vec3 cosine = cos(angles);
  mat3 rotateX = mat3(1.0, 0.0, 0.0, 0.0, cosine.x, sine.x, 0.0, -sine.x, cosine.x);
  mat3 rotateY = mat3(cosine.y, 0.0, -sine.y, 0.0, 1.0, 0.0, sine.y, 0.0, cosine.y);
  mat3 rotateZ = mat3(cosine.z, sine.z, 0.0, -sine.z, cosine.z, 0.0, 0.0, 0.0, 1.0);
  return rotateZ * rotateY * rotateX;
}

vec4 framePosition(vec3 position) {
  vec4 clip = uProjection * uView * vec4(position, 1.0);
  clip.xy += vec2(uFrameCenter.x - 0.5, 0.5 - uFrameCenter.y) * 2.0 * clip.w;
  return clip;
}

vec2 screenPosition(vec4 clip) {
  vec2 screen = clip.xy / max(0.0001, clip.w) * 0.5 + 0.5;
  return vec2(screen.x, 1.0 - screen.y);
}

float zoneWeight(vec2 screen, vec4 zone) {
  vec2 distance = (screen - zone.xy) / max(zone.zw, vec2(0.0001));
  return exp(-2.0 * dot(distance, distance));
}

float readabilityRetention(vec2 screen) {
  float first = 1.0 - uReadabilityStrength.x * zoneWeight(screen, uReadabilityZone0);
  float second = 1.0 - uReadabilityStrength.y * zoneWeight(screen, uReadabilityZone1);
  return first * second;
}

void main() {
  int columns = max(1, uPointDomain.x);
  int index = gl_VertexID;
  int row = index / columns;
  int column = index - row * columns;
  vec2 uv = (vec2(float(column), float(row)) + 0.5) / vec2(max(uPointDomain, ivec2(1)));

  vec3 source = shapePosition(uSourceShape, uv);
  vec3 target = shapePosition(uTargetShape, uv);
  vec3 position = mix(source, target, saturate(uMorph));

  float low = uAudio.x;
  float mid = uAudio.y;
  float high = uAudio.z;
  float energy = uAudio.w;
  float identity = float(index) + uSeedPhase * 4096.0;
  float localPhase = hash11(identity) * TAU;

  float deformation = uEnvelope.w;
  position *= uGlobalScale * uShapeScale * uEnvelope.z * (1.0 + low * 0.24 * deformation);
  position.z *= mix(0.62, 1.0, uDepthStrength);

  vec3 rotation = vec3(
    0.46 + 0.10 * sin(uTime * 0.071 + uSeedPhase),
    uTime * 0.018 + sin(uTime * 0.25) * energy * 0.12 * deformation,
    0.055 * sin(uTime * 0.053 + 1.7)
  );
  position = rotationMatrix(rotation) * position;
  float localRetention = readabilityRetention(screenPosition(framePosition(position)));
  float localMotion = mix(0.42, 1.0, localRetention) * deformation;
  position.y += sin(position.x * 3.6 + position.z * 3.0 + uTime * 0.9 + localPhase)
    * mid * 0.15 * localMotion;
  float twist = mid * 0.25 * localMotion * sin(position.y * 1.35 + uTime * 0.12);
  position.xz = mat2(cos(twist), -sin(twist), sin(twist), cos(twist)) * position.xz;

  float impulseDistance = distance(uv, uImpulseOrigin);
  float waveFront = max(0.0, uImpulseAge) * uPropagation.x;
  float frontDistance = (impulseDistance - waveFront) / max(0.0001, uPropagation.z);
  float propagation = exp(-2.0 * frontDistance * frontDistance)
    * exp(-max(0.0, uImpulseAge) * uPropagation.y) * uTransient;
  vec3 outward = normalize(position + vec3(0.0001, 0.0002, 0.0003));
  position += outward * propagation * 0.24 * localMotion;

  vec4 viewPosition = uView * vec4(position, 1.0);
  vec4 clipPosition = framePosition(position);
  gl_Position = clipPosition;

  float perspective = clamp(2.8 / max(0.45, -viewPosition.z), 0.45, 2.15);
  float identityPulse = 0.84 + 0.16 * sin(uTime * (0.7 + high * 0.5) + localPhase);
  gl_PointSize = clamp(uPointSize * perspective * (1.08 + high * 0.28 + propagation * 0.30), 1.0, 18.0);

  float retained = readabilityRetention(screenPosition(clipPosition));
  float safeDim = max(uReadabilityMinimum, retained);
  vHaloRetention = 1.0 - uHaloSuppression * (1.0 - retained);

  vColor = mix(uPalettePrimary, uPaletteSecondary, saturate(uv.y * 0.66 + hash11(identity + 17.0) * 0.34));
  float tailProgress = smoothstep(192.0, uMasterDuration, uTime);
  float tailThreshold = hash11(identity + 149.0);
  float tailKeep = 1.0 - smoothstep(tailThreshold - 0.025, tailThreshold + 0.025, tailProgress);
  float tailVisibility = (1.0 - tailProgress) * tailKeep;
  float densityThreshold = hash11(identity + 211.0);
  float density = uEnvelope.x * (0.94 + energy * 0.06);
  float densityVisibility = smoothstep(densityThreshold - 0.025, densityThreshold + 0.025, density);
  vBrightness = safeDim * identityPulse * (0.70 + energy * 0.62 + high * 0.28 + propagation * 0.80)
    * uEnvelope.y * densityVisibility * tailVisibility;
  vDepthFade = mix(1.0, clamp(1.42 - (-viewPosition.z) * 0.095, 0.42, 1.0), uDepthStrength);
  vPointIdentity = hash11(identity + 91.0);
}
