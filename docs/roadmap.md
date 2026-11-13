# Delivery milestones

Based on the November 12, 2026 product plan. The sixty-hour estimate is a planning assumption,
not recorded development time. Weekly availability and external-account access set the pace.

The product is platform-neutral. Python on Linux/macOS, containers and standard Kubernetes/Helm
are alternative deployment modes. OpenShift compatibility is optional; no specific Kubernetes
distribution or cluster installation is required to complete the generic product or local pilot.

The primary flow is now a caller-defined scenario applied to compatible services, using
reusable operations rather than a Python package per case. Object scenarios are exercised
against self-hosted WsgiDAV and Apache WebDAV; HTTP JSON reads demonstrate different response
shapes under the same acceptance condition. Calendar trials remain an optional fixed package.
Google account access is not a primary development or pilot prerequisite.

## Integrated MVP 0.3.0

The CLI, actual storage comparison, REST/MCP workflow, bounded workers, evidence lifecycle
and optional deployment package are included in the first integrated release. The MCP
storage workflow and provider mutation journals passed in
[CI run 43387435849](https://github.com/eturkok/proofbeak/actions/runs/43387435849).
Release validation additionally checks installed package/CLI version consistency in the
rootless runtime image. See [release notes](../CHANGELOG.md).

The unchecked human-pilot gates below remain open. Shipping the MVP does not establish
onboarding speed, adoption, two-team reuse demand or improved agent decisions.

## Stable single-coordinator release 0.4.0

Technical hardening includes the deletion/retry race fix, bounded operation bookkeeping,
coordinator failure handling, deferred-maintenance retries, private atomic CLI exports,
offline configuration checks and verified snapshots with restore-key fencing. The
qualification suite exercises the actual 0.3.0 implementation, ordinary upgrade, restoration,
60 filesystem trials and 30 two-provider HTTP trials with eight simultaneous submissions/key.
Existing REST/MCP, provider, container, Helm, persistent restart and network-policy suites
remain mandatory. See [qualification details](stability.md) and [recovery operations](state-recovery.md).

This release qualifies controlled single-coordinator operation. The human-pilot and
demand-driven expansion gates below remain open.

## User-defined scenarios 0.5.0

The subsequent [capability scenario format](capabilities.md) extends the connector scope
while retaining this original format's behavior.

- [x] Let callers define setup, actions, independent observers and acceptance predicates as JSON.
- [x] Publish a machine-readable schema and candidate operation/route capabilities.
- [x] Validate through CLI, REST and MCP without creating work or accessing providers.
- [x] Reuse object operations across multiple cases on actual filesystem and WebDAV services.
- [x] Map actual HTTP JSON fields from different services without synthesizing observations.
- [x] Verify three user definitions and a caller-modified failing expectation through MCP/REST
  and actual WsgiDAV/Apache, including cleanup and mutation-free validation/retries.
- [x] Document authoring, reference rules, operation limits and the connector extension boundary.

These are controlled synthetic checks. New conditions within existing capabilities require
only a definition; unsupported protocols or operations remain trusted connector work. The
human-pilot, external agent decision-quality and demand gates remain open. This increment
does not enable arbitrary HTTP writes, executable scripts or a natural-language compiler.

## Scenario authoring feedback 0.5.1

- [x] Locate invalid operations, arguments, references and conditions within the caller's definition.
- [x] Identify each incompatible candidate and its first unsupported operation/route/parameters.
- [x] Expose structured CLI validation and preserve diagnostics through REST/MCP and the example client.
- [x] Verify repair-then-run behavior and mutation-free rejection without echoing input values.

This improves the caller-owned scenario workflow without adding case-specific product code,
new connectors or a natural-language compiler. Human-pilot gates below remain open.

## Capability expansion 0.7.0

- [x] Publish per-operation input/output schemas and read-only policy under a shared contract.
- [x] Add scenario.v2 without changing existing scenario.v1 or fixed domain definitions.
- [x] Add named REST mutations/observations and selected offline OpenAPI operation import.
- [x] Test other MCP servers through actual stdio and Streamable HTTP clients.
- [x] Add isolated SQLite/PostgreSQL statements, transactions, rollback and enforced read-only observations.
- [x] Add scoped Redis Streams and observe results produced by an asynchronous consumer.
- [x] Connect HTTP search/model services and evaluate explicit schema/content/ranking conditions.
- [x] Add bounded final-observation polling, consecutive passing samples and late-response handling.
- [x] Preserve ownership-checked cleanup, tenant profiles, private worker credentials and retry semantics.
- [x] Qualify real protocols, installed wheels, rootless containers and persistent Kubernetes workers.

Model/search protocol qualification uses local reference data and deterministic model fixtures.
External model quality, developer adoption and demand remain the independent pilot gates below.

| Stage | Planned effort | Exit condition | Branch |
| --- | --- | --- | --- |
| Foundation | First 12 h | Closed fixture, CLI, real self-hosted observations, two real user selection questions | `feat/storage-provider-comparison` |
| Provider comparison | Next 12 h | WsgiDAV and Apache under the same storage test; faults caught; cleanup reported | `feat/storage-provider-comparison` |
| Agent interfaces | Next 12 h | REST and MCP; authenticated tenant-bound runs; safe retries; two clients | `feat/agent-interfaces` |
| Private evidence hub | Next 12 h | Context-bound validity and reuse; decision-quality experiment | `feat/private-evidence-hub` |
| User pilot | Last 12 h | Three developers; measured decision quality, false positives, adoption, and a continuation decision | Separate scoped branches |

## Foundation status

Technical release scope and qualification are tracked in
[stability scope and release checks](stability.md). Human-pilot gates remain independently open.

- [x] Fixed four-occurrence Istanbul fixture and explicit acceptance conditions.
- [x] Dependency-free local CLI and deterministic observer.
- [x] Fault controls: correct, move-all, duplicate, permission-denied, ambiguous write.
- [x] SQLite idempotency, resource ledger, quarantine, explicit cleanup recovery.
- [x] JSON evidence and portable HTML comparison report.
- [x] Radicale adapter, container setup, and real-server integration test implementation.
- [x] Run and pass the real Radicale integration in GitHub CI (November 12, 2026).
- [ ] Obtain two recent real service-selection questions from developers.

## Generic core increment

- [x] Move calendar schema, fixture, and identity rules into a versioned package.
- [x] Use a domain-neutral adapter lifecycle and resource handle in the worker.
- [x] Bind package version and adapter compatibility before reservation.
- [x] Run a second package against actual disposable filesystem resources.
- [x] Preserve cleanup deserialization for the original resource ledger.
- [x] Add a protocol-tested Google adapter for the unchanged calendar package.

## Primary storage comparison

- [x] Reuse the closed storage.put_same_key.v1 package for a repeated key/content write.
- [x] Implement a standard WebDAV adapter with independent PROPFIND/GET observations.
- [x] Keep writes within owned existing objects, using strong ETags when available.
- [x] Guard cleanup with an owned-collection marker.
- [x] Support operator-owned WebDAV profiles through CLI, service and subprocess codecs.
- [x] Add actual WsgiDAV/Apache fixtures and a mandatory comparison/evidence CI job.
- [x] Run the two-service comparison and retain its JSON/HTML evidence (CI run 43381751390).

The comparison checks one test-data key and its content. It does not establish exact-once
execution, hidden version-history deduplication, crash durability or concurrent-writer safety.
See [storage comparison](storage-comparison.md).

## Optional calendar backlog

- [ ] Verify Google Calendar on a dedicated account if that optional integration is selected.

The existing Radicale and Google adapter code/tests remain available. Live Google access is
not required by the primary storage case or the generic product.

## Agent interface increment

- [x] Add a runnable MCP comparison/export client and operator profile import.
- [x] Add mandatory MCP-to-WebDAV workflow checks with independent provider mutation journals.
- [x] Add FastAPI/Pydantic REST around a shared application service.
- [x] Persist a tenant-scoped queue before returning 202.
- [x] Bind tenant identity to the authenticated key, with provider allowlists and queue budgets.
- [x] Expose structured run, comparison, evidence, and cleanup tools over MCP stdio.
- [x] Stop interrupted jobs from being replayed after a worker restart.
- [x] Implement real REST/MCP protocol integration checks and make them mandatory in CI.
- [x] Run trials and cleanup in separate bounded processes with independent deadlines.
- [x] Terminate orphan workers and support tenant-bound cancellation without replaying writes.
- [x] Package a non-root container and configurable Kubernetes NetworkPolicy.
- [x] Add evidence audit, retention, revocation and tenant data deletion.
- [ ] Add remote identity and any per-trial container boundary required by an actual pilot.

## Evidence lifecycle increment

- [x] Add bounded tenant history and explicit context-bound evidence lookup.
- [x] Exclude expired, revoked, simulated, inconclusive, corrupt, and redacted records from reuse.
- [x] Audit mutations and evidence access without credentials or fixture payloads.
- [x] Apply seven-day raw and thirty-day summary/audit retention to both local database copies.
- [x] Preserve minimal replay-prevention tombstones after run deletion.
- [x] Add offline tenant purge and credential disabling after resource cleanup.
- [ ] Validate actual two-team reuse demand and the planned decision-quality experiment.

Do not claim a passing real provider or cross-provider comparison from simulation controls.
Do not build the public hub, pricing, generic natural-language compiler, or multi-DC deployment
before evidence of repeated demand.

The initial real-provider verification passed in
[CI run 43296836958](https://github.com/eturkok/proofbeak/actions/runs/43296836958),
alongside the Python 3.11, 3.13, and 3.14 core jobs. Subsequent CI runs preserve the synthetic
Radicale JSON/HTML evidence as the `radicale-trial-evidence` artifact for seven days.

## Deployment increment

- [x] Pin runtime dependencies with hashes and the Python base image by digest.
- [x] Package non-root Compose and singleton Helm deployments with persistent state.
- [x] Add read-only filesystem and arbitrary-UID offline container checks.
- [x] Add chart validation and an actual kind install/REST trial to CI.
- [x] Run the deployment checks and preserve their reports (CI run 43313710253).
- [x] Add an actual pod replacement test for persistent evidence and idempotency.
- [x] Run the restart verification in CI (run 43316814424).
- [x] Add actual ingress/egress probes with positive controls on Calico.
- [x] Run the policy-enforcement probes and preserve their report (CI run 43375288148).

Host/cluster-specific storage, identity, TLS and networking are validated when that deployment
is requested. A Kubernetes deployment needs a suitable CNI for policy enforcement; direct
Python/container use does not require Kubernetes. Distribution-specific checks are optional
compatibility work, outside the generic product/pilot exit criteria.

## Pilot decision gates

- [x] Measure candidate wall/CPU time and instrumented HTTP attempts including cleanup.
- [x] Implement offline paired A/B scoring with sealed reviewer references and explicit missing data.
- [x] Reject simulated evidence, unstable repeats and invalid budgets from pilot continuation signals.
- [x] Prepare three-developer onboarding/return observation templates and pilot instructions.
- [ ] Collect real developer questions, independent reviews and external agent A/B attempts.
- [ ] Observe actual onboarding and fourteen-day return usage; make the continuation decision.

See [pilot collection and scoring](pilot.md). Regression-test studies are not real pilot results.

Keep failure and inconclusive cases separate. Use twelve decision cases, including at least
four real-provider observations. Compare documentation alone to documentation plus Proofbeak
with the same model, task, candidates, and budget. Two independent reviewers establish the
reference outcomes before seeing agent answers.

Proposed continuation signals from the plan: zero false-positive cases, at least two more
correct decisions across twelve valid cases, two of three developers producing their first
report within thirty minutes, and two returning with another condition within fourteen days.
Build the private catalog when two separate teams want to reuse the same test.
