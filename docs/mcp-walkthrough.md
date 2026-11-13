# Compare storage services through MCP

This runnable client uses the actual MCP stdio protocol, authenticated REST service and bounded
worker processes. It discovers packages and allowed candidates, submits an explicit trial,
waits for completion, verifies each raw-evidence hash, and exports JSON/HTML. No model call is
required. An external agent can use the same published tools.

## Start the local services

Use Python 3.11+ on Linux or macOS. Docker Compose packages the two disposable example
providers; Proofbeak itself can run directly in Python.

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[agents]'
docker compose -f compose.storage.yaml up --build -d
export PROOFBEAK_WEBDAV_WSGIDAV_PASSWORD=proofbeak-local-only
export PROOFBEAK_WEBDAV_APACHE_PASSWORD=proofbeak-local-only
proofbeak-server init --providers examples/storage.providers.json --tenant demo
proofbeak-server serve
```

Initialization imports registered provider profiles and prints a one-time API key. Store it
locally for the client; the private configuration contains only its digest. Existing
configuration is never overwritten. The example profile allows filesystem, WsgiDAV on
loopback port 8801 and Apache on 8802. Importing profiles makes no provider requests and does
not require provider passwords until execution/discovery. For other services, edit a copy
with an operator-owned HTTPS origin, dedicated test username and password environment name.
Raw passwords and arbitrary executable providers cannot be imported.

The password above is public fixture data for disposable loopback servers. Provider passwords
stay in the API process environment. The MCP client needs only its API token.

## Run the protocol client

In another terminal, activate the same environment. Supply the API key issued above through
your local secret environment as `PROOFBEAK_API_TOKEN`; do not put it in a report or commit.

```bash
export PROOFBEAK_API_URL=http://127.0.0.1:8765
python examples/mcp_compare.py \
  --candidate webdav:wsgidav --candidate webdav:apache \
  --key mcp-first --output reports/mcp-first.json --html reports/mcp-first.html
```

The default package is `storage.put_same_key.v1`. Use `--spec` for a local closed-schema
definition or `--trial` to select another discovered package, with compatible allowed
candidates. Exit codes follow the CLI's pass/fail/inconclusive/input conventions. Exports
are private files and cannot overwrite existing files.

Retry the same request/key with different export paths:

```bash
python examples/mcp_compare.py \
  --candidate webdav:wsgidav --candidate webdav:apache \
  --key mcp-first --output reports/mcp-retry.json --html reports/mcp-retry.html
```

The retry retrieves the same run and original observation times. An intentionally new trial
requires a new key. A client timeout does not cancel or replay provider work: use the returned
run ID to inspect it or retry the original request with the same key. If the response was
lost before a run ID arrived, the same key still resolves the accepted request.

## Use another MCP host

Configure the host's command as `proofbeak-mcp`, with `PROOFBEAK_API_URL` and
`PROOFBEAK_API_TOKEN` supplied through its secret environment. Discovery supplies package
examples and this tenant's candidate capabilities. See [the complete tool contract](agent-interfaces.md).

`find_evidence` only looks up exact-context historical evidence; even `fresh: true` creates
no job. `run_trial` is the explicit write operation. Revocation prevents evidence reuse but
does not erase historical observations or let an old key create another run.

## Verify the complete path

CI starts actual WsgiDAV and Apache containers and a REST server, then runs this client over
MCP. Provider-side method/path/status journals independently check five successful mutation
requests per provider for a first trial, zero additional mutations for retries, reads,
lookups, denied access and revocation, and another five for an explicit new key. A separate
tenant's run, comparison, evidence and cleanup requests must be rejected.

```bash
docker build -f deploy/Dockerfile.webdav-apache -t proofbeak-webdav-apache:test .
docker build -f deploy/Dockerfile.webdav-wsgi -t proofbeak-webdav-wsgi:test .
PROOFBEAK_REQUIRE_MCP_STORAGE=1 PYTHONPATH=src python tests/test_mcp_storage_integration.py -v
```

The mandatory job saves comparison JSON/HTML, image identities and workflow verification as
`mcp-storage-workflow-evidence`. Journals exclude headers, authentication and bodies. These
checks measure HTTP requests and application behavior; they do not prove internal disk-write
counts, exact-once execution, concurrent-writer safety or better LLM decisions. See
[the storage condition's scope](storage-comparison.md).

Stop the API with Ctrl-C, then remove disposable providers with
`docker compose -f compose.storage.yaml down`. Retain the private API state for history and
idempotency; deleting it removes those local guarantees.
