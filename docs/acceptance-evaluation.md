# Reproduce the acceptance evaluation

This exercise applies twelve author-prepared selection questions to actual running services.
It starts with a fresh operator configuration, uses the public MCP example client and existing
`scenario.v1` format, and exports each condition's measured verdict and independent evidence.
The definitions are ordinary user inputs; no new trial package or product operation is added.

See the [recorded results](acceptance-results.tr.md) and [measured JSON](acceptance-results.json):
the successful run observed 17 passing, 4 failing and 3 inconclusive candidate requirements,
with verified cleanup and mutation-free same-key/restart retrievals.

The questions are synthetic examples, not collected customer needs. The two HTTP catalogs
are deliberately different local fixtures. This is a technical usability exercise, not
independent human review, external-agent A/B scoring, novice onboarding or measured adoption.

## Inputs and reference outcomes

The [manifest](../examples/evaluation/manifest.json) records the questions, definition paths,
candidate IDs and expected outcomes before execution. References are authored by the same
developer preparing the exercise, not sealed by independent reviewers. The suite hashes the
manifest and every definition, then checks actual observations against those expectations.

| Question | Input | Configured candidates |
| --- | --- | --- |
| Retry an upload without leaving extra objects | [Repeated write](../examples/scenario.repeat_put.json) | WsgiDAV, Apache |
| Update one object and preserve its neighbour | [Update](../examples/scenario.update_and_preserve.json) | WsgiDAV, Apache |
| Delete one object and preserve its neighbour | [Delete](../examples/scenario.delete_and_preserve.json) | WsgiDAV, Apache |
| Repeat a deletion without damaging neighbouring data | [Repeated delete](../examples/evaluation/repeat_delete.json) | WsgiDAV, Apache |
| Delete an absent key and preserve existing data | [Absent key](../examples/evaluation/delete_absent.json) | WsgiDAV, Apache |
| Preserve Turkish characters, emoji and line breaks in content | [UTF-8](../examples/evaluation/utf8_roundtrip.json) | WsgiDAV, Apache |
| Keep an original backup while updating the working copy | [Backup/update](../examples/evaluation/backup_then_update.json) | WsgiDAV, Apache |
| Preserve an archive key's first value after an overwrite attempt | [Immutable requirement](../examples/evaluation/immutable_key.json) | WsgiDAV, Apache |
| Establish readiness from the actual response | [Readiness](../examples/evaluation/http_ready.json) | HTTP catalog A, B |
| Establish capacity for at least 1000 items | [Capacity](../examples/evaluation/http_capacity.json) | HTTP catalog A, B |
| Establish at least 30 days of retention | [Retention](../examples/evaluation/http_retention.json) | HTTP catalog A, B |
| Establish whether audit export is available | [Audit export](../examples/evaluation/http_audit.json) | HTTP catalog A, B |

Object candidates use the same standard upsert operation. The immutability question is
expected to reject both: an unchanged object count alone cannot prove preservation of its
original content. Missing HTTP fields are expected to remain inconclusive, rather than
being treated as false or filled from an expected value. Eligibility applies to **each
definition**; combine readiness, capacity and other conditions when all are required.
No global provider ranking follows from these independent questions.
HTTP conditions establish the returned field values; they do not independently exercise
the reported capacity, elapsed retention period or audit-export operation.

## Run locally

The object subset needs only Python 3.11+ and actual temporary files:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -p test_acceptance_cases.py -v
```

For the complete protocol exercise, install the optional interface dependencies and build
the two disposable providers. The Python HTTP fixtures need no extra dependencies:

```bash
python3 -m pip install -e '.[agents,dev]'
docker build -f deploy/Dockerfile.webdav-apache --tag proofbeak-webdav-apache:test .
docker build -f deploy/Dockerfile.webdav-wsgi --tag proofbeak-webdav-wsgi:test .
PROOFBEAK_REQUIRE_ACCEPTANCE=1 PROOFBEAK_ACCEPTANCE_REPORT_DIR=reports/acceptance \
  python3 tests/test_acceptance_integration.py -v
```

The runner creates fresh disposable provider scopes, an owner-only tenant configuration
through `proofbeak.server init`, and a new SQLite state directory. It performs the offline
configuration check, starts a real REST coordinator and invokes `examples/mcp_compare.py`
for all twelve definitions. That client discovers capabilities, validates, submits through
MCP, waits for the actual worker and verifies raw-evidence hashes before JSON/HTML export.
No provider credentials are given to the MCP client; its tenant token stays out of reports.

It then retrieves the first run with the same key, restarts the API and retrieves it again.
Both stored results must equal the original, including observation times and evidence.
Only the current validity assessment's `checked_at` changes when evidence is read again;
the comparison excludes that field and checks every other report field unchanged.
Independent WebDAV HTTP journals must show only the expected scoped mutation requests, with
zero additional requests for those retrievals. HTTP catalogs accept only GET and record
actual read paths. Deleting a missing key can return 404; that is an observed idempotent
delete attempt, not an additional state change.

## Inspect the results

Open `reports/acceptance/summary.html`, then follow each row's evidence link. The directory
also contains `summary.json`, per-case JSON/HTML reports, retry/restart exports, provider
journals and fixture image identities. Summary rows include the actual verdicts, candidate
IDs, cleanup, exact test hashes, run IDs, raw-evidence hashes and report-file digests.

The suite distinguishes `eligible_candidates`, `no_match` and `insufficient_evidence`.
It asserts the observed outcome matrix, cleanup and provenance; it does not invent an agent
answer or a reviewer score. A failing requirement is an expected and useful measurement,
not necessarily a failure of the evaluation harness.

CI runs the complete exercise as a mandatory WebDAV job step and retains
`acceptance-evaluation-evidence` for seven days. The required flag refuses a skipped suite.
Docker/network access is necessary for that step; the standalone filesystem subset remains
available without it. The container and Kubernetes suites separately test deployment;
this exercise starts the application directly in Python with fresh state.

Human pilot requirements remain tracked in [the pilot protocol](pilot.md) and
[the roadmap](roadmap.md). Passing this exercise does not fill those observations.
