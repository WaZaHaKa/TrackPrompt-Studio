# Optional models and licenses

## Shipping status

TrackPrompt Studio does not bundle, install, download, or enable model weights by
default. Fast mode uses local DSP libraries and has no model-weight disk cost.
Normal startup and analysis never trigger a model download. A functional local
Demucs adapter exists, but it remains unavailable until an operator explicitly
enables it and supplies both the optional package and separately obtained,
reviewed repository files whose SHA-256 values match a complete local manifest.

The table records every model-related capability currently named by the code.

| Capability/model | Version | Source | Code license | Weight license | Intended use | Approximate disk impact | Included by default |
| --- | --- | --- | --- | --- | --- | ---: | --- |
| Demucs four-stem separator | Python package range `>=4.0.1,<5`; configurable checkpoint name, default `htdemucs`; no checkpoint pinned or included | [facebookresearch/demucs](https://github.com/facebookresearch/demucs) | Upstream repository identifies the code as MIT | **Not verified or approved by TrackPrompt Studio; terms can differ by checkpoint and training data** | Optional local vocals/drums/bass/other separation, relative-RMS category descriptors, and enhanced vocal presence | Up to about 5 GB as conservatively disclosed by `/api/capabilities` | No; functional only after explicit enablement, package installation, and a checksum-verified complete reviewed repository |

## Important constraint

Installing a Python package does not establish that a particular checkpoint's
weights or training-data terms are suitable for a user's commercial or
noncommercial purpose. TrackPrompt Studio therefore keeps the adapter disabled by
default and does not provide a checkpoint download command. Deep analysis falls
back to Fast without fabricated stem output unless all readiness checks pass.

After independently reviewing the exact checkpoint and its terms, a direct-
development operator can opt in as follows:

1. From `backend/`, install the optional code with
   `python -m pip install -e ".[dev,deep]"`. This can bring substantial transitive
   dependencies, but it does not download or approve weights.
2. Place only the reviewed local repository files under `MODEL_CACHE_DIR`, then
   list every regular file there (checkpoint, configuration, and nested files) by
   relative path and actual SHA-256 under
   `models.<DEMUCS_MODEL_NAME>.files` in the root `demucs-models.json`. Any extra
   unmanifested file disables the adapter. Do not rely on a runtime download. The
   manifest is an integrity allowlist, not license approval.
3. Set `ENABLE_DEMUCS=true`, restart the backend, and verify the adapter reason
   and availability through `/api/capabilities` before analyzing private audio.

The adapter passes the configured cache as Demucs's local model repository and
sets offline environment flags. It supports explicit `cpu`, `cuda`, or `auto`
device selection and reports CUDA build/runtime state separately; CPU remains
the safe fallback. The base Docker image intentionally omits the optional
package. The reviewed `compose.deep.yaml` override builds `Dockerfile.deep`, and
`setup-deep-docker.ps1` requires explicit terms acceptance before it obtains the
selected repository files, writes their complete checksum manifest, and copies
that repository into `/data/models`. Merely selecting the Deep override without
that complete pre-populated repository leaves the adapter honestly unavailable.

No additional genre, instrument-family, vocal-descriptor, melody, or semantic
section model was added in analysis version `0.2.0`. Capability-gated reporting
marks these families unavailable because no candidate with fully reviewed code
license, weight license, training-data restrictions, commercial-use
implications, and resource requirements was selected. No result is fabricated
and no weights are downloaded.

Before this project pins, bundles, recommends, or enables an optional model by
default, a contributor must update this file with:

1. an exact package and checkpoint version or checksum;
2. authoritative source URLs;
3. separate code and weight licenses;
4. training-data restrictions relevant to intended use;
5. download size and expanded disk/GPU memory requirements;
6. whether commercial use, redistribution, or generated outputs are restricted;
7. an explicit, non-silent installation and consent flow; and
8. tests proving honest unavailable/fallback behavior.

If any term is unclear, noncommercial, source-available rather than open source,
or otherwise restrictive, keep the adapter disabled and flag the restriction
prominently in the capability response and UI. Model files must live in
`MODEL_CACHE_DIR`; every other file in that cache must be listed in the selected
model's checksum manifest, remain local, and be covered by the complete-deletion
guidance.
