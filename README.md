# Proofbeak

**Try a service before your agent chooses it.**

Proofbeak runs a consumer's acceptance condition against candidate services and returns the
observations behind each result. The agent runtime stays outside Proofbeak. No LLM, product
account, license key, or external inference service is required by the local core.

This is the first development increment from the
[product and project plan](https://chatgpt.com/library?file_id=libfile_f45914e128bc819185b293e71955b439&account_id=personal).
It is an early local CLI, not the finished pilot. Development happens on feature branches;
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

## Next increments

1. Establish a passing real Radicale trial and verify Google Calendar test-account access.
2. Implement and compare the Google adapter using the identical fixture and observer.
3. Add FastAPI/Pydantic REST and MCP interfaces around the same execution service, with
   tenant-bound authorization and asynchronous run tracking.
4. Validate real selection decisions with pilot developers before building the private hub.

See [architecture](docs/architecture.md), [milestones](docs/roadmap.md),
[Google access checklist](docs/google-calendar.md), and [contribution rules](CONTRIBUTING.md).
The core is licensed under Apache-2.0.
