# Delivery milestones

Based on the November 12, 2026 product plan. The sixty-hour estimate is a planning assumption,
not recorded development time. Weekly availability and external-account access set the pace.

| Stage | Planned effort | Exit condition | Branch |
| --- | --- | --- | --- |
| Foundation | First 12 h | Closed fixture, CLI, real Radicale evidence, Google access check, two real user selection questions | `feat/calendar-trial-foundation` |
| Provider comparison | Next 12 h | Google and Radicale under the same test; faults caught; cleanup reported | `feat/google-calendar-adapter` |
| Agent interfaces | Next 12 h | REST and MCP; authenticated tenant-bound runs; safe retries; two clients | `feat/agent-interfaces` |
| Private evidence hub | Next 12 h | Context-bound validity and reuse; decision-quality experiment | `feat/private-evidence-hub` |
| User pilot | Last 12 h | Three developers; measured decision quality, false positives, adoption, and a continuation decision | Separate scoped branches |

## Foundation status

- [x] Fixed four-occurrence Istanbul fixture and explicit acceptance conditions.
- [x] Dependency-free local CLI and deterministic observer.
- [x] Fault controls: correct, move-all, duplicate, permission-denied, ambiguous write.
- [x] SQLite idempotency, resource ledger, quarantine, explicit cleanup recovery.
- [x] JSON evidence and portable HTML comparison report.
- [x] Radicale adapter, container setup, and real-server integration test implementation.
- [x] Run and pass the real Radicale integration in GitHub CI (November 12, 2026).
- [ ] Verify Google test-account read/write access with the product owner.
- [ ] Obtain two recent real service-selection questions from developers.

## Generic core increment

- [x] Move calendar schema, fixture, and identity rules into a versioned package.
- [x] Use a domain-neutral adapter lifecycle and resource handle in the worker.
- [x] Bind package version and adapter compatibility before reservation.
- [x] Run a second package against actual disposable filesystem resources.
- [x] Preserve cleanup deserialization for the original resource ledger.
- [x] Add a protocol-tested Google adapter for the unchanged calendar package.
- [ ] Verify it on a dedicated real Google account and preserve cross-provider evidence.

## Agent interface increment

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
- [ ] Validate an actual OpenShift install and NetworkPolicy enforcement on the target CNI.
- [x] Add an actual pod replacement test for persistent evidence and idempotency.
- [x] Run the restart verification in CI (run 43316814424).
- [x] Add actual ingress/egress probes with positive controls on Calico.
- [x] Run the policy-enforcement probes and preserve their report (CI run 43375288148).

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
