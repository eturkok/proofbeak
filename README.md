# Proofbeak

**Check whether a service does what your application needs. Keep the evidence.**

[English](README.md) · [Türkçe](README.tr.md)

Proofbeak is a self-hosted test-and-evidence service for developers and AI agents. You give
it a supported requirement, test data and candidate services. It runs the same small trial
against each candidate, reads what actually happened, and returns a result with the
observations behind it.

Use it when deciding whether a service or agent tool satisfies a specific behavior your
application depends on. Your agent can call Proofbeak through MCP or REST; you can also run
trials directly from the command line.

## A concrete example

You are building an agent that uploads files. The agent may be asked to store the same file
more than once, and your requirement is:

> Writing the same content to the same key again must leave one test-data object,
> with the same key and the same content.

You want to check this against two storage services, such as WsgiDAV and Apache WebDAV.
Proofbeak:

1. Creates a disposable test collection in each configured service and writes a test file.
2. Lists and reads the stored data to establish the starting state.
3. Writes the same content to the same key again.
4. Lists and reads the data again, then checks the object count, key and content hashes.
5. Attempts to remove its test resources and reports cleanup separately.

The result is based on those read requests. A successful upload response alone cannot
establish that the stored state meets the requirement.

Both real WebDAV fixtures pass the current repeated-write integration test. That establishes
this observed behavior for the recorded inputs and configuration; it does not rank the
services or establish concurrency, crash durability or hidden version-history behavior.
See the [complete storage example](docs/storage-comparison.md).

## What you give it, and what you get

| Input | Example |
| --- | --- |
| A condition from a registered trial package | Repeat a write and preserve one object, its key and content |
| Small, disposable test data | `probe.txt` containing a chosen string |
| Configured candidate IDs | `webdav:wsgidav`, `webdav:apache` |

Each candidate receives its own result:

| Result | Meaning |
| --- | --- |
| `pass` | All requested conditions were observed in this trial |
| `fail` | An observed value violated a requested condition |
| `inconclusive` | Permissions, an ambiguous operation or missing observations prevented a conclusion |

The JSON report and readable HTML report include the checks, expected and observed values,
before/after observations, timestamps, evidence hash, test/adapter versions and cleanup
status. A trial can pass while cleanup needs attention; both facts remain visible.

Use the result to make a service-selection or integration decision. Proofbeak supplies the
measured observations; the developer or external agent makes the final choice.

## Who is it for?

- **Agent application developers** checking a tool's behavior before letting their agent depend on it.
- **Backend and platform teams** comparing compatible services under the same acceptance condition.
- **Integration developers** turning a concrete requirement into a repeatable check and an inspectable report.

## How an agent uses it

The operator configures allowed test services and keeps provider credentials on the server.
The agent then discovers available trial packages, submits a condition and candidate IDs,
waits for completion, and reads the comparison and evidence through MCP or REST.

A retry with the same request and key retrieves the original run. Eligible historical evidence
can also be looked up explicitly in the same context. Reading evidence never starts a hidden
provider trial. The external agent/model runtime remains under your control.

Follow the [runnable MCP walkthrough](docs/mcp-walkthrough.md) for the complete two-service
flow, including discovery, execution, evidence verification and JSON/HTML exports.

## What works today?

Version **0.4.0** includes two registered trial packages:

| Package | Behavior checked | Available adapters |
| --- | --- | --- |
| `storage.put_same_key.v1` | Repeat a write while preserving object count, key and content | Local filesystem reference; WsgiDAV and Apache through WebDAV |
| `calendar.move_one.v1` | Move one recurring event occurrence while preserving the others and their durations | Real Radicale integration; optional Google adapter with protocol tests, awaiting live verification |

Storage is the primary example. The engine, evidence lifecycle and agent interfaces are shared
across packages. Supporting a new condition or service requires a developer to register and
validate a trusted trial package and/or adapter. Requests use closed JSON definitions;
this release does not compile arbitrary natural-language requirements or run arbitrary code.
See [how to add a package or adapter](docs/trial-packs.md).

## Try it locally

From a checkout, Python 3.11+ on Linux or macOS is sufficient for a real filesystem trial.
This example needs no third-party Python packages, cloud account, model service or Docker:

```bash
PYTHONPATH=src python3 -m proofbeak run examples/storage.put_same_key.v1.json \
  --candidate filesystem --key first-trial \
  --output reports/first-trial.json --html reports/first-trial.html
```

Open `reports/first-trial.html` to inspect the condition, actual observations and cleanup.
For a successful run, the CLI includes:

```text
filesystem: pass (conditions_met); cleanup=cleaned
```

This tests the local filesystem adapter. To compare two actual HTTP services, follow the
[WebDAV setup](docs/storage-comparison.md) or [MCP walkthrough](docs/mcp-walkthrough.md).
Use dedicated test accounts/scopes for external providers. The examples use synthetic data.

## Deployment and release scope

The stable scope is a controlled **single-coordinator** deployment with SQLite state.
Run directly in Python, build the container, or use standard Kubernetes/Helm. OpenShift is
an optional hosting choice. No particular cloud, Google account or LLM is required by the
core; provider-specific credentials are needed only for the integrations you select.

The service includes tenant-bound API keys, durable queueing, bounded workers, explicit
cleanup recovery, evidence validity/revocation/retention and verified offline backup/restore.
Standalone CLI stores and reports use owner-only permissions; report replacement is atomic.
Remote SSO, multi-DC coordination and a shared evidence catalog remain outside this release.

Qualification covers real providers, MCP/REST, upgrade from 0.3.0, repeated submissions,
restarts, restore, containers and Kubernetes network policies. These are bounded synthetic
checks; human adoption and agent decision-quality gains remain unmeasured.
See [stability scope and results](docs/stability.md) and [release notes](CHANGELOG.md).

## Guides

| I want to… | Read |
| --- | --- |
| Compare the two storage services | [Storage trial](docs/storage-comparison.md) |
| Connect an agent and run the complete MCP flow | [MCP walkthrough](docs/mcp-walkthrough.md) |
| Use REST, configure tenants or inspect tool contracts | [Agent interfaces](docs/agent-interfaces.md) |
| Run CLI fault controls, the calendar example or cleanup | [CLI guide](docs/cli-guide.md) |
| Add a supported condition or provider | [Trial packages and adapters](docs/trial-packs.md) |
| Understand evidence reuse, expiry and deletion | [Evidence lifecycle](docs/evidence-lifecycle.md) |
| Check configuration, upgrade, back up or restore | [State recovery](docs/state-recovery.md) |
| Deploy a container or Helm chart | [Deployment](docs/deployment.md) |
| Inspect the execution design and worker limits | [Architecture](docs/architecture.md) · [Workers](docs/worker-processes.md) |
| Review development and pilot work | [Roadmap](docs/roadmap.md) · [Pilot](docs/pilot.md) |

## Develop

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

See [contribution rules](CONTRIBUTING.md). Licensed under [Apache-2.0](LICENSE).
