# Integrating another project

Keep the master code byte-identical; write a project-owned adapter beside it. Do
not fork the protocol silently or make the Companion parse the project's logs.

The integration boundary is `wnp_host.store.Store`:

```python
store = Store(project_root)  # initialized once, not once per run
store.append(
    "state.changed",
    patch={"state": "waiting", "health": "degraded", "run": run_id,
           "task": task_id, "detail": "provider.reconciling"},
    key=stable_source_record_id,
)
store.observe({"worker": {"state": "stale", "heartbeat_at": source_heartbeat}})
```

The package examples are executable without GPU. Validate source records before
publishing; never infer a successful run from a log message or a marker filename.
Deduplicate by stable source event/transition identity, not the current time.
One adapter owner is responsible for one project instance. Simultaneous workloads
belong under explicit run/task summaries in extensions; do not keep overwriting an
arbitrary current task with whichever worker polled last.

An adapter restart must reconcile authoritative application state first, then
publish. Preserve unknown and ambiguous states. Do not infer that an unreachable
provider is absent. If WNP fails, do not change render execution, clear successful
work, or generate a new job to repair monitoring.

Maintain a small, redacted local outbox keyed by producer ID when application
state commits independently of WNP. Retry delivery after restart without duplicate
events. The outbox and its ACK policy are project-owned integration work; the core
producer-key support does not magically make an application's separate DB atomic.

For evidence, register sanitized bytes then append a referencing event. Keep a
bounded allow-list of report fields. Large media stays outside WNP.
