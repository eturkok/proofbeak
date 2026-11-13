# Acceptance suites and release comparisons

Group your own scenarios into a `suite.v1` document. A suite runs ordinary registered
operations; adding a data combination does not add a Python case to Proofbeak.

```bash
proofbeak suite example --output acceptance.json
proofbeak suite validate acceptance.json
proofbeak suite run acceptance.json --key release-candidate-1 \
  --output reports/suite.json --html reports/suite.html \
  --junit reports/junit.xml --summary reports/summary.md
```

The example tests three string values against actual disposable local files and needs no
account, model, database server or optional connector package. Edit its candidates to use
your configured services. Capability candidates additionally take `--providers providers.json`.

Each case contains `id`, `test`, `candidates`, and optionally `matrix`. In the test, use
`{"$param":"content"}` to replace an entire JSON value from `"matrix":{"content":["Hello","Merhaba 🌍"]}`.
References inside the resulting scenario continue to use the ordinary `ref` syntax.
Dimensions are sorted for deterministic expansion; each parameter combination has a stable ID.
Every dimension must be used, values must be distinct, and expansion is capped at 64 executions,
four dimensions, 16 values per dimension and four candidates per execution. The definition
is capped at 64 KiB. Every expanded definition and candidate is validated before any work.

## Progress, cancellation and retries

REST exposes `POST /suites/validate`, `POST /suites`, `GET /suites`, `GET /suites/{id}`,
`POST /suites/{id}/cancel`, and `GET /suites/{id}/export?format=json|html|junit|summary`.
Submission uses the same `Idempotency-Key` header as trials. MCP exposes matching suite tools.
All reads, cancellation and evidence references remain tenant-bound.

The service persists all planned cases atomically and gives each suite one queue-budget slot.
One child trial is active per suite, through the existing bounded worker. A normal restart
can continue unstarted cases; completed writes are not replayed. An interrupted child or
unresolved cleanup stops the remaining plan. Explicit cancellation also stops remaining cases.
A restored snapshot interrupts pending suites instead of resuming provider work.
Same-key requests retrieve the suite; changed definitions/configurations conflict. A new
intentional execution uses a new key. CLI crashes retain the existing ledger's recovery-required
behavior and do not automatically resume an interrupted parent.

Suite definitions and parameter copies follow the existing seven-day raw retention, thirty-day
summary retention and tenant purge policy. Deleted suite keys retain replay-prevention tombstones.
These policies do not erase exported reports or CI artifacts outside the service.

## Compare releases

```bash
proofbeak diff reports/baseline.json reports/suite.json --output reports/diff.json
proofbeak gate reports/suite.json --junit reports/junit.xml --summary reports/summary.md
```

Comparisons identify regressions, improvements, changed observations and lost evidence per
condition. Cases, data, candidate identities, predicates and execution context must match.
Added/removed cases, changed expectations, corrupt evidence, simulations and incompatible
contexts remain inconclusive. Expired observations can describe a historical baseline;
comparing them does not refresh their validity or prove current provider behavior.

If only the declared provider version changes, explicitly pass `--allow-provider-version-change`.
This does not waive changed endpoints, bindings, connector/observer versions or conditions.
The REST `POST /comparisons` endpoint takes `baseline`, `current`, `kind` (`trial` or `suite`)
and optional `allow_provider_version_change`; both IDs must belong to the caller.

## CI gate

Exit codes: **0** all requirements pass with real observations and clean resources; **1**
a measured acceptance failure/regression; **2** incomplete/incompatible evidence or cleanup;
**3** invalid input or an idempotency conflict. An inconclusive result never opens the gate.
A comparison with no regression does not replace evaluating the current suite's acceptance gate.

Install Proofbeak in CI, supply operator-owned profiles and environment credentials, then run
the commands above. For GitHub Actions, the optional composite action at
`eturkok/proofbeak/.github/actions/acceptance@main` runs an installed package, writes the job
summary, uploads reports, and fails the job when acceptance or its optional baseline comparison
does not pass. Pin an immutable commit for a controlled installation. Run it only against
disposable authorized scopes. Review CI artifact access/retention: reports contain the supplied
synthetic fixtures and observations. Credentials stay in environment injection, outside reports.
