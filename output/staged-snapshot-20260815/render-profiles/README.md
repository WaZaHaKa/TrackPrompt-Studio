# Local render profiles

WZHK Mission Control stores reusable render profiles below this directory, grouped by project. Generated JSON profiles, summaries, authorization requests, and authorization records stay local because they bind absolute approved-scene paths and hashes.

Create and manage schema 1.1 profiles through `WZHK-Media-Launcher.cmd`; do not hand-edit generated JSON. The builder validates with both Windows PowerShell and the renderer, shows every resolved renderer setting before save, saves atomically, and writes a sibling summary. A saved profile is not production-authorized until the two-stage scene/profile-specific authorization flow completes; any saved-file change invalidates that authorization. Parseable invalid files can be inspected, repaired, or explicitly deleted from the saved-profile manager, but cannot be selected for renderer actions.
