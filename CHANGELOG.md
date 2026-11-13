# Changelog

## 0.4.0 — 2026-11-13

Stable release for controlled, single-coordinator self-hosted use. Existing closed trial,
REST and MCP request contracts remain compatible; discovery adds an optional restore-key
prefix and health adds safe maintenance/coordinator status.

### Fixed

- A retry racing with deletion could reserve a deleted idempotency key again. Tombstones
  are now checked within the same write transaction as reservation.
- Completed operation locks no longer accumulate. Dead coordinators reject new submissions;
  temporary retention failures retry after sixty seconds without stopping trial dispatch.
- Failed startup and corrupt-ledger initialization release their database/file ownership.
  Schema initialization respects active coordinator and backup locks.
- Standalone CLI stores, locks and reports use owner-only permissions. Store paths refuse
  symlinks. Report replacement is atomic; JSON/HTML paths must differ before work starts.
- Report paths cannot replace trial definitions, SQLite databases or their lock/journal
  files. SQLite failures return an operational result with same-key recovery guidance.

### Added

- Offline `proofbeak-server check`, including duplicate JSON key, tenant/token uniqueness,
  provider-profile and worker-limit validation without creating state or contacting services.
- Offline `backup` and `restore` with private files, SQLite integrity/schema checks, complete
  directory inventory and SHA-256 checksums. Existing destinations are never overwritten.
- Restored queued/running work becomes interrupted. Historical evidence/tombstones remain;
  a fresh idempotency-key prefix fences requests absent from an older snapshot. A SQLite
  trigger also rejects stale insertions by older coordinator code. The MCP example reports
  the prefix without changing a caller's key or replaying a request.
- Actual upgrade qualification from pinned 0.3.0 source, plus 60 filesystem and 30 two-provider
  HTTP repeated-use trials with eight concurrent submissions per key, cancellation, tenant
  boundaries, deletion, restarts, restore, worker/lock and Linux descriptor checks.
- Actual MCP/WebDAV restore verification with independent mutation journals: historical
  reads and rejected stale requests cause no new provider mutations; an explicitly new
  prefixed trial provides the positive control.

### Upgrade and operation

Stop the service, take a verified snapshot, update the package/image and run the configuration
check before starting. See [state recovery](docs/state-recovery.md). Ordinary upgrade retains
queued jobs; restoring an older snapshot interrupts them and requires the new prefix for new
keys. Restore is not a rollback of remote provider side effects and cannot restore requests
or revocations accepted after the snapshot. Keep original provider scopes and reconcile them.

Qualification is bounded synthetic evidence, not a long-duration uptime/load study or a human
pilot. The stable scope retains one SQLite coordinator; remote SSO, per-trial containers and
multi-DC remain separate requirements. No cloud/Google account, model, OpenShift or Kubernetes
is required by the local core. No hosted site, package registry or image publication is implied.

## 0.3.0 — 2026-11-13

First integrated MVP release for consumer-defined acceptance trials. The external agent
chooses a registered condition and candidates; Proofbeak executes the trial and returns
independent observations with separate verdict and cleanup status.

### Included

- Generic package registry and dependency-free Python CLI, with filesystem and WebDAV
  repeated-write trials. Calendar occurrence movement is an optional second domain.
- Actual WsgiDAV, Apache WebDAV and Radicale integrations; labeled fault simulations remain
  excluded from reusable provider evidence. Google Calendar is protocol-tested only.
- Authenticated tenant-bound REST and eleven structured MCP tools, durable queue,
  idempotent retrieval, provider allowlists, explicit cancellation and cleanup recovery.
- Runnable MCP comparison client, private JSON/HTML evidence exports, integrity checks and
  operator profile import without raw credentials or arbitrary executable providers.
- Bounded subprocess workers with execution deadlines, process-group termination and
  interrupted-run recovery without automatic replay of provider writes.
- Context-bound evidence validity, revocation, audit, retention and tenant purge. Historical
  idempotency keys do not silently create fresh provider writes after evidence removal.
- Non-root read-only container, Compose and standard Kubernetes/Helm packaging, persistent
  restart checks and actual Calico ingress/egress tests. OpenShift is an optional profile.
- Measured trial/cleanup wall time, worker CPU and HTTP attempts, plus an offline pilot
  evaluator that distinguishes collected observations from missing data.

### Validation and limits

CI runs the core suite on Python 3.11, 3.13 and 3.14, real REST/MCP and provider integrations,
container checks, Helm installation, pod replacement and network-policy enforcement.
Provider-side HTTP journals independently verify that repeated MCP requests/read tools do
not add mutations, while an explicit new key starts another trial.

This version targets a local or controlled single-coordinator pilot. SQLite state uses one
owner; there is no multi-DC or horizontally replicated coordinator. Remote SSO/OAuth and
per-trial container isolation are separate pilot requirements. Shared-service tenant checks
are logical access controls, not independent tenant sandboxes.

Passing the storage package establishes its specific observed repeated-key/content condition.
It does not establish exact-once execution, disk-write counts, concurrency safety or durability
after a crash. Both providers passing does not establish a ranking. No actual human pilot,
fourteen-day adoption study, LLM token/cost benefit or decision-quality gain is claimed.

See the [MCP walkthrough](docs/mcp-walkthrough.md), [deployment guide](docs/deployment.md),
[package contract](docs/trial-packs.md) and [remaining pilot gates](docs/roadmap.md).
