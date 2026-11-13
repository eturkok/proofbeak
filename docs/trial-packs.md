# Trial packages and provider adapters

Proofbeak is a generic execution and evidence product. Its behavior is assembled from a
common worker, explicit domain packages, and compatible provider adapters.

## Common worker contract

`run_trial(spec, adapters, store, key, registry=None)` obtains a trusted package from its
registry and validates the specification before reserving a run. It checks every candidate's
`supported_trials` set, then executes:

1. Persist a domain-neutral resource handle and cleanup intent.
2. Provision and seed the isolated resource.
3. Read the provider and ask the package to validate the baseline.
4. Apply the accepted operation and read the provider again.
5. Ask the package to derive metrics and assess the condition.
6. Attempt cleanup and persist evidence plus a separate cleanup status.

The worker does not know event dates, object keys, or operation names. Its request fingerprint
includes the package ID/version, observer version, candidate profiles, and complete test.
Evidence format `0.2` adds package identity and version to each candidate.

## Package contract

A package declares `id` and `version` and implements:

| Method | Responsibility |
| --- | --- |
| `parse(spec)` | Validate a closed schema and return a prepared domain object; perform no external I/O. |
| `valid_baseline(trial, before)` | Check the first independent read against the fixture. |
| `assess(trial, before, after)` | Derive domain metrics from observations and return verdict, reason, and checks. |

Both built-in packages use the common `evaluate()` function for strict metric comparison.
A missing metric is inconclusive. A measured value that violates its expected value fails.
The package is responsible for supplying complete and honest metrics; the common evaluator
does not make an arbitrary package trustworthy.

Package registration is a developer-controlled allowlist. The CLI lists built-ins with
`proofbeak packs`. Python callers can supply another `PackRegistry` of trusted package
objects without modifying the worker. Trial input cannot load Python modules, download
packages, or name a command. There is no public plugin marketplace or natural-language
compiler in this increment.

## Adapter contract

Adapters implement `resource`, `provision`, `seed`, `read`, `apply`, and `cleanup`, declare
their compatible package IDs, and return observations with `source`, `observed_at`, and
`to_dict()`. Domain snapshots may differ; the common evidence envelope remains the same.
Resources contain `id` and `scope_id`, not a domain-specific event or object identifier.
Calendar event identity stays within its package.

The initial v0.1 ledger used `series_id` in resource handles. Cleanup deserializes that legacy
shape so upgrading the code does not make a previously recorded resource unreachable.
Package/observer/adapter version changes intentionally conflict with an old idempotency key;
use a new key for a fresh run under new semantics.

## Proving the boundary

The actual filesystem adapter exercises the storage package through the same CLI, SQLite
ledger, worker, evaluator, and HTML/JSON report path used by the calendar package. Its tests
inject duplicate files and changed content and verify that these cannot pass. Another unit
test registers a scalar-only package whose prepared value is an integer and whose adapter
has no calendar model or move method; it runs through the unchanged worker.

This establishes an extension boundary. Supporting a new service category still requires
a carefully implemented package and adapter, plus real-provider validation.
