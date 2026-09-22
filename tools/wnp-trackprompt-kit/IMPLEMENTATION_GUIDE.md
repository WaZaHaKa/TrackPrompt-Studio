# WNP packages — plain-English implementation guide

## Read this first

You have three kits, not three new copies of your applications.

| Package | Where it goes | What it does |
|---|---|---|
| WNP_Master_1.0.0.zip | Keep as the reusable base for future projects | Protocol, host/API/CLI, schemas, examples, tests |
| WNP_Companion_1.0.0.zip | Asus TUF, inside the existing Companion repo in WSL | Same master + client + OpenCode builder/QA setup + integration handoff |
| WNP_TrackPrompt_1.0.0.zip | Windows RTX3060 desktop, inside existing TrackPrompt | Same master + recovery tools + adapter + Codex handoff |

Packages 2 and 3 ALREADY include package 1's master code. Do not install all three
into each project. Their installers only stage an isolated tools/wnp-...-kit
folder; they do not patch the app, launch a model, start a render or change SSH.

The host/client/recovery libraries have local automated test evidence. Your actual
Companion UI and local Hayale runner still require the supplied agent integration
steps. No Windows GPU run, SSH tunnel or model account was exercised here.

**Keep the current Hayale run and all completed frames. Do not launch another
seven-shot render just to test installation.** Keep ZIPs and source media private.

## 1. Put the downloads on the right computers

Download WNP_TrackPrompt_1.0.0.zip to the RTX3060 desktop's Downloads folder.
Download WNP_Companion_1.0.0.zip to the Asus TUF's Windows Downloads folder.
Keep WNP_Master_1.0.0.zip wherever you keep reusable development packages.

No folder should be moved out of an existing repo. The commands below copy the
new kit, preserve your downloads and reject differing existing files.

## 2. Install the TrackPrompt kit — Windows PowerShell

Open PowerShell on the **desktop**, not inside WSL. Run:

```powershell
$Repo = 'C:\Users\theon\GitHub\TrackPrompt-Studio'
$Stage = Join-Path $HOME 'Downloads\WNP-Packages'
New-Item -ItemType Directory -Path $Stage -Force | Out-Null
Expand-Archive -LiteralPath "$HOME\Downloads\WNP_TrackPrompt_1.0.0.zip" -DestinationPath $Stage
$Kit = Join-Path $Stage 'WNP_TrackPrompt_1.0.0'
py -3 "$Kit\tools\verify_package.py"
py -3 "$Kit\tools\install_package.py" --target "$Repo"
```

The last command is a dry run. It should say the destination is:
`C:\Users\theon\GitHub\TrackPrompt-Studio\tools\wnp-trackprompt-kit`.

Now apply that copy and run local kit tests:

```powershell
py -3 "$Kit\tools\install_package.py" --target "$Repo" --apply
Set-Location -LiteralPath $Repo
py -3 .\tools\wnp-trackprompt-kit\tools\run_checks.py
```

Python must be 3.11 or newer. `py -3` uses your Windows Python launcher; when it is
not installed, use the full path to your EXISTING stable Python executable instead.
Do not use a temporary Codex-cache interpreter for a long-running production worker.
Do not install a second GPU stack. If frame tests are skipped because Pillow is not
available in this interpreter, use the existing TrackPrompt/ComfyUI environment
with Pillow for those tests and tell the agent which executable you used.

An existing staging folder may make Expand-Archive refuse a second extraction.
Use the existing verified staging folder, or choose a fresh empty staging path.
Do not add -Force to overwrite local changes. The installer itself is repeatable;
conflicting kit files stop it before copying. It never uses git reset/clean.

## 3. Start TrackPrompt's coding agent — still on Windows

From the real repository root:

```powershell
Set-Location 'C:\Users\theon\GitHub\TrackPrompt-Studio'
Get-Content .\tools\wnp-trackprompt-kit\trackprompt\prompts\START_CODEX.txt -Raw | Set-Clipboard
codex
```

Paste the clipboard into Codex. The prompt instructs it to diagnose the local
runner, add the regression/fix and wire WNP. It does not authorize killing a live
render, clearing ComfyUI history or starting a duplicate generation.

A useful optional read-only collection command is:

```powershell
pwsh -NoProfile -File .\tools\wnp-trackprompt-kit\trackprompt\tools\collect-incident.ps1
```

The collector writes a NEW private directory under
`.trackprompt-data\wnp-incidents`. It does not edit the run. Do not upload the
raw private process/provider dumps to a cloud model. Let the local agent extract
a redacted factual incident report. ComfyUI's port is discovered from the real
run; there is intentionally no guessed default endpoint.

**Gate:** the agent must prove the actual root cause and pass the existing runner's
regression tests before another production attempt. A new dashboard is not a fix.
If Shot002 was possibly accepted but its history is missing, the correct state is
ambiguous—not automatically safe to submit again.

## 4. Install the Companion kit — Asus TUF WSL terminal

Open your normal Ubuntu/WSL terminal on the **Asus**. Run:

```bash
mkdir -p "$HOME/wnp-packages"
python3 -m zipfile -e "/mnt/c/Users/theon/Downloads/WNP_Companion_1.0.0.zip" "$HOME/wnp-packages"
KIT="$HOME/wnp-packages/WNP_Companion_1.0.0"
REPO="$HOME/projects/opencode-experiments"
python3 "$KIT/tools/verify_package.py"
python3 "$KIT/tools/install_package.py" --target "$REPO"
```

Confirm that this is your current Companion root and contains
`wzhk_render_companion/`. The package will not invent a replacement repository.
Change only the REPO value if your actual local root moved.

Apply and test:

```bash
python3 "$KIT/tools/install_package.py" --target "$REPO" --apply
cd "$REPO"
python3 tools/wnp-companion-kit/tools/run_checks.py
```

Keep the project in WSL's Linux filesystem. Do not use the desktop's live runtime
or a copied Windows virtual environment as your WSL execution environment.

## 5. Configure DeepSeek builder and Kimi reviewer

Use your EXISTING OpenCode/Ollama provider connection. First inspect it:

```bash
opencode --version
opencode models
```

You need configured entries corresponding to:
`deepseek-v4.1-flash:cloud` and `kimi-k3:cloud` (some direct-cloud provider IDs omit
`:cloud`). The configurator reads the actual catalog, it does not invent IDs.

Generate a dry-run agent configuration:

```bash
cd "$HOME/projects/opencode-experiments"
python3 tools/wnp-companion-kit/companion/tools/configure_agents.py --repo "$PWD" --provider ollama
```

`ollama` must be the provider prefix displayed by `opencode models`. If your
existing prefix is different, replace only the --provider value with that prefix.
A missing/ambiguous requested model is a blocker. Do not silently use another one.
Cloud inference may consume your account credits; these scripts do not buy credits,
change a plan or fall back to another billable provider.

After the preview shows the two correct models:

```bash
python3 tools/wnp-companion-kit/companion/tools/configure_agents.py --repo "$PWD" --provider ollama --apply
```

This writes exactly `.opencode/agents/wnp-dev.md` and `wnp-qa.md` without replacing
root configuration/provider credentials. It selects the installed OpenCode
permission syntax. Versions/settings still need a local effective-permission check.

Start the builder:

```bash
opencode --agent wnp-dev
```

Paste `tools/wnp-companion-kit/companion/prompts/START.txt`. The builder invokes
`@wnp-qa` for Kimi's read-only review. You can also select wnp-qa manually in
OpenCode for a separate review. Kimi may read code and test reports; it cannot
edit or run shell commands under the supplied profile. Test execution comes from
the deterministic runner, not from a fabricated QA claim.

Before using the reviewer on sensitive code, confirm effective permissions with
a disposable write/shell probe. Managed settings, plugins or a version mismatch
must not undermine the intended restrictions. Use no real secret in such a probe.

**Gate:** preserve the current Companion's UI, jobs, database and SSH monitoring.
The builder adds WNP through existing architecture and runs its current tests.
The historical 258-test audit is not a substitute for a fresh local test run.

## 6. Initialize and test the WNP host on the desktop

Do this after the TrackPrompt adapter is integrated, or in a separate test project
for a synthetic smoke test. Do not confuse a manually initialized idle host with
an adapter that is actually tracking the render.

Back in desktop PowerShell:

```powershell
Set-Location 'C:\Users\theon\GitHub\TrackPrompt-Studio'
py -3 .\tools\wnp-trackprompt-kit\master\wnp.py --root . --json init --project trackprompt-studio --node render-rtx3060 --kind media.pipeline
py -3 .\tools\wnp-trackprompt-kit\master\wnp.py --root . --json status
py -3 .\tools\wnp-trackprompt-kit\master\wnp.py --root . token-init
py -3 .\tools\wnp-trackprompt-kit\master\wnp.py --root . serve --port 9761
```

Run token-init once; an existing token is intentionally not overwritten. The token
is in `.wnp\auth\read.token`; never paste it into chat or commit it. Verify NTFS
ACLs and `.wnp/` Git exclusion. The server requires the token even over loopback.
Keep this terminal open for the first smoke test. Serving the API is not the same
as running the TrackPrompt observer; the agent must provide and test its exact
adapter launch as part of T4.

For unattended monitoring, have the agent create a separate reviewed Windows
user-service/Scheduled Task for the stable host and adapter, with working directory,
logs, single-instance policy, bounded restart and an uninstall command. Test closing
the coding session. No service registration is performed by these packages.

## 7. Connect using the SSH access you actually have

### Route A: Asus can already SSH directly to the desktop

In a NEW WSL terminal on the Asus:

```bash
read -rp 'Existing SSH alias/login for the Windows desktop: ' DESKTOP_SSH
ssh -N -T -o BatchMode=yes -o StrictHostKeyChecking=yes -o ExitOnForwardFailure=yes \
  -L 127.0.0.1:19761:127.0.0.1:9761 "$DESKTOP_SSH"
```

This forwards Asus localhost19761 to desktop localhost9761. Keep it running during
the smoke test. It does not start the WNP host. Verify the host key independently
at first enrollment; do not bypass host checking to make a command work.

### Route B: both computers can SSH to the SAME Hetzner relay, not each other

Existing Hetzner access alone is insufficient: create BOTH legs. This keeps the
relay listener loopback-only. Never enable public GatewayPorts for this purpose.

On the DESKTOP in a NEW PowerShell window:

```powershell
$Relay = Read-Host 'Existing SSH alias/login for the Hetzner relay'
ssh -N -T -o BatchMode=yes -o StrictHostKeyChecking=yes -o ExitOnForwardFailure=yes `
  -R 127.0.0.1:19761:127.0.0.1:9761 "$Relay"
```

On the ASUS in a NEW WSL terminal:

```bash
read -rp 'Existing SSH alias/login for that SAME relay: ' RELAY_SSH
ssh -N -T -o BatchMode=yes -o StrictHostKeyChecking=yes -o ExitOnForwardFailure=yes \
  -L 127.0.0.1:19761:127.0.0.1:19761 "$RELAY_SSH"
```

Both commands must succeed. Your server must permit the specific forwarding. A
port collision or denied forwarding is a real network configuration issue, not
reason to expose ports publicly. Local Unix accounts on a shared relay may reach
a loopback listener; the API's reader authentication is still required. Only use
a relay you trust; end-to-end TLS can add protection on top of the SSH legs.

For a persistent tunnel, let the agent adapt your existing process/service manager
with bounded reconnects. Do not build autonomous render control into that tunnel.

## 8. Provision the reader credential privately and run one sync

Securely transfer the desktop reader token to the Asus using your trusted secret
transfer method, not Git/chat/model prompts. In WSL you can store it with hidden
input (the token is not printed):

```bash
umask 077
mkdir -p "$HOME/.config/wnp"
read -rsp 'Paste the desktop WNP reader token here (hidden): ' WNP_READER_TOKEN
printf '\n'
printf '%s\n' "$WNP_READER_TOKEN" > "$HOME/.config/wnp/render-desktop.token"
unset WNP_READER_TOKEN
chmod 600 "$HOME/.config/wnp/render-desktop.token"
```

Run a single real sync on the Asus:

```bash
cd "$HOME/projects/opencode-experiments"
python3 tools/wnp-companion-kit/companion/sync.py \
  --url http://127.0.0.1:19761 \
  --token-file "$HOME/.config/wnp/render-desktop.token" \
  --database "$HOME/.local/share/wnp/companion-cache.sqlite3" \
  --alias render-desktop
```

Expect correct project/node/stream, a contiguous cursor and a last-successful-sync
value. The same command with `--watch` polls every10seconds. It never submits work.
The existing Companion UI displays this via the C1–C2 integration, not automatically
just because the sidecar script exists. Use the same configured cache path; do not
accidentally create two independent mirrors and compare them as one.

## 9. Prove reconnect and non-duplication

Use a synthetic run first. Stop only the tunnel/observer, not ComfyUI or the renderer.
Record a few semantic transitions on the test host. Reconnect. The cursor catches
up, existing events are not duplicated, old state is clearly marked cached while
offline, and a stale worker does not automatically become a failed workload.

On the actual incident, require the TrackPrompt acceptance report to demonstrate:
Shot001's exact verified checkpoint reused; Shot002 matched/adopted or explicitly
held ambiguous; no duplicate provider POST; repeated local recovery is a no-op.
Only then use the agent's tested existing-run continuation command. Do not reuse
an old launcher that creates a new run. The finished movie gate is1440finalframes,
60seconds,1080p24, coherent audio and actual visual review.

## 10. Reuse the master in future projects

Extract WNP_Master_1.0.0.zip to a staging folder. Run its verifier, then installer
with --target pointing to an existing project Git root (dry run first, --apply
second). Read master/PROMPT_START.txt and have the project's coding agent add a
small adapter. Give each instance a stable node ID and independent runtime. Keep
the same master version/hash; don't fork schemas per project. Upgrade later with
an explicit migration rather than overwriting a running copy.

The Companion can be offline without losing host history, provided the host keeps
that history and the machine/storage survives. A powered-off/asleep machine cannot
be queried live; show cached state and reconnect later. WNP cannot overcome loss
of the underlying disk, absent backups, network reachability or power.
