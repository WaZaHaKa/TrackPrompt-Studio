# Archive backup, verification, and restore

Archive retention means retained locally until explicit deletion. Verified backups are required for durability.

Explicit project deletion is available only through the confirmed API/UI path.
It removes the project audit journal and private project files, retains shared
source bytes while another logical asset references them, and leaves only a
content-free deletion tombstone in the catalogue database. Restore from a
verified backup is the only supported recovery path.

Create and verify a backup from the repository root:

```powershell
.\backup-trackprompt-catalog.ps1 -Destination D:\TrackPrompt-Backups\catalog-2026-07-20
.\verify-trackprompt-archive.ps1 -Source D:\TrackPrompt-Backups\catalog-2026-07-20
```

Or use Python:

```text
cd backend
python -m app.catalog.backup create D:\TrackPrompt-Backups\catalog-2026-07-20
python -m app.catalog.verify D:\TrackPrompt-Backups\catalog-2026-07-20
```

The command uses SQLite’s consistent backup API, copies archive blobs/artifacts, excludes model caches and active temporary jobs, and writes a manifest containing byte sizes and SHA-256 values. Creation checks destination free space and refuses an existing destination. `--dry-run` makes no destination changes.

Restore only into a separate empty location:

```powershell
.\restore-trackprompt-catalog.ps1 `
  -Source D:\TrackPrompt-Backups\catalog-2026-07-20 `
  -Destination D:\TrackPrompt-Restore-Check
```

Restore verifies the manifest and SQLite integrity first, checks free space, refuses overwrite, and initializes the restored catalogue in the new location. Verify the restored audit chains and artifact hashes before changing `TRACKPROMPT_DATA_DIR`.

An off-device copy is recommended. TrackPrompt does not silently upload backups or add cloud storage.
