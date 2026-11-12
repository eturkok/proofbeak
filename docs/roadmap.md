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

Do not claim a passing real provider or cross-provider comparison from simulation controls.
Do not build the public hub, pricing, generic natural-language compiler, or multi-DC deployment
before evidence of repeated demand.

The initial real-provider verification passed in
[CI run 43296836958](https://github.com/eturkok/proofbeak/actions/runs/43296836958),
alongside the Python 3.11, 3.13, and 3.14 core jobs. Subsequent CI runs preserve the synthetic
Radicale JSON/HTML evidence as the `radicale-trial-evidence` artifact for seven days.

## Pilot decision gates

Keep failure and inconclusive cases separate. Use twelve decision cases, including at least
four real-provider observations. Compare documentation alone to documentation plus Proofbeak
with the same model, task, candidates, and budget. Two independent reviewers establish the
reference outcomes before seeing agent answers.

Proposed continuation signals from the plan: zero false-positive cases, at least two more
correct decisions across twelve valid cases, two of three developers producing their first
report within thirty minutes, and two returning with another condition within fourteen days.
Build the private catalog when two separate teams want to reuse the same test.
