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

These checks qualify the stated deployment scope. They do not establish multi-DC availability,
an uptime SLA, concurrent-writer semantics, decision-quality gains or human adoption.
