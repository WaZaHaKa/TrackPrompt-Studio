You are a senior Python architecture engineer, FastAPI engineer, Docker/NVIDIA
engineer, Windows PowerShell engineer, and test engineer.

Fix the current TrackPrompt Studio full-GPU startup failure completely.

Do not stop after diagnosing the issue or writing a temporary repair script.
Inspect the actual repository state, implement a clean source-level fix, repair
the canonical setup and diagnostic tooling, run the tests, launch the full GPU
stack, and verify all enabled features.

Read and follow:

- AGENTS.md
- README.md
- docs/architecture.md
- docs/privacy.md
- docs/model-licenses.md
- docs/full-feature-implementation-plan.md
- existing backend and frontend tests

Do not overwrite unrelated working functionality.

# 1. Verified current state

The following parts have already worked and should not be rebuilt or replaced
without a concrete reason:

- Windows sees an NVIDIA GeForce RTX 3060 with 12 GB VRAM.
- Docker GPU passthrough works.
- PyTorch CUDA inference works.
- CTranslate2 sees one CUDA device.
- Demucs weights have been provisioned.
- The genre model has been explicitly downloaded into the model volume.
- The faster-whisper lyrics model has been explicitly downloaded.
- The Ollama prompt-writer service is healthy.
- The model `qwen2.5:7b-instruct-q4_K_M` is installed.
- The existing Demucs-only Deep profile has previously run successfully.
- The full GPU Docker image has already been built and should use Docker cache.

Do not delete Docker volumes.

Do not run:

```text
docker compose down --volumes
````

Do not redownload multi-gigabyte model files unless a verified integrity or
compatibility problem requires it.

# 2. Current source-level failure

The full GPU genre diagnostic fails with this dependency cycle:

```text
app.tagging.music
  imports app.analysis.core

Python initializes app.analysis.__init__
  which eagerly imports app.analysis.pipeline

app.analysis.pipeline
  imports app.adapters

app.adapters
  imports app.tagging

app.tagging.__init__
  imports app.tagging.music
```

This produces errors such as:

```text
ImportError: cannot import name 'create_music_tagger'
from partially initialized module 'app.tagging'
```

and:

```text
ImportError: cannot import name 'demucs_ready'
from partially initialized module 'app.adapters'
```

The immediate cycle is approximately:

```text
app.tagging.music
â†’ app.analysis.core
â†’ app.analysis.__init__
â†’ app.analysis.pipeline
â†’ app.adapters
â†’ app.tagging
```

A repair script attempted to replace the eager exports in
`backend/app/analysis/__init__.py` with lazy exports. Inspect the current file
rather than assuming that patch is correct or complete.

# 3. Current PowerShell diagnostic failure

The repair script also runs an inline command similar to:

```powershell
docker compose ... run backend python -c `
  'import ...; print("TrackPrompt import smoke test passed")'
```

Windows PowerShell 5.1 strips or alters nested quotes when forwarding that native
argument. The container receives invalid Python similar to:

```python
print(TrackPrompt
```

The resulting error is:

```text
SyntaxError: '(' was never closed
```

This is a diagnostic-script bug, not proof that the circular-import source fix
failed.

Do not solve this by trying another combination of nested quote characters.

# 4. Primary objective

Produce a clean import architecture in which all of these work in fresh Python
processes and in any relevant order:

```python
import app.analysis.core
import app.analysis.pipeline
import app.tagging.music
import app.tagging
import app.adapters
import app.main
```

Also preserve any supported public imports such as:

```python
from app.analysis import analyze_audio
from app.analysis import AnalysisCancelled
```

only if the repository actually relies on them.

Prefer explicit leaf-module imports over package-level re-export magic where
practical.

# 5. Fix the import architecture cleanly

Inspect every relevant import in:

* backend/app/analysis/**init**.py
* backend/app/analysis/core.py
* backend/app/analysis/pipeline.py
* backend/app/adapters.py
* backend/app/tagging/**init**.py
* backend/app/tagging/music.py
* backend/app/main.py
* backend/app/jobs.py
* backend/app/analysis/worker.py
* backend/app/diagnostics/*.py
* tests importing these modules

Choose the smallest clean architectural fix.

Valid approaches may include a combination of:

1. Make `app.analysis.__init__` minimal and remove eager imports of
   `app.analysis.pipeline`.
2. Change application call sites to import orchestration symbols directly from
   `app.analysis.pipeline`.
3. Make `app.tagging.__init__` minimal rather than eagerly importing every
   concrete adapter.
4. Import concrete adapter factories from their leaf modules rather than package
   `__init__` files.
5. Move shared protocols, dataclasses, enums, or adapter contracts into a neutral
   module with no orchestration imports.
6. Use dependency injection for adapter construction.
7. Use a narrowly scoped local import only when it represents a real runtime
   dependency boundary.

Do not:

* wrap the circular imports in broad `try/except ImportError`
* silently set missing functions to `None`
* suppress the exception
* duplicate functions to avoid imports
* introduce global mutable registries without tests
* add fragile import-order assumptions
* rely only on a repair script modifying source files at installation time

The repository source itself must be correct before setup scripts run.

# 6. Public package API

Determine whether code genuinely relies on:

```python
from app.analysis import analyze_audio, AnalysisCancelled
```

If it does not, remove those package-level re-exports and use:

```python
from app.analysis.pipeline import analyze_audio, AnalysisCancelled
```

If backward compatibility is required, implement a safe public API without
causing package initialization to load orchestration modules during imports of
`app.analysis.core`.

A lazy `__getattr__` is acceptable only if:

* it is typed
* it is tested
* it does not recreate another cycle
* direct leaf imports would not be cleaner
* documentation explains why it exists

# 7. Add a real import diagnostic module

Create:

```text
backend/app/diagnostics/imports.py
```

It must run with:

```text
python -m app.diagnostics.imports
```

Do not use a PowerShell inline `python -c` command for this diagnostic.

The diagnostic should:

* import the critical modules
* test at least two different import orders
* preferably use clean child Python processes so `sys.modules` from one test
  does not hide order-dependent cycles
* import the FastAPI application
* access the adapter factories
* access `analyze_audio`
* access `AnalysisCancelled`
* print a concise JSON result
* return a nonzero exit code on failure
* avoid loading large model weights merely to test imports
* avoid private job data

Suggested import orders include:

```text
analysis.core â†’ tagging.music â†’ adapters â†’ analysis.pipeline â†’ main
```

and:

```text
adapters â†’ tagging.music â†’ analysis.core â†’ main
```

# 8. Add regression tests

Create backend tests that fail on the current circular-import architecture.

At minimum test:

## Fresh-process import order

Use `subprocess.run` with the current Python interpreter and test multiple
orders in isolated processes.

## Package API

Verify any retained `app.analysis` public exports.

## Direct module APIs

Verify direct imports from:

* app.analysis.core
* app.analysis.pipeline
* app.tagging.music
* app.adapters

## Diagnostics

Verify:

```text
python -m app.diagnostics.imports
```

returns success.

## Application import

Verify:

```python
from app.main import app
```

works without loading optional model weights or making network requests.

## Optional dependency absence

Where supported, verify the application still imports cleanly when optional ML
packages or weights are unavailable.

# 9. Repair PowerShell tooling

Inspect:

* setup-full-gpu.ps1
* setup-full-gpu-v3.ps1, if present
* repair-circular-import-and-start.ps1
* repair-circular-import-and-start-v2.ps1, if present
* verify-full-gpu.ps1
* diagnose-full-stack.ps1

Consolidate them rather than leaving multiple contradictory scripts.

There should be one documented canonical full-GPU installer and one documented
verification script.

Requirements:

* Compatible with Windows PowerShell 5.1.
* Use argument arrays for native commands.
* Judge native-process success using `$LASTEXITCODE`.
* Do not treat normal Docker stderr progress as a terminating PowerShell error.
* Do not pass multiline shell programs through `sh -lc`.
* Do not pass nontrivial Python through `python -c`.
* Use repository Python modules such as
  `python -m app.diagnostics.imports`.
* If a temporary Python file is truly necessary, write it to a temporary
  directory, mount it read-only, and remove it in `finally`.
* Do not create timestamped backups inside tracked source directories during
  normal setup.
* Do not edit Python application source files during installation.
* Do not delete Docker volumes.
* Preserve model caches.
* Start services in dependency order:

  1. prompt writer
  2. backend
  3. frontend
* Wait for health status.
* On backend failure, print:

  * `docker compose ps --all`
  * backend logs
  * container state
  * import diagnostic result
* Return a nonzero process exit code on failure.

Replace fragile smoke commands with:

```powershell
docker compose `
  -f compose.yaml `
  -f compose.full-gpu.yaml `
  run --rm --no-deps `
  backend `
  python -m app.diagnostics.imports
```

# 10. Repair the canonical full-GPU setup flow

The canonical script must support the already-installed environment.

It should not force a full model download or dependency rebuild on every run.

Add or preserve switches similar to:

```text
-AcceptAllReviewedModelTerms
-SkipBuild
-SkipModelInstall
-ForceDownload
-NoStart
-NoBrowser
```

A normal recovery run should be possible with a command similar to:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\setup-full-gpu.ps1 `
  -AcceptAllReviewedModelTerms `
  -SkipBuild `
  -SkipModelInstall
```

Use names that match the final implementation and document them exactly.

The script should:

1. Validate Docker.
2. Validate NVIDIA.
3. Validate Compose.
4. Reuse existing model volumes.
5. Run `app.diagnostics.gpu`.
6. Run `app.diagnostics.imports`.
7. Run genre diagnostics.
8. Run lyrics diagnostics.
9. Start and verify the prompt writer.
10. Run prompt-writer diagnostics.
11. Run combined capabilities diagnostics.
12. Start backend.
13. Wait for backend health.
14. Start frontend.
15. Wait for frontend health.
16. Call `/api/health`.
17. Call `/api/capabilities`.
18. Print the application URL.

# 11. Verify all full-feature adapters

After fixing imports, run:

```text
python -m app.diagnostics.gpu
python -m app.diagnostics.imports
python -m app.diagnostics.genre
python -m app.diagnostics.lyrics
python -m app.diagnostics.prompt_writer
python -m app.diagnostics.capabilities
```

Run them inside the full-GPU backend image.

Verify that diagnostics report truthfully:

* RTX 3060 available
* Torch CUDA available
* CTranslate2 CUDA device available
* Demucs available
* genre tagger available
* lyrics adapter available
* prompt writer reachable
* Reliable mode available
* Creative mode available
* Experimental mode available

Do not mark a capability available merely because a Python package imports.

Where practical, run a tiny bounded inference smoke test.

# 12. Full Docker verification

Use:

```text
docker compose -f compose.yaml -f compose.full-gpu.yaml config
```

Then build using Docker cache:

```text
docker compose -f compose.yaml -f compose.full-gpu.yaml build backend frontend
```

Run import diagnostics before starting the backend.

Start services separately rather than allowing a frontend dependency error to
hide the backend exception.

Expected sequence:

```text
docker compose -f compose.yaml -f compose.full-gpu.yaml up -d prompt-writer
docker compose -f compose.yaml -f compose.full-gpu.yaml up -d --no-deps backend
docker compose -f compose.yaml -f compose.full-gpu.yaml up -d --no-deps frontend
```

Wait for health between stages.

Then run:

```text
docker compose -f compose.yaml -f compose.full-gpu.yaml ps
```

Call:

```text
GET http://127.0.0.1:8000/api/health
GET http://127.0.0.1:8000/api/capabilities
```

Open or verify:

```text
http://127.0.0.1:5173
```

Do not use `down --volumes`.

# 13. Run repository checks

Run all documented checks.

At minimum:

```text
cd backend && python -m pytest
cd backend && python -m ruff check .
cd backend && python -m mypy app

cd frontend && npm test -- --run
cd frontend && npm run lint
cd frontend && npm run typecheck
cd frontend && npm run build

docker compose -f compose.yaml -f compose.full-gpu.yaml config
```

Run the end-to-end test suite when the environment supports it.

Do not claim any command passed unless it actually ran successfully.

# 14. Dependency integrity

The full GPU Dockerfile installs the base lock file and then installs optional
ML dependencies.

Check whether this changes locked packages such as NumPy after the initial lock
installation.

Run:

```text
python -m pip check
```

inside the full GPU image.

Record important final package versions.

If optional dependency installation causes incompatible or unintended package
downgrades/upgrades:

* resolve them explicitly
* pin compatible versions
* update the reviewed lock or full-GPU requirements file
* do not silently accept dependency drift

Do not undertake unrelated dependency upgrades.

# 15. Git hygiene

The repository currently has generated artifacts that must not remain tracked,
including potentially:

* deep-models/
* model checkpoints
* `*.bak-*`
* full-GPU setup transcripts
* temporary repair directories

Update `.gitignore` with appropriate rules.

Ensure:

```text
git ls-files deep-models
```

returns no files.

If model files are already tracked, remove them from the Git index without
deleting the local copies.

Do not rewrite Git history automatically.

Report that history cleanup may still be needed before pushing if a large model
was included in an earlier commit.

# 16. Documentation

Update README.md with:

* the canonical full-GPU setup command
* the normal relaunch command
* import diagnostic command
* verification command
* troubleshooting for backend startup
* model-cache preservation behavior
* commands that do and do not delete volumes

Remove or clearly deprecate instructions pointing to obsolete repair scripts.

Update architecture documentation to explain the corrected dependency
boundaries.

# 17. Acceptance criteria

The task is complete only when:

1. The circular import is fixed in repository source.
2. Import success does not depend on import order.
3. `python -m app.diagnostics.imports` passes.
4. The diagnostic does not use fragile `python -c` quoting.
5. Genre diagnostics pass.
6. Lyrics diagnostics pass.
7. Prompt-writer diagnostics pass.
8. Combined capability diagnostics pass.
9. The backend container becomes healthy.
10. The frontend container becomes healthy.
11. The prompt-writer container remains healthy.
12. `/api/health` succeeds.
13. `/api/capabilities` succeeds.
14. CUDA capability is reported truthfully.
15. Genre, lyrics, and prompt modes are reported independently.
16. Fast mode still imports and works without optional weights.
17. Existing Demucs functionality is not regressed.
18. The canonical PowerShell setup script works in Windows PowerShell 5.1.
19. The setup script does not edit application source during installation.
20. The setup script does not use multiline `sh -lc` programs.
21. The setup script does not use nontrivial inline `python -c`.
22. Docker volumes and model caches are preserved.
23. Backend tests pass.
24. Frontend tests pass.
25. Lint and type checks pass.
26. The production frontend build passes.
27. Git no longer tracks local model files or generated backups.
28. Documentation matches the actual commands.

# 18. Implementation order

Work in this order:

1. Inspect the current Git diff and repository state.
2. Reproduce the circular import in a clean backend Python process.
3. Map the actual import graph.
4. Implement the source-level import fix.
5. Add import regression tests.
6. Add `app.diagnostics.imports`.
7. Run backend import tests locally.
8. Rebuild the backend image using cache.
9. Run the import diagnostic in Docker.
10. Run genre and lyrics diagnostics.
11. Repair and consolidate PowerShell scripts.
12. Run the canonical setup script.
13. Start the full stack.
14. Verify health and capabilities.
15. Run all backend and frontend checks.
16. Fix any newly exposed issue.
17. Clean Git-tracked generated artifacts.
18. Update documentation.

Do not stop after the import test passes. Continue until the full GPU application
is healthy and usable.

# 19. Final response

When finished, report:

* root cause
* import-graph changes
* files changed
* scripts consolidated or deprecated
* tests added
* exact commands run
* exact test results
* Docker service states
* `/api/health` result
* `/api/capabilities` summary
* GPU diagnostic result
* genre diagnostic result
* lyrics diagnostic result
* prompt-writer diagnostic result
* remaining limitations
* Git cleanup performed
* exact normal launch command

Do not conceal failed commands.

Do not claim the full application works until the backend, frontend, and prompt
writer are all healthy and the capability endpoint has been checked.

