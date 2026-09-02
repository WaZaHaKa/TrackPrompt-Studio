# Mastering comparison report

The set report compares reviewed child analyses. It is analytical preparation, not an automatic mastering decision.

Exports are available from:

```text
POST /api/batches/{batch_id}/report
GET  /api/batches/{batch_id}/report.json
GET  /api/batches/{batch_id}/report.md
GET  /api/batches/{batch_id}/report.csv
```

The archived POST writes immutable JSON, Markdown, and CSV artifact revisions. Read routes build the same typed comparison without returning storage paths.

Numeric fields include BPM, integrated loudness, loudness-range estimate, sample peak, macro dynamics, stereo width, phase correlation, spectral centroid, and onset density when available. Descriptive fields include key/mode, low-end weight, brightness, transient emphasis, density, mono compatibility, and vocal presence. The report calculates median, minimum, maximum, deviation, robust outlier flags, and withheld counts.

Estimator limitations remain explicit:

- sample peak is not true peak;
- no universal LUFS, tonal-balance, or stereo target is prescribed;
- missing values remain withheld;
- crossfade regions are mixed evidence and are not source-separated;
- tracks without a sufficient stable core carry a contamination warning.

