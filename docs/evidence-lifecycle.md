# Evidence validity, audit, and local retention

The service now exposes a tenant's run history and an explicit evidence lookup. It does not
hide a cache hit inside a new trial request. `POST /runs` still requests execution; an
idempotency retry returns the original run. `POST /evidence/lookup` performs no provider write.

## Reuse contract

Lookup requires the exact current test, ordered candidate list, tenant, package/observer
versions, adapter versions and profiles, executor limits, and evidence-policy version.
Only intact real-provider observations with conclusive pass/fail results are reusable.
Expired, revoked, simulated, inconclusive, redacted, mismatched, or unavailable-context
records stay historical. Unknown provider versions cannot extend their validity beyond
24 hours. Passing `fresh=true` makes lookup return `fresh_requested`, without creating a run;
the agent can then explicitly submit a new trial with a new key.

Reuse returns the existing run ID and original timestamps, never a newly observed date.
These are context-bound historical observations, not a guarantee of unchanged service
behavior. Observation hashes detect inconsistent records; they are not trusted signatures.

| Interface | Purpose |
| --- | --- |
| `GET /runs?limit=25&before=<run-id>` | Bounded tenant history with an older-page cursor. |
| `POST /evidence/lookup` | Find reusable evidence for `test`, `candidates`, and optional `fresh`. |
| `POST /runs/{id}/revoke` | Prevent future reuse while keeping historical observations. |
| `GET /audit?limit=50&before=<event-id>` | Read metadata-only events for the authenticated tenant. |
| `DELETE /runs/{id}` | Erase a terminal run after its resources have been cleaned. |

MCP adds `list_runs`, `find_evidence`, `revoke_evidence`, and `list_audit_events`. The stdio
bridge remains tied to the operator-supplied tenant credential. A historical evidence read
includes `validity.reusable`, reasons, and the time of the check. A raw-observation checksum
failure returns 409 rather than serving that observation. Foreign IDs remain 404.

## Audit scope

The audit stores submission, dispatch, completion/interruption, cancellation, cleanup,
evidence reads/reuse/revocation, and retention/deletion events. Event details contain stable
reason/status codes, IDs, and request hashes. Tokens, provider credentials, and test fixture
payloads are excluded. Routine progress polling does not generate an unbounded audit row
per poll. The audit is ordinary local SQLite data; no tamper-proof signature is claimed.

## Retention

An hourly bounded maintenance pass applies the pilot policy: raw observations and fixture
payloads for seven days, summaries and metadata audit events for thirty days. The interval
starts from completion where available. Maintenance redacts the queue result and its
separate worker ledger, including duplicated fixture content. It uses SQLite secure-delete
and compaction for these local database copies. This does not erase exported reports,
external backups, provider logs, or storage snapshots outside the service's control.

The retained summary includes checks, provenance, hashes, and a redacted test marker. A raw
evidence read then returns 410; the summary remains readable until its retention ends.
After summary deletion, the ledger is removed and the owner sees `run_deleted` (410).
A minimal tombstone retains the tenant, run ID, hashed idempotency key, and deletion time so
replaying a deleted key cannot create a second provider write. It contains no fixture,
observation, provider credential, or plaintext client key. Tombstones last until tenant purge.

Active workers, locked ledgers, and unresolved cleanup defer deletion. Raw data can still be
redacted after its retention period once its ledger is inactive; the minimum resource handles
needed for safe cleanup remain. Cleanup never becomes 'successful' because data expired.
Retry cleanup before requesting deletion again. No provider API is called by retention.

For explicit maintenance with the API stopped:

```bash
proofbeak-server maintain --config .proofbeak/server.json
```

## Tenant data removal

The operator can remove stored tenant data after all its disposable resources are cleaned:

```bash
proofbeak-server purge-tenant --config .proofbeak/server.json --tenant demo
```

This requires exclusive offline ownership. It erases that tenant's queue rows, worker
ledgers, tombstones, audit rows, and service-managed filesystem data, and disables its
configured credential hash. Other tenants remain available. The operator still owns the
configuration file and should remove the deleted tenant's entry and injected secrets there.
The retained disabled credential digest prevents that old key from authenticating again.
No tenant-wide purge tool is exposed to an autonomous MCP agent.

## Verification

Regression checks cover context mismatch, expiry, revocation, simulation exclusion,
raw-observation corruption, tenant-scoped history/audit, fresh lookup, deletion replay safety,
both database copies' raw-byte removal, summary expiry, active-ledger protection, pending
cleanup deferral, and tenant purge with surviving second-tenant access. REST and MCP protocol
tests exercise discovery, lookup, history, revocation, and audit through the actual interfaces.
