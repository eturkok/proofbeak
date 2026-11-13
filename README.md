# Proofbeak

**Check whether a service meets your requirements. Decide with evidence.**

[PyPI](https://pypi.org/project/proofbeak/) · [Documentation](https://github.com/eturkok/proofbeak/tree/main/docs)

Proofbeak is a self-hosted acceptance-testing and evidence service for developers and AI
agents. Define the behavior your application needs, run the same test against configured
services, and inspect independently observed results before choosing an integration or release.

For example: write data through an API, read it independently, and check that the returned
fields match your requirements. Repeat with different data or candidate services; compare
the result with a previous release. Proofbeak records what happened and whether cleanup succeeded.

You define scenarios through the visual editor or JSON using connector operations, test data
and acceptance conditions. New cases and data combinations do not require changes to this package.

## Start in the browser

Use Python 3.11+ on Linux or macOS, in a virtual environment:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install 'proofbeak[agents,connectors]==0.8.0'
proofbeak studio
```

Open the displayed local address and sign in with the access key printed by the launcher.
Choose **Try a local example**, edit its values, then **Validate & preview** and **Run test package**.
The example uses actual disposable SQLite data. No cloud account, model or external database
is needed. Configuration, results and signing identity persist in your private local workspace.

The [workspace guide](https://github.com/eturkok/proofbeak/blob/main/docs/workspace.md) covers
your own service profiles, visual/JSON authoring, safe retries, history, comparisons and exports.

## What you can do

| Capability | Use |
| --- | --- |
| Guided authoring | Choose configured operations and independent observers; specify your expected results. |
| Suites and parameter matrices | Run multiple scenarios and bounded data combinations against compatible services. |
| Release comparisons | Identify per-condition regressions, improvements and changed observations in compatible historical runs. |
| CI gates and reports | Export JSON, HTML, JUnit and Markdown; fail CI on rejected or incomplete evidence. |
| Web, CLI, REST and MCP | Use the same execution and evidence service from a browser, automation or an external agent. |
| Connector SDK | Install trusted independent Python connector packages for additional protocols. |
| Signed evidence | Export Ed25519-signed records and verify content offline against an independently trusted public key. |

Compose tests over configured REST APIs, MCP tools, SQLite/PostgreSQL transactions, Redis
Streams and HTTP search/model endpoints. Existing storage and calendar adapters remain
available. Provider credentials and executable bindings belong to the operator. Additional
protocols require a connector; business conditions remain yours.

## Use the CLI

The core CLI has no third-party Python dependencies. This suite tests three values against
actual disposable local files:

```bash
python -m pip install proofbeak==0.8.0
proofbeak suite example --output acceptance.json
proofbeak suite validate acceptance.json
proofbeak suite run acceptance.json --key first-suite \
  --output reports/suite.json --html reports/suite.html \
  --junit reports/junit.xml --summary reports/summary.md
proofbeak gate reports/suite.json
```

Edit the definition for your requirement and use a new key for an intentional new run.
Repeating an unchanged request and key retrieves the original execution without replaying writes.

## Read the result

| Result | Meaning |
| --- | --- |
| `pass` | Recorded observations satisfy your acceptance conditions. |
| `fail` | An observed value violates a condition. |
| `inconclusive` | Missing observations or an operational issue prevent a conclusion. |

Cleanup is reported separately. The CI gate requires actual observations, passing acceptance
and clean resources. A valid signature establishes record integrity and signer identity for
the chosen key; it does not certify a provider or prove it still behaves the same way.

## Deploy and extend

Run directly in Python, in a container or on standard Kubernetes/Helm. The supported
deployment uses one coordinator with SQLite state, tenant-bound access, bounded workers
and explicit recovery. OpenShift is optional. Remote SSO, multi-DC operation and a shared
public evidence catalog are outside this release. Technical qualification does not establish
human adoption or an improvement in external-agent decision quality.

- [Installation and artifacts](https://github.com/eturkok/proofbeak/blob/main/docs/installation.md)
- [Browser workspace](https://github.com/eturkok/proofbeak/blob/main/docs/workspace.md) · [Scenario format](https://github.com/eturkok/proofbeak/blob/main/docs/scenarios.md)
- [Suites, comparisons and CI](https://github.com/eturkok/proofbeak/blob/main/docs/suites.md)
- [Connector configuration](https://github.com/eturkok/proofbeak/blob/main/docs/capabilities.md) · [Connector SDK](https://github.com/eturkok/proofbeak/blob/main/docs/connector-sdk.md)
- [REST and MCP](https://github.com/eturkok/proofbeak/blob/main/docs/agent-interfaces.md) · [Signed evidence](https://github.com/eturkok/proofbeak/blob/main/docs/signed-evidence.md)
- [Evidence lifecycle](https://github.com/eturkok/proofbeak/blob/main/docs/evidence-lifecycle.md) · [Backup and recovery](https://github.com/eturkok/proofbeak/blob/main/docs/state-recovery.md)
- [Deployment](https://github.com/eturkok/proofbeak/blob/main/docs/deployment.md) · [Qualification and limits](https://github.com/eturkok/proofbeak/blob/main/docs/stability.md)
- [Release notes](https://github.com/eturkok/proofbeak/blob/main/CHANGELOG.md) · [Roadmap](https://github.com/eturkok/proofbeak/blob/main/docs/roadmap.md)

[Contributing](https://github.com/eturkok/proofbeak/blob/main/CONTRIBUTING.md) · [Architecture](https://github.com/eturkok/proofbeak/blob/main/docs/architecture.md) · [Apache-2.0](https://github.com/eturkok/proofbeak/blob/main/LICENSE)
