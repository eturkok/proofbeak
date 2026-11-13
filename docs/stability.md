# Stability scope and release checks

The next stable release targets the generic single-coordinator service on Linux/macOS,
with direct Python, container and standard Helm deployment options. Existing closed trial,
REST and MCP contracts remain compatible. No vendor platform, cloud account or model is
required. Human pilot results remain separate from technical release qualification.

Release checks:

- Prevent a deleted idempotency key from creating another job during concurrent deletion.
- Keep operation bookkeeping bounded and reject new work when the coordinator stops.
- Retry temporary retention failures without losing dispatch or exposing raw exceptions.
- Validate operator configuration without creating state or contacting provider services.
- Provide offline, verified state snapshots and conservative restore without automatically
  replaying queued work. Document rollback limits and original provider resource ownership.
- Verify upgrade from the previous release, evidence integrity, idempotency and cleanup after
  restart, restore and injected failures.
- Run a bounded repeated-use qualification with actual workers and services, then pass the
  supported Python, REST/MCP, provider, container, Helm and network-policy suites.
- Keep standalone CLI stores/locks/reports owner-only, publish complete reports atomically,
  reject overlapping export paths before writes, and release resources on corrupt-store errors.

These checks qualify the stated deployment scope. They do not establish multi-DC availability,
an uptime SLA, concurrent-writer semantics, decision-quality gains or human adoption.

## Qualification implementation

`tests/upgrade_smoke.py` loads the actual 0.3.0 source from pinned commit
`db9ad1b4aeffa01ff637e29734d652d36e10d8fc`, creates historical/queued/deleted runs, then opens
that state with the new code. It checks evidence hashes/timestamps, original idempotency,
queued-job execution after upgrade, deleted-key protection and the backup/restore CLI.
The pinned baseline must be present in the checkout; CI fetches repository history.

`tests/stability_soak.py` runs 60 filesystem trials and, separately, 30 trials against both
actual WebDAV services. Eight concurrent submissions per key must reserve one run. It checks
completed retries, cancellations, deletion/tombstones, tenant access, coordinator restarts,
snapshot/restore, empty worker/operation registries, and Linux file-descriptor growth.
Provider-side HTTP journals must contain exactly five mutations per trial/provider.

The MCP integration separately verifies stop/snapshot/restore through the real protocol,
unchanged historical evidence and mutation-free reads/stale retries, followed by a successful
intentionally new prefixed trial. No model call is used.

```bash
PYTHONPATH=src python tests/upgrade_smoke.py
PYTHONPATH=src python tests/stability_soak.py --iterations 60
# After building both WebDAV fixture images:
PYTHONPATH=src python tests/stability_soak.py --http --iterations 30
```

CI preserves `stability-core-qualification` and `stability-http-qualification` artifacts.
These are bounded synthetic exercises, not long-duration production load or uptime evidence.
Releases also retain the complete existing provider, interface and deployment checks.
