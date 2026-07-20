# Cloud render privacy

The approved artistic scene may be disclosed to a cloud VM, but private music
and analysis data must remain local.

## Sanitization boundary

The established remote exporter copies the approved scene, requires baked
`TP_AUDIO_BUS` animation, removes sequencer sound strips and sound datablocks,
removes known private custom properties from the data blocks it inspects,
packs/relativizes visual assets, scrubs the copied profile, and saves a separate
sanitized `.blend`. It never overwrites the approved source `.blend`.

The intended package exclusion contract covers source audio, audio paths,
lyrics, transcripts, cue sheets, analysis JSON, prompts, model caches,
credentials, provider tokens, local absolute paths, and unrelated files.
Structural package validation checks the manifest and packaged files, but it is
not a proof that arbitrary private text could not be embedded inside a Blender
datablock the sanitizer does not inspect. Review the sanitization report and
package contents before disclosure.

`CREATE SANITIZED PACKAGE` currently emits the established remote-package
schema. Convert it to the provider-neutral sealed manifest with the offline
`prepare-manifest` command, then run `validate-manifest`; exact commands are in
[`cloud-rendering.md`](cloud-rendering.md). Conversion and validation do not
upload anything.

## Visual-equivalence gate

The current exporter performs a sanitized clean-environment smoke frame. It
does **not** automatically render the matching approved-source frames or compute
an original-versus-sanitized comparison. Therefore a newly generated package is
not live-cloud-ready merely because its structural validation succeeds.

The current package at
`render-packages\trip-to-andromeda\225ee7124b62\8dac222acc7d\package` does have
package-specific bounded comparison evidence. It passed validation without
source audio, remained immutable after its clean-environment smoke, reported
`privateAudioIncluded: false`, and contained no detected Python bytecode or
private source strings. The approved and sanitized PNG container bytes differed,
but FFmpeg decoded both comparison frames to the identical pixel SHA-256:

```text
5662cf0406bed23d28feedde066dfa5fef9e5baf4b4a88960cd869af4f662f35
```

That evidence is bound to package ID `pkg-1447cb6531a8-31dea3f11155` and package
SHA-256
`00E66F1F7748C789864DF22F842BF7B82A843E55C7A50003BE6C0D27D6FC2D1E`.
It does not automatically transfer to a rebuilt package, another profile, or
another scene.

Before any cloud disclosure, render bounded matching frames from both the
approved and sanitized scenes, inspect composition/color/compositor/Fog Glow
and thin-detail behavior, record the verdict, and reject any material mismatch,
unless the exact package identities match the recorded evidence above. There is
not yet a single automated A/B command in Mission Control; the generic exporter
does not create this comparison evidence for a new package.

## Credentials and generated artifacts

Credentials should use the provider CLI's existing authentication or
short-lived environment-backed credentials. Never commit Brev tokens, SSH
keys, storage secrets, private endpoints, sanitized packages, returned frames,
worker logs containing secrets, or source audio. Inspect logs before sharing.

Python currently provides a video-only encode **plan** and a local-audio mux
**plan**; it does not execute either. Mission Control's existing executable
path encodes from a complete verified local frame sequence and muxes the private
audio locally. It does not yet consume a downloaded cloud video-only master.
