# Compose scenarios across service protocols

`scenario.v2` accepts logical operations registered by a trusted operator. A connector
publishes each operation's name, JSON Schema 2020-12 input/output contracts and read-only
policy. Callers compose setup, actions, independent observations and acceptance conditions.
Adding a condition needs a new JSON definition. Adding a protocol needs trusted connector
code; adding another endpoint or tool on a supported protocol usually needs a provider profile.

`scenario.v1` and the fixed storage/calendar formats remain available with their original
schemas and behavior. The existing twelve MCP tools expose the new format through discovery,
validation, execution, evidence and history; clients do not need a second control API.

Connector developers implement `resource`, `provision`, `invoke` and `cleanup`, expose an
`OperationCatalog`, and register the trusted constructor in `build_connector`. The same
constructor must be installed in the coordinator and worker. `CapabilityAdapter` supplies
execution, validation, observations and polling, so the worker needs no per-case branches.
Registration is deployment code, never an import path or executable supplied by a scenario.

## Supported connectors

| Kind | Configured operations | Isolation and observations |
| --- | --- | --- |
| `rest` | Named GET/HEAD/POST/PUT/PATCH/DELETE requests, path/query parameters, JSON bodies, status codes and output projections | Mutations require a run-scoped path and ownership lifecycle; acceptance reads run separately. |
| `mcp` | Named tools discovered and called using the MCP SDK, over stdio or Streamable HTTP | Remote input schemas are checked; trusted bindings inject scope and define ownership/cleanup. |
| `sqlite` | Record operations or named parameterized SQL transactions | A private database per run, independent connections, actual commit/rollback and database-enforced read-only observations. |
| `postgres` | The same record contract or named SQL transactions | A schema per run with an ownership row, statement timeouts, independent connections and read-only transactions. |
| `redis` | Publish JSON to a stream and independently read events/results | A run-owned namespace, configurable channels, atomic ownership-checked deletion; an external consumer can produce results. |
| `search` | HTTP search requests with operator-defined projections | Ranked results can be checked against caller-supplied relevance references. |
| `model` | HTTP model requests, including OpenAI-compatible JSON endpoints | Output schema/content and measured response time; no embedded model, required cloud account or automatic model grading. |

Search/model kinds use the REST binding contract. Marking an inference or query request as
read-only is an operator declaration of its application behavior, even if its HTTP method
is POST. External MCP tool annotations do not grant permission to run a mutation as an observer.

## Local SQL quick start

Install the connector extra (Python 3.11+ on Linux/macOS):

```bash
python -m pip install 'proofbeak[connectors]==0.8.0'
proofbeak example --trial scenario.v2 --output scenario-v2.json
```

Save this minimal map as `providers.json`:

```json
{"capability:sqlite": {"kind": "sqlite", "connection": {}}}
```

```bash
proofbeak validate scenario-v2.json --providers providers.json --candidate capability:sqlite --json
proofbeak run scenario-v2.json --providers providers.json --candidate capability:sqlite \
  --key sql-first --output reports/sql.json --html reports/sql.html
```

The example writes one row, reads it through a separate connection, checks its value and
removes the owned test database. Edit the JSON to express your own requirement. Use a new
key for a changed request. A retry with the same request/key retrieves the original run.

The core and old formats still have no third-party dependencies. `scenario.v2` requires
the `connectors` extra. PostgreSQL also requires `postgres`; the REST service requires
`agents`. The standard service image already contains JSON Schema and MCP dependencies;
PostgreSQL needs the optional driver added to a derived image or Python environment.

## Operator profiles and credentials

[The provider map](../examples/capabilities.providers.json) illustrates all seven kinds.
Use a minimal map containing the candidates you actually configure; its example HTTPS
origins, executable paths and model names are placeholders. A profile's logical names can
be shared by different candidates: for example, `record.put` and `record.get` can map to
REST endpoints, MCP tools or SQL, allowing the same acceptance condition to compare them.

Each operation has this shape:

```json
{
  "input_schema": {
    "type": "object",
    "properties": {"key": {"type": "string"}},
    "required": ["key"],
    "additionalProperties": false
  },
  "output_schema": {"type": "object"},
  "read_only": true,
  "binding": {"method": "GET", "path": "/scopes/{scope}/items/{key}"}
}
```

Binding fields belong to the connector, not to the scenario. HTTP bindings support `body`,
`query`, `project` and accepted `statuses`. In body/query templates, `{"arg":"key"}` reads
a validated argument and `{"scope":true}` injects the current run's scope. Path `{scope}`
is always injected by the runner. Other path placeholders name validated arguments.
The scenario cannot override scope, choose a URL, provide a SQL statement or launch a command.

`project` maps output names to observed JSON paths. HTTP paths start at the actual response
envelope, such as `["json","items"]` or `["status"]`. MCP paths start at structured tool
output (or one JSON text result). Missing projected fields remain missing and cannot pass a
condition. Output schemas are checked before a result becomes an observation.

For APIs that return structured model output inside a JSON string, `decode_json` lists
projected fields to parse as JSON before checking them. For example, project `answer` from
`["json","choices",0,"message","content"]` and set `"decode_json":["answer"]` to apply
an object schema to that model response. Invalid JSON and duplicate keys remain invalid
observations. Other fields stay unchanged.

Keep credentials in the operator process's environment. `connection.secret_env` maps a
private field to its environment variable name: PostgreSQL uses `dsn`; Redis uses `password`
and optionally `username`; REST uses header names such as `Authorization`; MCP stdio uses
child environment names, and MCP HTTP uses header names. A bearer header environment value
includes its `Bearer ` prefix. These values cross the worker boundary through its private
pipe, not the queue, public profile or scenario. Never put credentials in binding literals.

Initialize the REST service with the same map:

```bash
proofbeak-server init --config server.json --state .proofbeak/service --providers providers.json
proofbeak-server check --config server.json --require-providers
proofbeak-server serve --config server.json --port 8765
```

Connect the existing `proofbeak-mcp` stdio bridge using the issued tenant API key. The
Proofbeak MCP control bridge remains stdio; the separate MCP *target connector* supports
both stdio and HTTP. Discovery reports candidate operations and schemas without provider
access. Actual target availability and remote MCP schemas are checked during execution.

## Owned resources and cleanup

Mutating REST/MCP profiles require three registered lifecycle operations:

```json
{"lifecycle": {"create": "scope.create", "inspect": "scope.inspect", "destroy": "scope.destroy"}}
```

These take no scenario arguments. `inspect` must independently return `exists` as a boolean
and, when present, `owner` equal to the injected scope. Provisioning refuses an existing
scope. Writes require verified ownership. Cleanup checks ownership again and confirms
absence after destruction; an acknowledgment alone is insufficient. The target service
must implement the profile's ownership/isolation contract. OpenAPI descriptions and MCP
discovery cannot establish those semantics automatically.

SQL and Redis implement their own ownership lifecycle. Redis cleanup checks the owner and
deletes its explicitly configured channels atomically. A foreign/replaced owner is not
deleted. An interrupted write is not replayed; explicit `retry_cleanup` reconciles only
recorded resources through the same profile, including from a restarted worker.

## SQL statements and transactions

Default operations are `record.put`, `record.get`, `record.list`, `record.delete` and
`record.rollback`. Record arguments are synthetic strings. For another SQL behavior,
register a named operation with a `statement` or up to eight `statements` and optional
`rollback: true`. Use `{table}` for the isolated `records` table and `:name` for bound
arguments. The last statement's rows become `rows`/`count`; results must contain JSON types.

```json
{"binding": {"statements": [
  "INSERT INTO {table}(key,value) VALUES (:key,:value)",
  "UPDATE {table} SET value=:updated WHERE key=:key"
], "rollback": true}}
```

All statements execute in one transaction. Subsequent observers use separate connections
and see committed state. SQLite enables `query_only` for read-only operations; PostgreSQL
uses `BEGIN READ ONLY`. SQL text is operator-controlled; request arguments are bound values.
PostgreSQL credentials must permit creating/dropping the isolated test schema.

## Eventual results and richer conditions

An optional block repeats **only** final observers:

```json
{"eventually": {"timeout_seconds": 5, "interval_seconds": 0.1, "stable_samples": 2}}
```

Success requires all final conditions to pass in the same sample, for the requested number
of consecutive samples. Failed observations reset the streak. Writes are never retried.
Every sample records its timestamp, observation hash and outcome. A deadline does not create
a successful observation: measured violations fail; unavailable observations, late successful
reads and insufficient stability remain inconclusive. Connector I/O has its own timeout;
the service worker's wall/CPU/memory limits remain the outer execution bound.

The format supports the original equality, containment, length and numeric predicates plus:

| Predicate | Expected value |
| --- | --- |
| `schema` | A JSON Schema 2020-12 object/boolean, literal or referenced from fixtures. Remote/dynamic references are rejected. |
| `set_eq` | An array whose members must equal the observed set, ignoring order and duplicates. |
| `recall_at_k` | `relevant` IDs, `id_field`, `k` and `min_recall`; duplicate results do not increase recall. |

References can occur inside nested operation arguments and expected values. Final conditions
reference `after.<observer>`, initial conditions reference `before.<observer>`; action
acknowledgments cannot serve as acceptance observations. The runner adds measured
`_proofbeak.elapsed_ms` to each operation result; providers cannot supply that field.

Examples: [record](../examples/capability.record.json), [stream](../examples/capability.stream.json),
[search](../examples/capability.search.json), [model](../examples/capability.model.json).
Search relevance and model expectations are supplied by the caller. A model output passing a
shape/content check establishes that condition on that observation, not general model quality.

## Import OpenAPI operations

```bash
proofbeak import-openapi openapi.json --operation getItem --operation updateItem --output operations.json
```

The command imports explicitly selected operation IDs from OpenAPI 3 JSON without contacting
the provider. It supports path/query parameters and JSON request bodies with nonrecursive
document-local references. Header/cookie parameter bindings, credentials, ownership lifecycle,
response projections and semantic read-only classification require operator review. Imported
writes cannot execute until their scope paths and lifecycle are configured. This is an
operation-binding aid, not an automatic compiler of business acceptance conditions.

## Qualification

Mandatory CI starts real HTTP and MCP stdio/HTTP targets, PostgreSQL and Redis. It checks
REST/database comparison with one condition, asynchronous consumer results, commit/rollback,
independent evidence and actual Proofbeak MCP → REST → worker execution. Separate tests
check invalid definitions, read-only SQL enforcement, changed expectations, ownership,
schema/ranking errors, read-only polling, replay safety and worker serialization.

The search/model HTTP tests use a local search corpus and deterministic protocol fixture;
they establish connector behavior and explicit checks, not external model quality or adoption.
The distribution matrix also runs `scenario.v2` over actual installed MCP/REST/SQLite
workers outside the source checkout on Python 3.11–3.14.
