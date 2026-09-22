# WNP/1 reusable master — reference implementation 1.0.0

This directory is identical in all three delivery packages. It contains runnable,
standard-library Python code, a wire specification, schemas, examples and tests.
It is not a remotely deployed service until installed and configured locally.

Python 3.11+ is required. No model, cloud account, GPU or pip install is needed
for the master. Native Windows locking/service behavior still requires acceptance
on your Windows machine; the packaged evidence records Linux tests only.

Start with `spec/WNP1.md`, `spec/SPEC_CLARIFICATIONS.md` and `spec/SECURITY.md`.
Run the enclosing package's `tools/run_checks.py` before integrating it.

## Small command vocabulary

Run from the enclosing kit folder; use your existing Python executable:

```text
python master/wnp.py --root /absolute/project --json init --project example --node laptop
python master/wnp.py --root /absolute/project --json status
python master/wnp.py --root /absolute/project token-init
python master/wnp.py --root /absolute/project serve --port 9761
```

`--root` must identify the actual project, not the kit folder. `init` changes only
`wnp.json` and `.wnp/`; ordinary restarts retain the stream. Add `.wnp/` to the
project's ignore policy BEFORE committing. A project descriptor conflict is an
error, not permission to overwrite an existing configuration.

`serve` is an authenticated **read-only** loopback API. Keep its terminal open for
a smoke test; install a supervised user service for unattended operation. The
host cannot start, cancel, restart or resume a workload. Local `append`, `observe`
and `recover` commands belong to the trusted application/operator, never to a
remote monitoring credential.

## Deployment boundary

Use one runtime per project instance on a native local filesystem. Do not share
`.wnp/` between Windows and WSL writers, mount it over SMB/NFS, or synchronize its
live files through OneDrive/Drive. The Companion uses its own separate SQLite
cache. Backups must be consistent and restored deliberately, not over a live writer.

This small reference implementation scans active history on reads. Its tests prove
specified behavior, not throughput at arbitrary scale. Keep high-frequency metrics
outside the journal. Add indexing only behind the unchanged contract, with tests.
