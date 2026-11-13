# Changelog

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
