# Optional models and licenses

## Long-form catalogue milestone

The catalogue, resumable upload, deterministic boundary detector, queue,
mastering comparison, audit journal, and backup/restore tooling add no model and
make no network request. Boundary features use the existing FFmpeg/NumPy stack.
Optional Demucs, CLAP, faster-whisper, and local prompt-writer terms below remain
unchanged and apply only to bounded child analyses when explicitly enabled.

## Shipping and installation status

TrackPrompt Studio's base Fast profile bundles no model weights and never
downloads a model during startup or track analysis. The full-GPU image contains
reviewed runtime packages, but its weights still live only in ignored local
seeds or named Docker volumes.

The Blender Visualizer adds no model, checkpoint, font, texture, HDRI, or other
downloaded asset. Its cue extraction is deterministic NumPy/SciPy DSP and its
single preset uses only procedural Blender geometry and materials. It therefore
does not alter the optional-model installation or license acceptance boundary
documented below.

`setup-full-gpu.ps1` is the explicit installation boundary. A first install
requires `-AcceptAllReviewedModelTerms`; a normal recovery uses
`-SkipModelInstall`, verifies the existing manifests/digests, and performs no
model download. These terms switches record an operator decision, not legal
advice or an independent approval by TrackPrompt Studio.

## Selected full-GPU components

| Capability/model | Pinned selection | Code/runtime license | Weight/model license | Purpose and limitation | Approximate disk impact | Default Fast profile |
| --- | --- | --- | --- | --- | ---: | --- |
| Demucs four-stem separator | `demucs==4.0.1`; local `htdemucs` signature `955717e8`, accepted only through `demucs-models.json` SHA-256 allowlisting | [Demucs repository](https://github.com/facebookresearch/demucs) identifies the code as MIT | **Not clearly established for the pretrained checkpoint by the upstream code license; the operator must review the exact checkpoint/training-data terms independently** | Private local vocals/drums/bass/other separation and coarse relative-energy evidence; not instrument identification | Up to 5 GB conservatively disclosed by the capability API | No |
| CLAP genre similarity | `laion/clap-htsat-unfused` revision `8fa0f1c6d0433df6e97c127f64b2a1d6c0dcda8a`; Transformers `4.53.2` | Transformers and the selected [model card](https://huggingface.co/laion/clap-htsat-unfused) identify Apache-2.0 | Selected model card identifies Apache-2.0 | Artist-free hierarchical audio-text similarity over bounded windows; scores are not calibrated probabilities | About 650 MB | No |
| Local lyrics adapter | `Systran/faster-whisper-small` revision `536b0662742c02347bc0e980a01041f333bce120`; `faster-whisper==1.2.1`; `ctranslate2==4.6.0` | faster-whisper and CTranslate2 repositories identify MIT | Selected [model card](https://huggingface.co/Systran/faster-whisper-small) identifies MIT | Private approximate transcription of the temporary vocal stem; timestamp and hallucination filtering do not make lyrics exact | About 520 MB | No |
| Private prompt writer | `qwen2.5:7b-instruct-q4_K_M`, reviewed Ollama digest prefix `845dbda0ea48`; Ollama image `0.32.0` | [Ollama repository](https://github.com/ollama/ollama) identifies MIT | The selected [Ollama model entry](https://ollama.com/qcwind/qwen2.5-7B-instruct-Q4_K_M) and upstream Qwen2.5 7B Instruct card identify Apache-2.0 | Structured local Creative/Experimental wording from allowlisted evidence; every candidate is validated and can fall back to Reliable deterministic composition | About 4.7 GB | No |

Model cards and repository licenses can be revised independently of this
project. The exact pinned revision/digest and the terms fetched for an install
must be reviewed before accepting the setup switch. If a pinned source no longer
matches this document, stop installation and update the reviewed selection.

## Integrity and privacy boundaries

Demucs seeds stay under the ignored `deep-models/` directory and are copied into
`/data/models/demucs` only by
`python -m app.diagnostics.provision_demucs`. The provisioner verifies the
complete source allowlist, stages a private copy, verifies it again, and replaces
an invalid destination atomically. A valid destination is reused unless
`-ForceDownload` was explicitly supplied.

PyTorch 2.6 and newer default legacy checkpoint loading to weights-only mode,
which Demucs 4.0.1's class-bearing checkpoint cannot use. After the complete
Demucs repository passes the SHA-256 allowlist, TrackPrompt scopes
`TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD=1` to that one offline Demucs child process.
It is not set for the API process, genre/lyrics models, or unknown files. This
permits legacy Python-object deserialization only for the exact operator-reviewed
checkpoint, whose code-execution implications are another reason its source and
hash must be trusted before accepting model terms.

Genre and lyrics models are downloaded only by
`python -m app.diagnostics.install_model` during the reviewed setup flow. Each
destination receives a complete SHA-256 `manifest.json`; any missing, changed,
or unmanifested file keeps that adapter unavailable. The prompt writer is ready
only when Ollama reports the configured model and its digest begins with the
reviewed prefix.

Normal runtime sets model clients to offline mode. The browser cannot trigger a
download, adapters do not silently download, and analysis never receives model-
installation authority. Demucs stems stay private and temporary. Private
transcript artifacts remain local until explicit deletion;
ordinary analysis/export does not expose raw transcripts, model paths, or model
files.

## Direct development and base Deep profile

Installing the Demucs Python package does not establish acceptable checkpoint
terms. After independently reviewing a checkpoint, a direct-development
operator may:

1. install code from `backend/` with
   `python -m pip install -e ".[dev,deep]"`;
2. place only the reviewed repository files under `MODEL_CACHE_DIR/demucs` and
   list every file by relative path and SHA-256 in `demucs-models.json` under the
   selected `DEMUCS_MODEL_NAME`; and
3. set `ENABLE_DEMUCS=true`, restart, and inspect `/api/capabilities` before
   analyzing audio.

The base Docker image intentionally omits optional model packages. The separate
`compose.deep.yaml` profile remains a Demucs-only path, while
`compose.full-gpu.yaml` is the explicitly provisioned NVIDIA path for all four
capabilities.

## Review requirements for changes

Before pinning, recommending, or enabling another model, update this file with:

1. exact package, model revision, and immutable digest/checksum;
2. authoritative source URLs;
3. separate code/runtime and weight/model licenses;
4. known training-data or intended-use restrictions;
5. download size, expanded disk use, and GPU-memory expectations;
6. redistribution, commercial-use, and output restrictions, if any;
7. an explicit installation and consent boundary; and
8. tests proving truthful unavailable/fallback behavior and no silent network
   access.

If terms are unclear, noncommercial, source-available rather than open source,
or otherwise restrictive, keep the adapter disabled and flag the constraint in
the capability response and UI. Model volumes are removed only by the explicit
complete-deletion command using `docker compose ... down --volumes`; ordinary
setup, recovery, rebuild, and shutdown preserve them.

## Local ComfyUI video stack

- ComfyUI application/runtime: GPL-3.0. The optional managed installation is pinned to official release `v0.30.0`; the resulting Git commit is recorded in the local installation lock.
- Wan2.2 I2V-A14B and TI2V-5B: Apache-2.0 upstream model license. The local setup script pins official Comfy-Org repackaged artifacts for native weights and the documented `bullerwins/Wan2.2-I2V-A14B-GGUF` community quantizations for low-memory Q5/Q4 pairs. Record the exact revision and SHA-256 in the installation lock before use.
- FLUX.1-schnell FP8 ComfyUI package: Apache-2.0. Used for fictional reference sheets/keyframes only; the setup record binds the exact source revision and SHA-256.
- ComfyUI-GGUF: Apache-2.0 software license at pinned revision `6ea2651e7df66d7585f6ffee804b20e92fb38b8a`. It is never silently installed; review and pin the selected revision separately.
- RIFE NCNN Vulkan: MIT software/model release; explicitly configured local executable only.
- Real-ESRGAN NCNN Vulkan and the distributed `realesrgan-x4plus-anime` model: MIT release bundle at `v0.2.2.4`; explicitly configured local executable only.

Optional anime checkpoints and LoRAs remain disabled unless their source, exact revision, SHA-256, and commercial-use-compatible license are recorded here first.
