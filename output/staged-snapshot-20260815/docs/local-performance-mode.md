# Local performance mode

Exclusive Performance Mode is opt-in and reversible. It records the current
Windows power scheme, can select High Performance, inhibits sleep for the
active session, and raises at most one Blender process to `High` or
`AboveNormal`. `Realtime` priority is forbidden.

The mode does not terminate unrelated applications. Known WZHK GPU workloads
are reported, not silently killed. Multiple full Blender processes remain
disabled by default because VRAM pressure can reduce throughput or destabilize
EEVEE.

Mission Control takes a bounded telemetry snapshot before enabling the mode.
While the render watcher is running, it polls GPU temperature and writes the
identity-bound stop-after-current-chunk request at 88 C or above, but only when
the saved Exclusive Performance state is valid and active. The renderer then
validates and publishes its current chunk before exiting. VRAM, RAM, and disk
telemetry is displayed but does not yet have an automatic stop threshold; use
`LOCAL OPERATIONS / SAFETY -> REQUEST STOP AFTER CURRENT CHUNK` if those values
become unsafe.

GPU temperature, utilization, VRAM, clocks/power where available, CPU, RAM,
and disk observations should be evaluated as frames per validated hour. A high
utilization percentage alone is not a success criterion.

Use a bounded A/B range before adopting a power plan, validation-worker count,
scratch drive, or priority change. Restore the recorded power plan and sleep
state explicitly after the render. A failed mode launch attempts restoration,
but the operator should still verify the saved restore state.

From the repository root:

```powershell
.\WZHK-Media-Launcher.cmd
```

Then select:

```text
LOCAL OPERATIONS / SAFETY
-> EXCLUSIVE PERFORMANCE MODE
-> review AC power, telemetry, competing processes, and restore-state path
-> first Y confirmation
-> final Y confirmation
-> bounded A/B calibration
-> restore previous performance state
```
