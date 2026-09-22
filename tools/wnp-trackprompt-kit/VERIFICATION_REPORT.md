# Verification report — WNP_TrackPrompt_1.0.0

- 61 local tests in this package passed.
- Master: 29; installer: 7; trackprompt: 25.
- Across the three component suites: 75 distinct tests passed (do not count the duplicated master suite three times).
- All authored Python files parsed successfully.
- Published JSON Schemas were checked against real locally generated objects.
- Reference tests use synthetic files/fake provider states and a local loopback API.
- Python 3.13.5 on Linux; optional test modules {'Pillow': '12.3.0', 'jsonschema': '4.26.0'}.
- PowerShell diagnostics, Windows process lifetime/ACLs, native Windows locking, actual SSH tunnels, installed OpenCode permission/model access, existing Companion UI and the real Hayale runner were NOT exercised here.
- Root cause of the actual repeated stale incident is not established by these tests.
- The included components require mapping into the actual local apps; see ACCEPTANCE_GATES.md.

Full local outputs: verification/LOCAL_TESTS.json. Initial failing-test evidence is included for the corresponding components.
