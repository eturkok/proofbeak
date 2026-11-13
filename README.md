# Proofbeak

**Check whether a service meets your requirements. Decide with evidence.**

[English](https://github.com/eturkok/proofbeak/blob/main/README.md) · [Türkçe](https://github.com/eturkok/proofbeak/blob/main/README.tr.md) · [PyPI](https://pypi.org/project/proofbeak/)

Proofbeak is a self-hosted acceptance-testing and evidence service for developers and AI
agents. Define the behavior your application needs, choose candidate services, and run the
same scenario against each one. Proofbeak reports what it observed so you can make an
informed service-selection or integration decision.

You write your own scenarios in JSON by combining available connector operations, test
data and acceptance conditions. The included examples are starting points you can adapt.

## How it works

1. **Define** the preparation, actions, observations and expected results.
2. **Choose** compatible services from the configured candidates.
3. **Validate** the definition before starting provider work.
4. **Run** the trial and check its outcome through independent reads.
5. **Inspect** the comparison, evidence and separate cleanup status.

| Result | Meaning |
| --- | --- |
| `pass` | The observations satisfy the requested conditions. |
| `fail` | An observed value violates a requested condition. |
| `inconclusive` | Missing observations or an operational issue prevent a conclusion. |

JSON and HTML reports include expected and observed values, before/after records and
evidence provenance. The developer or external agent makes the final choice.

## Quick start

Use Python 3.11+ on Linux or macOS. Install from PyPI in a virtual environment; the local
example needs no provider account or model service:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install proofbeak==0.6.0
proofbeak example --output scenario.json
proofbeak validate scenario.json \
  --candidate scenario:filesystem
proofbeak run scenario.json \
  --candidate scenario:filesystem --key first-trial \
  --output reports/first-trial.json --html reports/first-trial.html
```

Open `reports/first-trial.html` to inspect the result. The example uses disposable files.
Copy the JSON and edit it for your condition; use a new key for a changed definition.
Repeating the same request and key retrieves the original run.

Follow the [scenario guide](https://github.com/eturkok/proofbeak/blob/main/docs/scenarios.md) to author a definition and apply it to
multiple configured services.

## Interfaces and scope

Use Proofbeak through **CLI, REST or MCP**. Provider connections and credentials are
configured by the operator; the external agent runtime stays under your control.

User-defined scenarios currently support object write/read/list/delete operations and
configured read-only HTTP JSON requests. Composing these operations requires only a JSON
definition. Supporting a new protocol or operation requires a trusted connector.

Run directly in Python, in a container or on standard Kubernetes/Helm. The current deployment
scope uses one coordinator with SQLite state. See the deployment guide for configuration
and operating limits.

## Documentation

- [Installation and package artifacts](https://github.com/eturkok/proofbeak/blob/main/docs/installation.md)
- [Scenarios and examples](https://github.com/eturkok/proofbeak/blob/main/docs/scenarios.md)
- [REST and MCP integration](https://github.com/eturkok/proofbeak/blob/main/docs/agent-interfaces.md)
- [Connectors and extension contracts](https://github.com/eturkok/proofbeak/blob/main/docs/trial-packs.md)
- [Evidence history and reuse](https://github.com/eturkok/proofbeak/blob/main/docs/evidence-lifecycle.md)
- [Deployment](https://github.com/eturkok/proofbeak/blob/main/docs/deployment.md) · [Backup and recovery](https://github.com/eturkok/proofbeak/blob/main/docs/state-recovery.md)
- [Validation results](https://github.com/eturkok/proofbeak/blob/main/docs/acceptance-evaluation.md) · [Release notes](https://github.com/eturkok/proofbeak/blob/main/CHANGELOG.md)

[Contributing](https://github.com/eturkok/proofbeak/blob/main/CONTRIBUTING.md) · [Architecture](https://github.com/eturkok/proofbeak/blob/main/docs/architecture.md) · [Roadmap](https://github.com/eturkok/proofbeak/blob/main/docs/roadmap.md) · [Apache-2.0](https://github.com/eturkok/proofbeak/blob/main/LICENSE)
