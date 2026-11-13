# Configuration, backup and restore

These offline operator commands support the single-coordinator service without contacting
any provider, external account or model. They use Python's standard library.

## Check configuration before starting

```bash
proofbeak-server check --config .proofbeak/server.json
proofbeak-server check --config .proofbeak/server.json --require-providers
```

The check validates the closed schema, unique tenant identities/token digests, provider
profiles and worker limits. Duplicate JSON keys are rejected. It creates no state directory,
database or provider resources. Output contains provider names and configuration booleans,
not endpoints, usernames, passwords or API keys. With `--require-providers`, missing provider
credentials exit with 2; invalid configuration exits with 3. A configured credential has not
been authenticated against a provider by this offline check.

## Create a snapshot

Stop the API and disable automatic restarts for the maintenance window. Keep the configuration
and provider credentials through your existing local secret-management process. The snapshot
does not include the server configuration, API tokens or environment variables.

```bash
proofbeak-server backup --state .proofbeak/server \
  --output .proofbeak/backups/before-upgrade
```

Use the actual `state_dir` from your configuration. Backup takes exclusive coordinator and
ledger locks, uses SQLite's backup API, checks database integrity/schema, and copies owned
filesystem fixture data under the state directory. External WebDAV/calendar resource data
is not backed up. The manifest records file hashes, sizes, directories, original state path,
creation time and application version. Snapshot format is `proofbeak-state.v1`.

The destination must be new and outside the state directory. Existing files/directories are
never overwritten. Symlinks, unexpected files, unknown database schemas and active owners
are rejected. Limits are 10,000 total entries and 512 MiB of file data. Files are created 0600
and directories 0700. Keep snapshots private: they contain historical fixture/evidence data.
Retention of a running service does not delete separate snapshots.

## Restore conservatively

Stop the service and its automatic restarts. Preserve the old state separately, then restore
to a new directory at the original configured path:

```bash
mv .proofbeak/server .proofbeak/server.before-restore
proofbeak-server restore --snapshot .proofbeak/backups/before-upgrade \
  --state .proofbeak/server
proofbeak-server check --config .proofbeak/server.json --require-providers
proofbeak-server serve --config .proofbeak/server.json
```

Restore validates the manifest, complete inventory, every checksum and SQLite integrity
before creating its destination. It never overwrites a directory or extracts an archive.
A failed copy is removed; after an abrupt process kill, `.restore-incomplete` blocks service
startup. Preserve and inspect an incomplete target before removing it and retrying into a
new target. Do not start another process against a target during restoration.

Completed evidence, observation timestamps, revocations, disabled tokens and deleted-key
tombstones are retained as of the snapshot. Restored queued/running jobs become `interrupted`
with reason `state_restored`; no provider operation is replayed. Inspect their ledgers and
request explicit cleanup through the existing API/MCP recovery tools as needed.

A snapshot cannot contain requests accepted after it was taken. To prevent old transport
retries from becoming unintended new provider writes, every restore generates a fresh
`idempotency_key_prefix` such as `restore:0123456789abcdef:`. Discovery (`GET /packs` or
`list_trial_packs`) and restore output publish this value. Known historical keys still
retrieve their original runs; unknown keys without the new prefix receive 409
`restore_key_prefix_required`. New trials require an explicitly chosen key beginning with
that prefix, within the existing 128-character total limit. The SQLite insertion trigger
also fences older coordinator code that does not understand the discovery field.

The MCP example reports the required prefix without changing or replaying a key automatically.
Use that prefix only for an intentionally new trial, after inspecting relevant provider
state. Repeated restores generate distinct prefixes; a key from an earlier restoration is
not automatically valid in the next one.

## Scope and limits

The snapshot is a point-in-time copy of local coordinator state, not a rollback of provider
side effects or a cross-system transaction. Requests, tombstones and revocations created
after the snapshot are absent. Reconcile external resources and retain the original state
until that recovery is complete. A restored credential revocation is only as recent as the
snapshot; rotate keys after a credential-related incident.

Resource IDs and provider profiles are never rewritten. Restoring at a different absolute
path changes filesystem profiles: historical evidence is then ineligible for current-context
reuse and original-scope cleanup may require the original location/account. Restoring at the
original path preserves those identities; it does not refresh evidence expiry.

Use trusted snapshots. Checksums detect corruption; they are not signatures or authorization
to import another operator's state. Encryption, off-host copies, backup scheduling and
storage availability remain deployment responsibilities. Upgrade from 0.3.0 is supported
by the qualification check; running older software against restored state is not a supported
downgrade procedure. Multi-writer and multi-DC operation are outside this deployment scope.
