# Proofbeak

**Try a service before your agent chooses it.**

Proofbeak runs a consumer's acceptance condition against candidate services and returns the
observations behind each result. The agent runtime stays outside Proofbeak. No LLM, product
account, license key, or external inference service is required by the local core.

This is an early development version based on the
[product and project plan](https://chatgpt.com/library?file_id=libfile_f45914e128bc819185b293e71955b439&account_id=personal).
The local CLI has a domain-neutral execution core and registered trial packages. It is not
the finished pilot. Development happens on feature branches;
changes reach `main` only through reviewed pull requests.

## The first question

> Move only the second occurrence of my four-week meeting from 10:00 to 11:00.
> Keep all other occurrences and durations unchanged.

The fixed fixture is December 1, 8, 15, and 22, 2026, in `Europe/Istanbul`.
Proofbeak reads the baseline, applies the operation, then performs another read. A successful
write response is never treated as evidence that the condition passed.

| Outcome | Meaning |
| --- | --- |
| `pass` | Every acceptance condition was observed in this trial. |
| `fail` | At least one condition was observably violated. |
| `inconclusive` | Missing permissions, invalid baseline, ambiguous writes, or unavailable observations prevented a conclusion. |

Cleanup status is separate from the verdict. A passing trial with failed cleanup exits with an
operational error and leaves a durable cleanup record.

## Registered trial packages

The worker owns reservations, execution order, evidence, and cleanup. A versioned trial
package owns its closed schema, baseline validation, and domain-specific metrics. Provider
adapters declare which packages they support; an incompatible combination is rejected
before a run or a resource is created.

| Package | Acceptance condition | Available adapters |
| --- | --- | --- |
| `calendar.move_one.v1` | Move one weekly occurrence while preserving the other instances and durations. | Radicale; Google adapter awaiting live verification; labeled simulation controls |
| `storage.put_same_key.v1` | Repeating a write to the same key preserves one object and the same content. | Real local filesystem reference adapter |

```bash
PYTHONPATH=src python3 -m proofbeak packs
PYTHONPATH=src python3 -m proofbeak run examples/storage.put_same_key.v1.json \
  --candidate filesystem --key storage-first-trial \
  --output reports/storage.json --html reports/storage.html
```

The filesystem trial uses a generated disposable directory under `.proofbeak/storage` and
reads actual directory entries and file contents before cleanup. It demonstrates a second
domain using the same worker; its result describes this local adapter and filesystem,
not a remote object store such as S3. `PROOFBEAK_FILESYSTEM_ROOT` can configure the test root.
Read [the package contract](docs/trial-packs.md) to extend the built-in registry.

## Google Calendar adapter

The Google adapter implements the same calendar package with a generated secondary calendar,
independent expanded-event reads, ETag-protected instance updates, and ownership-checked
cleanup. It is protocol-tested with an injected transport; a real Google account trial has
not been run yet. Injected transports are marked as simulation evidence.

After configuring a short-lived OAuth token and a separate test account in your local
environment, check identity without creating anything:

```bash
PYTHONPATH=src python3 -m proofbeak google-preflight
```

To run the same fixture against both providers once their test-account access is available:

```bash
PYTHONPATH=src python3 -m proofbeak run examples/calendar.move_one.v1.json \
  --candidate radicale --candidate google-calendar --key calendar-comparison-1 \
  --output reports/comparison.json --html reports/comparison.html
```

The CLI never writes to a primary calendar or sends participant invitations. See
[Google account setup and live verification](docs/google-calendar.md); do not send tokens
in chat or commit them. The `.env.example` file documents variables but is not loaded by
the CLI automatically. Core CI has no Google credentials and never invokes a live Google trial.

## Run the offline controls

Python 3.11+ on Linux or macOS is sufficient; this path has no third-party dependencies.

```bash
PYTHONPATH=src python3 -m proofbeak validate examples/calendar.move_one.v1.json
PYTHONPATH=src python3 -m proofbeak run examples/calendar.move_one.v1.json \
  --candidate simulation:correct \
  --candidate simulation:move_all \
  --candidate simulation:duplicate \
  --candidate simulation:permission_denied \
  --key offline-controls-1 \
  --output reports/controls.json \
  --html reports/controls.html
```

Expected outcomes: `pass`, `fail`, `fail`, `inconclusive`. Exit code `2` is expected for that
combined demonstration. Open `reports/controls.html` to inspect each condition and the raw
before/after records. **These are simulations, not measured Google or Radicale results.**
Simulations are explicitly excluded from provider comparison eligibility.

After installing the package, use `proofbeak` instead of `PYTHONPATH=src python3 -m proofbeak`:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[calendar,dev]'
```

## Try a real local Radicale server

The adapter uses a new disposable CalDAV collection for every trial. It writes a recurring
VEVENT and one `RECURRENCE-ID` exception. Observations come from separate server-expanded
CalDAV REPORT requests. It never writes to an existing calendar or sends invitations.

```bash
docker compose up --build -d radicale
export PROOFBEAK_RADICALE_URL=http://127.0.0.1:5232
export PROOFBEAK_RADICALE_USERNAME=proofbeak
export PROOFBEAK_RADICALE_PASSWORD=proofbeak-local-only
proofbeak run examples/calendar.move_one.v1.json \
  --candidate radicale --key radicale-first-trial \
  --output reports/radicale.json --html reports/radicale.html
```

The Compose service is a disposable loopback-only test server with a documented test password
and temporary storage. Use a separate test account for other deployments. Remote plaintext
HTTP endpoints are rejected. Docker runs the service as a non-root user.

If calendar dependencies are missing, the result is `inconclusive` with
`calendar_dependency_missing`. Successful real-provider operation must be established by the
integration suite or your own run; the offline controls do not establish it.

## Repeat and recover

Use the same `--key` for a retry of the same request. A completed report is returned without
another resource being created. The key is bound to the complete test, candidate order,
adapter version, observer version, and non-secret provider profile. A changed request using
the same key produces a conflict. Use a new key for an intentionally fresh observation.
The package ID and version are also part of that context. The v0.2 resource ledger uses
domain-neutral `scope_id`; cleanup still accepts records from the initial v0.1 CLI.

Run state and cleanup intent are persisted before provider writes in `.proofbeak/runs.sqlite3`.
A local process lock prevents concurrent cleanup from removing an active trial's resources.
An interrupted request is **not** silently replayed. Inspect the original process, then run:

```bash
proofbeak cleanup --candidate radicale
```

Cleanup recovery requires the original endpoint and test-account scope. Resources whose
ownership could not be established are left for manual inspection. A recovery changes the
resource ledger; it does not rewrite historical trial evidence or automatically resume work.

| Exit code | Meaning |
| --- | --- |
| `0` | All trial conditions passed and cleanup completed, or validation/cleanup succeeded. |
| `1` | At least one observed failure; no inconclusive or cleanup errors. |
| `2` | Inconclusive, interrupted, or cleanup requiring attention. |
| `3` | Invalid input, configuration, or idempotency conflict. |

## Verify

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
# Real server integration; refuses to pass by skipping dependencies:
PROOFBEAK_REQUIRE_INTEGRATION=1 PYTHONPATH=src \
  python3 tests/test_radicale_integration.py -v
```

The offline suite covers false positives, duplicate and missing identities, UTC normalization,
DST boundaries, interruption, idempotency, resource ownership, cleanup recovery, and report
escaping. CI installs calendar dependencies and runs a real Radicale process on loopback.
The initial real-server integration and the Python 3.11/3.13/3.14 core jobs
[passed in GitHub CI](https://github.com/eturkok/proofbeak/actions/runs/43296836958).
The integration job also saves its JSON/HTML observations as a `radicale-trial-evidence`
artifact; the records contain synthetic calendar data and no account password.

## Agent interfaces

The optional REST service and authenticated MCP stdio bridge expose the same trial packages
to external agents. Runs are tenant-bound, queued durably, and protected by scoped
idempotency keys. A restarted worker never blindly replays an interrupted write.

```bash
python -m pip install -e '.[agents]'
proofbeak-server init --tenant demo
proofbeak-server serve
```

Supply the one-time issued API key to your client/MCP host locally. See
[agent interface setup, tools, and recovery](docs/agent-interfaces.md). The built-in launcher
binds to loopback by default and runs bounded worker subprocesses, cancellation, and crash recovery.
See [worker lifecycle and limits](docs/worker-processes.md) and the
[container and Helm deployment package](docs/deployment.md). Production identity remains pending.
Core CLI usage remains dependency-free.

## Next increments

Run history, explicit context-bound evidence lookup, revocation, metadata audit, and the
seven-day raw/thirty-day summary retention policy are implemented. See
[evidence lifecycle and tenant removal](docs/evidence-lifecycle.md). Reuse never refreshes
an observation's timestamp, and deleted idempotency keys cannot silently trigger a new write.

1. Run the implemented Google adapter against a dedicated test account.
2. Preserve the first actual Google/Radicale comparison under the identical fixture and package.
3. Add production identity when a remote pilot requires it; container and Helm CI checks pass.
4. Validate real selection decisions with pilot developers before building the private hub.

See [architecture](docs/architecture.md), [milestones](docs/roadmap.md),
[Google access checklist](docs/google-calendar.md), and [contribution rules](CONTRIBUTING.md).
The core is licensed under Apache-2.0.

## Pilot preparation

Candidate reports now record trial/cleanup wall time, worker CPU and instrumented HTTP attempts.
External agent tokens and cost stay unmeasured. The offline proofbeak-pilot command prepares
empty study templates, seals two-reviewer references, and scores externally collected paired
agent attempts plus developer onboarding/return observations.
See [the pilot protocol](docs/pilot.md). No actual human pilot or decision-quality claim has
been made; simulations and incomplete measurements cannot pass the continuation gates.
