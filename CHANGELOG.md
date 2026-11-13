# Changelog

## 0.8.1 — 2026-11-14

- Apply the parliament-blue and orange brand palette throughout Studio: navigation, primary
  actions, overview, visual/JSON editor, service forms, dialogs and sign-in screen.
- Use dark blue text on orange controls, legible muted text on light surfaces and distinct
  pass/fail status colors. Match the icon and browser theme color to the workspace.
- Keep the existing desktop/mobile layout, operation and authentication contracts.

## 0.8.0 — 2026-11-14

Make caller-defined acceptance tests usable from a self-hosted browser workspace and repeatable
across suites, releases and CI. Existing scenario/provider contracts remain supported.

- Add `proofbeak studio`: private local setup, actual SQLite/filesystem candidates, signing
  identity and a responsive browser console. Existing servers expose the same console at `/ui/`.
- Add schema-guided visual/JSON authoring, explicit expectations, parameter selection,
  nonexecuting validation/previews and selected OpenAPI import. Drafts can be saved/imported.
- Add bounded `suite.v1` parameter matrices, durable tenant-bound scheduling, cancellation,
  retention, restart/restore handling and same-key recovery after a lost submission response.
- Add strict-context historical comparisons with per-condition changes and explicit
  inconclusive results for incompatible or missing evidence.
- Add conservative CLI/CI gates, JUnit/Markdown/HTML/JSON suite exports and a reusable
  GitHub Actions acceptance step. Missing observations or unresolved cleanup cannot pass.
- Add independent installed connector packages, explicit entrypoint selection, scaffolding
  and actual worker qualification with persistent cleanup ledgers.
- Add portable Ed25519-signed evidence, independently pinned-key offline verification and
  separate authenticity, acceptance and freshness results. Signatures do not certify providers.
- Add HttpOnly browser sessions, same-origin/CSRF enforcement, memory-only drafts and
  a local-owner configuration boundary. Ordinary tenants cannot change provider allowlists.
- Extend REST/MCP with suites, comparisons, guided drafts/previews and signed exports;
  the MCP bridge now publishes twenty-two tools.
- Qualify actual browser workflows, mobile layout, installed wheel assets, external connector
  entrypoints, signing/tamper rejection and the existing provider/container/Kubernetes contracts.

The deployment scope remains one coordinator with SQLite state. Remote SSO, multi-DC and
human adoption/agent decision-quality claims remain outside this release.

- Add twelve author-prepared acceptance questions as ordinary scenario definitions, with an
  automated fresh-configuration MCP/REST/worker walkthrough on actual WsgiDAV/Apache and
  deliberately different HTTP JSON fixtures. Archive per-case evidence, aggregate HTML/JSON,
  independent mutation-request journals and unchanged same-key/restart retrievals. This is
  technical qualification, not human pilot or external-agent decision-quality evidence.

## 0.7.0 — 2026-11-13

Expand user-defined acceptance scenarios across service protocols with a common capability
contract. Existing `scenario.v1`, storage/calendar formats and evidence/state compatibility
remain available. The current deployment scope remains one coordinator.

- Add `scenario.v2`: operator-registered logical operations with closed input/output JSON
  schemas, independent observations and caller-defined acceptance conditions.
- Add REST CRUD bindings and selected offline OpenAPI JSON import, MCP target clients over
  stdio/Streamable HTTP, isolated SQLite/PostgreSQL transactions and Redis Streams.
- Support HTTP search/model endpoints, JSON Schema checks, set comparison and recall@k
  against caller-supplied references. Model integration is optional; no embedded LLM is required.
- Add bounded eventual observations and consecutive passing samples. Actions are never
  repeated while polling; late/unavailable observations do not become passing evidence.
- Verify ownership before remote mutations and cleanup, confirm resource absence, enforce
  read-only SQL observations and retain poll/operation provenance with measured timings.
- Expose the same capabilities through CLI, REST and the existing twelve MCP tools, with
  private worker credential codecs and tenant-owned profiles.
- Qualify real REST/MCP/PostgreSQL/Redis protocols, installed-wheel workflows on Python
  3.11–3.14, rootless offline containers and persistent Kubernetes workers. Search/model
  fixtures establish protocol behavior, not external model quality or human adoption.
- Add `connectors` and `postgres` extras, operator documentation and editable examples.
  Package/application 0.7.0; Helm chart 0.5.0.

## 0.6.0 — 2026-11-13

Published [Python wheel/source distributions on PyPI](https://pypi.org/project/proofbeak/0.6.0/)
and `ghcr.io/eturkok/proofbeak:0.6.0`, with the existing REST/MCP commands. The
[release workflow](https://github.com/eturkok/proofbeak/actions/runs/43445753108) verified
public PyPI file hashes, a fresh installation and trial, and the published image identity.

- Add `proofbeak example --output scenario.json` so an installed package can create an editable
  starting definition without repository example files. Creation is private and atomic;
  existing files and symlinks are refused without running a provider trial.
- Add package URLs, SPDX license-file metadata and a curated source manifest excluding
  local state, credentials and generated reports.
- Build wheel/source artifacts once, verify metadata and hashes, then run fresh core and
  actual REST/MCP/worker installations outside the checkout on Python 3.11–3.14.
- Build a Linux amd64 container from that wheel and locked runtime dependencies, exercise
  rootless/read-only/offline operation and export the tested image archive with checksums.
- Add explicit main-only PyPI OIDC and GHCR publication jobs, gated on the installed-artifact
  checks. Main version increases start a release; manual publication requires a matching
  version input. PRs and main commits without a version increase do not publish.
- Package/application 0.6.0; Helm chart 0.4.0. Existing package/adapter/evidence schemas and
  single-coordinator state semantics are unchanged.

## 0.5.1 — 2026-11-13

Make user-defined scenarios easier to repair before execution. Validation errors now include
a stable code and the location of the invalid operation, argument, reference or condition.
Once a definition parses, each incompatible candidate gets its own diagnostic, including
the configured candidate ID and operation/route/parameter location.

- Add CLI `validate --json` with structured stdout and exit codes 0/3. Readable CLI errors
  show field locations. Invalid/deeply nested JSON and duplicate/excess candidate selections
  remain non-executing errors.
- Preserve server diagnostics in the MCP example client's final JSON error. Rejected
  definitions create no job, reserve no key, and create no report or provider mutations.
- Check diagnostic locations, input-value omission, bounded malformed-name feedback,
  candidate authorization and repair-then-run behavior in regressions. Actual MCP/WebDAV
  verification now includes rejection through the example client before any provider writes.
- Update application/package to 0.5.1 and Helm chart to 0.3.1. No database, runtime dependency,
  scenario semantics or adapter/profile version change; existing execution contexts remain
  compatible. Use the usual stopped-service backup/configuration-check upgrade procedure.

Diagnostics report the first definition issue or the first incompatibility per candidate.
Validation does not establish provider availability or a passing acceptance condition.

## 0.5.0 — 2026-11-13

Callers can now define their own acceptance scenarios instead of adding a Python package
for every condition. A single `scenario.v1` format describes preparation, action sequences,
independent before/after reads, initial requirements and acceptance predicates. The original
fixed storage/calendar packages and their request contracts remain compatible.

### Added

- Reusable object put/get/list/delete capabilities over the actual filesystem and WebDAV
  connectors, with generated disposable scopes and separate cleanup status.
- Public/read-only HTTP JSON GET capabilities over operator-configured origins and named
  routes. Optional JSON field projections preserve raw responses and let the same condition
  address differently shaped provider data. Client URLs, headers, credentials and arbitrary
  methods are unsupported.
- Machine-readable scenario input schema, operation/route discovery, CLI capability
  validation, authenticated `POST /trials/validate` and the twelfth MCP tool `validate_trial`.
  Validation creates no work and makes no provider request; it does not establish provider
  availability or satisfaction of the condition.
- Bounded references, strict predicates, captured operation traces and independent reads.
  Missing values cannot pass inequality or equality to null; failed preconditions stop
  actions; unknown outcomes and exhausted budgets stay inconclusive. HTML reports show
  scenario names, initial requirements and unavailable observations.
- Three editable JSON examples and an authoring guide covering custom conditions, two-service
  execution, capability boundaries and history/idempotency behavior.
- Mandatory real MCP/REST/worker verification of three definitions and a caller-modified
  failing expectation on both WsgiDAV and Apache. Independent journals verify scoped writes,
  cleanup, and mutation-free validation/reads/retries. Actual HTTP JSON fixture tests verify
  different response shapes, changed caller predicates, missing fields and error handling.

### Changed

- Raw-evidence retention also removes assertion expected/observed values, which can now
  contain user data. Existing verdicts and integrity metadata remain available.
- Package and application versions are 0.5.0; Helm chart is 0.3.0. Upgrade with the same
  stopped-service snapshot/configuration-check procedure as 0.4.0. No runtime dependency or
  database schema migration is introduced.

New conditions within supported capabilities need only JSON; unsupported protocols or
operations still require trusted connector development and actual-provider verification.
This release retains the controlled single-coordinator scope. It does not introduce an
unrestricted workflow/code runtime, natural-language compiler, HTTP write connector or
human adoption/decision-quality claim.

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
