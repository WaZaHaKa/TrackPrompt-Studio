#version 300 es

precision highp float;

in vec3 vColor;
in float vBrightness;
in float vDepthFade;
in float vPointIdentity;
in float vHaloRetention;

out vec4 outColor;

void main() {
  vec2 centered = gl_PointCoord * 2.0 - 1.0;
  float radius = length(centered);
  if (radius > 1.0) {
    discard;
  }

  float core = 1.0 - smoothstep(0.0, 0.42, radius);
  float halo = (1.0 - smoothstep(0.14, 1.0, radius))
    * (0.30 + vPointIdentity * 0.10) * vHaloRetention;
  float edge = 1.0 - smoothstep(0.72, 1.0, radius);
  float brightness = max(0.0, vBrightness) * max(0.0, vDepthFade);
  vec3 color = vColor * (halo * 0.64 + core * 1.78) * brightness;
  float alpha = (halo * 0.34 + core * 0.92) * edge * clamp(brightness, 0.0, 1.5);
  outColor = vec4(color, alpha);
}
