# CLI controls, calendar trial and recovery

Run these commands from the repository root. For the product overview and the first
filesystem trial, start with the [README](../README.md). For service-side snapshots and
restore, use [state recovery](state-recovery.md).

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

