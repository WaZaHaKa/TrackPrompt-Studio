# Trip to Andromeda render calibration results - 2026-07-20

This is the measured handoff for calibration `cal-20260720T154218Z-e4e99950`. It is evidence, not production authorization. Every generated profile remains `pending-operator-approval`, and no complete timeline was started.

## Identity and machine

- Approved scene SHA-256: `225EE7124B62434FF66D68E2477E5523C99914C76D7304366B0EBB696E0EFED5`
- Machine fingerprint: `226D7069C0B257E1B31BC1BB323D157D2EC1FA7ED03482653A5B26793CB63C97`
- Machine ID: `226d7069c0b2`
- GPU: NVIDIA GeForce RTX 3060, 12,288 MiB VRAM, driver 610.62
- CPU: AMD Ryzen 7 3700X, 8 physical cores / 16 logical processors
- RAM: 34,282,479,616 bytes
- Blender: 5.2.0 LTS
- Evidence: `test-output\render-calibration\226d7069c0b2\225ee7124b62\cal-20260720T154218Z-e4e99950`

The exact approved `.blend` remained unchanged. The original `4k-30-sdr-ultra.json` also remained separate and has saved-file SHA-256 `58F642759CABBB5E96ACA0C7D148BC0BC13F18743580ED08C74DA75A81C2E899`.

## Method

The staged matrix contained 73 candidates across native 720p, 1080p, 1440p, and 4K; 8/16/24/32 samples where relevant; PNG8/PNG16; compression 0/5/15; and profile-only shadow-pool variants. Dominated configurations stopped after screening stills. Finalists rendered the six required stills plus both 30-frame production ranges, with the first frame of each range excluded from warm timing.

Human review covered every finalist still and both temporal ranges. PNG compression 15 is lossless and materially reduced storage without a measured throughput penalty. The approved Blender Fog Glow, AgX look, composition, animation, camera, materials, and cue response were retained.

## Fully measured finalists

| Profile | Resolution | Samples | Shadow MiB | Output | Warm median | P90 | Frames/hour | Median frame | Full time | Projected sequence | Gate |
|---|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|---|
| `cal-720p-s8-png8-c15` | 1280x720 | 8 | 256 | PNG8 RGB c15 | 1.393845 s | 1.627856 s | 2,582.784 | 621,106 B | 5.045 h | 10.009 GiB | PASS WITH DOCUMENTED CAVEAT |
| `cal-720p-s16-png8-c15` | 1280x720 | 16 | 256 | PNG8 RGB c15 | 2.412049 s | 3.194463 s | 1,492.507 | 612,932 B | 8.730 h | 9.908 GiB | PASS |
| `cal-1080p-s8-png8-c15` | 1920x1080 | 8 | 256 | PNG8 RGB c15 | 2.740995 s | 3.061020 s | 1,313.391 | 1,316,770 B | 9.920 h | 21.236 GiB | FAIL - shadow overflow |
| `cal-1080p-s8-png8-c15-sp512` | 1920x1080 | 8 | 512 | PNG8 RGB c15 | 2.753354 s | 3.119807 s | 1,307.496 | 1,316,770 B | 9.965 h | 21.238 GiB | PASS WITH DOCUMENTED CAVEAT |
| `cal-1080p-s16-png8-c15-sp512` | 1920x1080 | 16 | 512 | PNG8 RGB c15 | 4.627476 s | 6.376322 s | 777.962 | 1,300,460 B | 16.748 h | 20.976 GiB | PASS |
| `cal-1440p-s16-png8-c15-sp512` | 2560x1440 | 16 | 512 | PNG8 RGB c15 | 8.418406 s | 9.316596 s | 427.634 | 2,237,228 B | 30.468 h | 35.907 GiB | PASS |
| `cal-4k-s16-png8-c15-sp512` | 3840x2160 | 16 | 512 | PNG8 RGB c15 | 16.880837 s | 22.226084 s | 213.260 | 4,841,859 B | 61.095 h | 77.617 GiB | FAIL - shadow overflow |
| `cal-4k-s16-png8-c15-sp1024` | 3840x2160 | 16 | 1024 | PNG8 RGB c15 | 16.456161 s | 18.780133 s | 218.763 | 4,841,860 B | 59.558 h | 77.294 GiB | PASS WITH DOCUMENTED CAVEAT |

The 720p and 1080p 8-sample candidates have a modest fine-detail smoothness caveat in the dense cyan energy shell. Hero readability, rails, stars, glow, gradients, color, composition, and temporal continuity passed. The technically failing 1080p/256 MiB and 4K/512 MiB profiles were rejected because later frames exceeded their EEVEE shadow pools.

## Generated profiles

| Purpose | Saved profile | Saved-file SHA-256 | Chunk | Expected / conservative | Minimum free |
|---|---|---|---:|---:|---:|
| Recommended and 720p hyper | `render-profiles\trip-to-andromeda\trip-to-andromeda-calibrated-recommended.json` | `8DAC222ACC7D311216A4F4A3E688B773EF99E8094403DA942FE08A518BC49001` | 600 | 5.045 / 5.891 h | 24 GiB |
| Native 720p reusable | `render-profiles\trip-to-andromeda\trip-to-andromeda-720p-hyper-optimized.json` | `DB27AA9DE2939ACA78819B58BB08C7DB408EED7092E83FA327363EE094779BF0` | 600 | 5.045 / 5.891 h | 24 GiB |
| 1080p release option | `render-profiles\trip-to-andromeda\trip-to-andromeda-1080p-recommended-calibrated.json` | `FFC75A909726AE6DBCC3D0341F1A3063436332BF2900C47EA8555F8754C035AB` | 325 | 9.965 / 11.291 h | 48 GiB |
| 1440p balanced | `render-profiles\trip-to-andromeda\trip-to-andromeda-1440p-balanced-calibrated.json` | `5CA0AA506AE17F50B4E405F11591C7F35B97E4A97448AAAFC8178279D87298DA` | 105 | 30.468 / 33.718 h | 80 GiB |
| 4K balanced | `render-profiles\trip-to-andromeda\trip-to-andromeda-4k-balanced-optimized.json` | `6A7C098CC122B9DBCB8D186C9A0A8DCDA52339249885A2B841ACB2F9918837DB` | 55 | 59.558 / 67.968 h | 170 GiB |

The recommended pointer is `render-profiles\trip-to-andromeda\recommended-profile.json`, saved-file SHA-256 `1FE2BE675A5CFF74E732069E6101AC149544501380948DBF1EFB0BF648421F0A`.

## Exclusive Performance Mode A/B

Frames 7065-7094 were repeated with the same candidate. The normal run measured 1.345630 s warm median and 2,675.326 frames/hour. High Performance power plan plus High Blender priority measured 1.071950 s and 3,358.364 frames/hour: a 25.531% throughput improvement in this bounded range. The prior Balanced plan was restored, sleep inhibition was released, zero services were paused, and no Blender process remained. Evidence is in `performance-ab\performance-comparison.json` beneath the calibration directory.

## Validation worker-count A/B

The existing 450 published 1080p frames were scanned in inspection-only mode with the exact saved profile and approved scene. This benchmark performed PNG validation and SHA-256 work only; it did not initialize output, start Blender, or change any frame. Four workers delivered the highest observed validation throughput and remains the production default.

| Validation workers | Elapsed | Validated frames/hour |
|---:|---:|---:|
| 2 | 6.1020 s | 265,486.3 |
| 4 | 2.4173 s | 670,156.7 |
| 6 | 2.7056 s | 598,765.8 |
| 8 | 2.8778 s | 562,928.1 |

## Sanitized remote smoke

The provider-neutral package is `render-packages\trip-to-andromeda\225ee7124b62\8dac222acc7d\package`. Its package ID is `pkg-1447cb6531a8-31dea3f11155` and package SHA-256 is `00E66F1F7748C789864DF22F842BF7B82A843E55C7A50003BE6C0D27D6FC2D1E`.

The package passed validation after a one-frame clean-environment render without source audio. The package stayed immutable after rendering, contained no Python bytecode or private source strings, and reported `privateAudioIncluded: false`. The local and sanitized frame files used different PNG container bytes, but FFmpeg decoded both to the identical pixel SHA-256 `5662cf0406bed23d28feedde066dfa5fef9e5baf4b4a88960cd869af4f662f35`.
