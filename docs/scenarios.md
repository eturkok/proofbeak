# Define your own acceptance scenario

Describe the behavior your application needs in a JSON file, select compatible configured
services, and run that definition against each service. You do **not** add a Python package,
edit a case registry, or redeploy Proofbeak for each new condition.

`scenario.v1` identifies the scenario **format**, not a catalog entry for a particular case.
The bundled JSON files are starting points. The user's `setup`, `steps`, `observe`, `require`
and `expect` determine the trial. A new protocol or operation still needs a trusted connector;
composing operations already supported by your candidates needs only a new definition.

## Try and change a scenario

From the repository root with Python 3.11+:

```bash
PYTHONPATH=src python3 -m proofbeak validate examples/scenario.delete_and_preserve.json \
  --candidate scenario:filesystem
PYTHONPATH=src python3 -m proofbeak run examples/scenario.delete_and_preserve.json \
  --candidate scenario:filesystem --key own-scenario-1 \
  --output reports/scenario.json --html reports/scenario.html
```

This creates two real disposable files, deletes one, independently reads both and lists the
directory, then checks that only the intended object disappeared. Cleanup is recorded separately.
No cloud account, LLM, container or optional Python dependency is needed.

Copy the file and change its operations, data or conditions for your own requirement. Use a
new idempotency key for the changed definition. For example, changing `one_remaining.value`
from `1` to `7` produces `fail` on the same filesystem; the engine does not substitute a
built-in expected result. These other examples use the **same** format and connectors:

| Starting file | User-defined requirement |
| --- | --- |
| [Delete and preserve](../examples/scenario.delete_and_preserve.json) | Delete one object and preserve the other object's content. |
| [Update and preserve](../examples/scenario.update_and_preserve.json) | Update one object's content while preserving the other and the object count. |
| [Repeat a write](../examples/scenario.repeat_put.json) | Repeating a write leaves one object with the requested content. |

The examples illustrate composition; they are not a whitelist of runnable cases.

## Definition and references

All eight top-level fields are required. Empty `setup`, `steps` and `require` lists are allowed.

| Field | Meaning |
| --- | --- |
| `id` | Always `scenario.v1`. |
| `name` | Your description, up to 120 characters. |
| `fixture` | Your small JSON inputs; no provider credentials. |
| `setup` | Up to 8 operations to establish the trial data. |
| `observe` | 1–8 read-only operations, performed before and after `steps`. |
| `require` | Up to 16 conditions on the independent initial observations. |
| `steps` | Up to 16 actions to exercise the behavior. |
| `expect` | 1–16 conditions on the independent final observations. |

An operation is `{ "id": "target", "op": "object.get", "args": { "key": "probe.txt" } }`.
IDs are unique within a phase. The same observer is executed twice with the same parameters;
its result is available as `before.target` and `after.target` respectively.

Arguments and expected values may use a reference such as `{ "ref": ["fixture", "key"] }`.
References are path arrays: strings address JSON object keys, integers address array elements.
They are references to actual captured data, not expressions or executable code.

| Used in | Available reference roots |
| --- | --- |
| `setup` | `fixture`, earlier `setup` results |
| `observe` | `fixture`, `setup` |
| `steps` | `fixture`, `setup`, `before`, earlier `steps` results |
| `require` expected value | `fixture`, `before` |
| `expect` expected value | `fixture`, `before`, `after` |

For example, compare an independently read final value with its initial value:

```json
{
  "name": "other_preserved",
  "actual": ["after", "other", "content"],
  "op": "eq",
  "value": { "ref": ["before", "other", "content"] }
}
```

`require.actual` must start with `before`; `expect.actual` must start with `after` and name
an observer. A successful write acknowledgement cannot be used as the acceptance observation.
Supported predicates are `eq`, `ne`, `contains`, `length_eq`, `gt`, `gte`, `lt` and `lte`.
Numbers and booleans are distinct. Missing data does not satisfy `ne` or equality with `null`.

A failed precondition prevents the action phase and yields `inconclusive` (`invalid_baseline`).
A measured violation of an acceptance condition yields `fail`. Missing observations,
unavailable runtime references, ambiguous operations and exhausted budgets yield
`inconclusive`; later actions stop and cleanup is still attempted. Setup/action captures
and independent before/after reads remain inspectable in the evidence.

## Discover and validate before running

`GET /packs` or MCP `list_trial_packs` publishes the Draft 2020-12 `input_schema` for
`scenario.v1`, plus each candidate's `provider_capabilities.operations`. Discover configured
HTTP route names, their parameters and projected output fields there too.

Send `{ "test": <your definition>, "candidates": [<allowed IDs>] }` to
`POST /trials/validate`, or use MCP `validate_trial(test, candidates)`. Authentication and
tenant allowlists apply. This checks the definition and candidate capabilities without
creating a job, reserving an idempotency key, or contacting a provider. The response includes
`valid`, `issues`, `execution_performed: false` and `provider_access_checked: false`.
An invalid definition returns useful issues; an authorization failure remains an API error.

Validation does not prove that credentials work or that the service satisfies the condition.
Only explicit `POST /runs` / `run_trial` executes the accepted trial. Identical retries use
the same key; a changed definition or context requires a new key. Historical evidence lookup
matches the complete definition and context, not just the scenario title or format ID.

## Available connector capabilities

| Candidates | Operation | Arguments | Observed/captured fields |
| --- | --- | --- | --- |
| `scenario:filesystem`, `scenario:webdav:<alias>` | `object.put` | `key`, `content` | Write acknowledgement; acceptance must use a later read. |
| Same object candidates | `object.get` | `key` | `key`, `exists`; `content` and content hash when present. |
| Same object candidates | `object.list` | none | `keys`, `count` within the generated test scope. |
| Same object candidates | `object.delete` | `key` | Write acknowledgement; acceptance must use a later read. |
| `scenario:http:<alias>` | `http.get` | configured literal `route`, `params` object | `json`; also `raw` when a projection is configured. |

Object trials use generated disposable directories/collections, not existing application
objects. Keys are simple filenames up to 64 characters; content is 1–4096 UTF-8 bytes.
The initial HTTP connector supports public JSON reads only, without client-supplied methods,
headers, credentials or URLs. A candidate must support every requested operation.

### Two real WebDAV services through MCP

Use dedicated test services; these fixtures contain synthetic data:

```bash
python3 -m pip install -e '.[agents]'
docker compose -f compose.storage.yaml up --build -d
export PROOFBEAK_WEBDAV_WSGIDAV_PASSWORD=proofbeak-local-only
export PROOFBEAK_WEBDAV_APACHE_PASSWORD=proofbeak-local-only
proofbeak-server init --config .proofbeak/scenarios-server.json \
  --state .proofbeak/scenarios-state --providers examples/scenario.providers.json --tenant demo
```

Copy the printed tenant API key into the client's `PROOFBEAK_API_TOKEN` environment variable.
Start the API in another terminal with the same provider environment:

```bash
proofbeak-server serve --config .proofbeak/scenarios-server.json --port 8766
```

Then run the client:

```bash
export PROOFBEAK_API_URL=http://127.0.0.1:8766
python3 examples/mcp_compare.py --spec examples/scenario.delete_and_preserve.json \
  --candidate scenario:webdav:wsgidav --candidate scenario:webdav:apache \
  --key own-two-service-trial --output reports/scenario-comparison.json \
  --html reports/scenario-comparison.html
```

Change `--spec` to your own JSON definition without changing the server registry. The client
discovers, validates, submits, reads and exports evidence. Stop the API and run
`docker compose -f compose.storage.yaml down` after the experiment. See the
[MCP walkthrough](mcp-walkthrough.md) for client credentials and recovery details.

### Different HTTP response shapes, the same condition

The operator configures an origin and named GET routes for each candidate. For example,
candidate A might return `{ "item": { "enabled": true } }` from `/items/probe`, while B
returns `{ "payload": { "active": false } }` from `/records/probe`. Their provider map is:

```json
{
  "scenario:http:catalog-a": {
    "endpoint": "https://a.example.test",
    "routes": { "item": { "path": "/items/{key}", "project": { "ready": ["item", "enabled"] } } }
  },
  "scenario:http:catalog-b": {
    "endpoint": "https://b.example.test",
    "routes": { "item": { "path": "/records/{key}", "project": { "ready": ["payload", "active"] } } }
  }
}
```

These hostnames are illustrative, not working providers. Use your own accessible test origins.
The projection copies actual fields into a common shape; it does not compute expected values
or supply defaults. Original JSON is retained as `raw`. The caller can then submit:

```json
{
  "id": "scenario.v1",
  "name": "The candidate exposes an enabled test item",
  "fixture": { "key": "probe" },
  "setup": [],
  "steps": [],
  "observe": [{ "id": "item", "op": "http.get", "args": {
    "route": "item", "params": { "key": { "ref": ["fixture", "key"] } }
  } }],
  "require": [],
  "expect": [{ "name": "ready", "actual": ["after", "item", "json", "ready"],
    "op": "eq", "value": true }]
}
```

With those observed responses A passes and B fails. Missing `ready` is inconclusive.
Read-only scenarios need no action phase and create no remote resources. HTTPS is required
for remote origins; loopback HTTP is allowed. Paths are operator-owned templates; values
are encoded as path parameters. Redirects and implicit proxy use are disabled.
Parameter values must be single path segments: separators, percent escapes, control
characters and `.`/`..` are rejected, including values captured from an earlier response.

For CLI use, configure `PROOFBEAK_HTTP_CATALOG_A_URL` and `PROOFBEAK_HTTP_CATALOG_A_ROUTES`
(the route map as JSON), then select `scenario:http:catalog-a`. Server profiles use the
provider map above, imported with `server init --providers`.

## Bounds and verification

The request budget is 64 KiB, with at most four candidates. Fixtures are at most 16 KiB;
JSON values have bounded depth, array/object size and string length. Each candidate has a
64 KiB captured-observation budget and at most 128 provider HTTP attempts, including
cleanup. HTTP response bodies are at most 8 KiB; object listings are bounded at 32 entries.
Large check values are displayed by hash/size with a reference to the actual evidence.
These are small controlled trials, not bulk migration or unrestricted workflow execution.

The [mandatory scenario integration](../tests/test_scenario_integration.py) runs three
definitions plus a caller-modified failing expectation through real MCP, REST, workers,
WsgiDAV and Apache. Provider journals check scoped writes and cleanup, and verify that
validation, reads and identical retries add no mutations. The
[HTTP tests](../tests/test_scenario_http.py) use actual loopback JSON services with different
response shapes. All use the same scenario format. CI retains JSON/HTML and journal evidence
as `user-defined-scenario-evidence`; these are synthetic checks, not human pilot results.
