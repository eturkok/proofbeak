# REST and MCP interfaces

The external agent runtime chooses the condition and candidates. Proofbeak validates and
executes the trial, exposes its observations, and keeps provider credentials server-side.
The optional interfaces call the same application service and package-driven worker.

## Start a local service

```bash
python -m pip install -e '.[agents]'
proofbeak-server init --config .proofbeak/server.json --tenant demo
proofbeak-server serve --config .proofbeak/server.json --port 8765
```

Initialization creates one filesystem-only tenant and prints a random API key once. Copy it
into the local environment of the REST client or MCP host as `PROOFBEAK_API_TOKEN`. The
configuration file stores only its SHA-256, is created with mode 0600, and is never overwritten.
Use `init --providers examples/storage.providers.json` to import a registered operator-owned
provider map instead. The [MCP walkthrough](mcp-walkthrough.md) covers two real storage
services, a runnable client, evidence exports and retry behavior.
The state directory must be private to its owner (0700). Keep the configuration and state
outside version control. Neither CLI core nor the server requires an external LLM.

The server defaults to `127.0.0.1` and starts one scheduling coordinator. Trial and cleanup work
run in bounded subprocesses, described in [worker processes](worker-processes.md). All data
routes require `Authorization: Bearer <tenant-key>`. `/health` exposes readiness only. API
documentation and schema routes are disabled. For remote deployments, terminate TLS and
apply network controls outside the API. The container explicitly sets `--host 0.0.0.0`;
the Compose host port remains loopback-only and the Helm Service is ClusterIP. See
[deployment configuration](deployment.md).

Readiness and new submissions fail closed when the coordinator has stopped. A dispatch or
result-persistence failure reports the safe `coordinator_failed` reason; the operator repairs
storage and restarts the service, then inspects interrupted runs without replaying writes.
Transient retention errors retry after sixty seconds while trial dispatch continues;
`/health` reports `maintenance: deferred` until maintenance succeeds. No raw error is exposed.

## REST contract

| Method and path | Behavior |
| --- | --- |
| `GET /packs` | List package descriptions, runnable example definitions, and this tenant's provider capabilities. |
| `POST /runs` | Validate, reserve, and queue; return 202 plus `run_id` and a Location header. |
| `GET /runs/{id}` | Read queued/running/completed/interrupted state and candidate verdict summaries. |
| `GET /runs/{id}/comparison` | Read conditions and provenance after completion; raw observations are referenced separately. |
| `GET /evidence/{id}` | Read the authorized candidate's before/after observations and integrity hash. |
| `POST /runs/{id}/cleanup` | Retry only recorded cleanup on a terminal run; never rerun trial writes. |
| `POST /runs/{id}/cancel` | Cancel a queued or running tenant-owned job without replaying its writes. |

`POST /runs` takes exactly `test` and `candidates`, with `Idempotency-Key` in the header.
No tenant ID, provider URL, credential, shell command, or arbitrary profile is accepted from
the client. Body size is capped at 64 KiB, there are at most four distinct candidates, and
the closed package schema is validated before queueing.

```python
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

payload = {
    "test": json.loads(Path("examples/storage.put_same_key.v1.json").read_text()),
    "candidates": ["filesystem"],
}
request = Request("http://127.0.0.1:8765/runs", method="POST",
    data=json.dumps(payload).encode(), headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + os.environ["PROOFBEAK_API_TOKEN"],
        "Idempotency-Key": "first-remote-trial",
    })
with urlopen(request) as response:
    print(response.read().decode())
```

Repeat the same key and request to obtain the same run ID. Keys are scoped to the authenticated
tenant and bound to the full request, package/observer versions, and provider profiles. A
changed request gives 409. Each tenant may have eight queued/running jobs by default; excess
submissions give 429. Provider selection outside the tenant allowlist gives 403. Unknown and
foreign run/evidence IDs both give 404. Results not yet available give 409.

## MCP stdio bridge

Configure the MCP host to run `proofbeak-mcp` with `PROOFBEAK_API_URL` (default
`http://127.0.0.1:8765`) and `PROOFBEAK_API_TOKEN` supplied through its local secret/environment
mechanism. The token binds this bridge process to one tenant. It is not a tool argument.

The eleven tools are `list_trial_packs`, `run_trial`, `get_run`, `compare_results`, `get_evidence`,
`retry_cleanup`, `cancel_run`, `list_runs`, `find_evidence`, `revoke_evidence`, and `list_audit_events`.
Typed tools publish output schemas and return structured JSON.
Discovery supplies example trials so the agent can form a valid request without another
documentation lookup. Discovery and reads are
annotated read-only. Trial creation and cleanup are explicit writes; they are never hidden
inside a read tool. A transport retry of `run_trial` must reuse its idempotency key.

The bridge connects to an operator-configured origin only, rejects URL credentials and
remote plaintext HTTP, disables redirects and implicit proxy use, and does not print API
tokens. It uses stdio, not a separate unauthenticated HTTP MCP listener. This increment pins
the official maintained SDK v1 major (`mcp<2`) to keep its protocol surface stable; a v2 SDK
migration is a separate dependency change.

## Durable queue and recovery

Accepted jobs are committed to `queue.sqlite3` before returning 202. The embedded worker
claims one queued job transactionally and dispatches it to a subprocess, which records a resource ledger under
`ledgers/{public-run-id}.sqlite3`. The public run ID is stable across the REST and MCP
interfaces; `execution_run_id` identifies the internal trial run.

Only one service process can own a state directory. On restart, unclaimed jobs remain queued;
claimed jobs become interrupted with `recovery_required` and are never automatically replayed.
An interruption may have occurred after the provider accepted a write. Use the terminal-run
cleanup route/tool to reconcile the recorded disposable resources. Cleanup progress is
returned in `cleanup_recovery`; historical trial observations are not rewritten. A missing
or ambiguous provider resource stays unresolved when its ownership/state cannot be proven.

Pending context is revalidated before execution. Package, provider-profile, or executor-policy drift stops the
job without making a new provider write. Shutdown terminates owned workers and preserves
cleanup intent; if the coordinator has not finished, it retains exclusive ownership.

API-key tenant checks are logical access controls. Worker processes and wall-clock deadlines
bound execution. The deployment package adds a non-root, read-only container and configurable
Kubernetes egress policy. Audit and retention are described in [evidence lifecycle](evidence-lifecycle.md).
SSO/OAuth for remote clients remains pilot work.

## Operator provider configuration

Each tenant's `providers` map is an allowlist. `filesystem: {}` uses a tenant-specific generated
directory under the service state root. `simulation:correct: {}` is a labeled fault control.
Other provider entries may be added by the trusted operator:

```json
{
  "radicale": {
    "endpoint": "http://127.0.0.1:5232",
    "username": "dedicated-test-user",
    "password_env": "DEMO_RADICALE_PASSWORD"
  },
  "google-calendar": {
    "access_token_env": "DEMO_GOOGLE_ACCESS_TOKEN",
    "test_account_env": "DEMO_GOOGLE_TEST_ACCOUNT"
  }
}
```

Use separate provider test accounts/scopes as required for each tenant. Credentials are
looked up in the service environment, never accepted in a run body, and excluded from the
queue context and evidence. A missing provider credential prevents submission/execution.

## Verification

```bash
PROOFBEAK_REQUIRE_INTERFACES=1 python tests/test_http.py -v
PROOFBEAK_REQUIRE_INTERFACES=1 python tests/test_mcp_integration.py -v
```

The protocol integration starts an actual loopback REST server and stdio MCP process,
initializes a client session, discovers typed tools, runs a real filesystem trial, reads
structured evidence, verifies idempotent retry, and rejects another tenant's run/evidence.
These are mandatory CI checks when interface dependencies are installed. No real Google
credentials or external LLM are involved.

Primary references: [FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/),
[FastAPI HTTP Bearer](https://fastapi.tiangolo.com/reference/security/), and
[official MCP SDK v1](https://py.sdk.modelcontextprotocol.io/v1/).
