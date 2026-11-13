# Proofbeak v0.7.0

Define the behavior your application needs, run the same acceptance scenario against
candidate services, and inspect the independent observations and evidence before choosing.

## What's included

- **Extensible scenarios:** `scenario.v2` composes operator-registered operations with
  JSON input/output schemas. Existing scenario and domain formats remain available.
- **Service connectors:** named REST operations and selected offline OpenAPI import;
  MCP target tools over stdio/Streamable HTTP; SQLite/PostgreSQL transactions and rollback;
  Redis Streams; HTTP search and model services.
- **Acceptance checks:** independent before/after reads, JSON Schema, set and recall@k
  checks, measured operation timings and bounded eventual observations. Polling repeats
  observations without resubmitting action steps.
- **Evidence and recovery:** tenant-bound access, context-bound evidence reuse,
  ownership-checked cleanup, durable queue and explicit recovery after interruption.

## Install

Core CLI:

```bash
python -m pip install proofbeak==0.7.0
```

CLI, REST/MCP and connector dependencies, including the optional PostgreSQL driver:

```bash
python -m pip install 'proofbeak[agents,connectors,postgres]==0.7.0'
```

Python 3.11+ on Linux/macOS is supported. Provider credentials are configured by the
operator. The core requires no cloud account or embedded model.

## Downloads

The attached wheel and source distribution are the exact tested files published to
[PyPI](https://pypi.org/project/proofbeak/0.7.0/). The attached Linux amd64 Docker archive is
the tested runtime image; it is also available as `ghcr.io/eturkok/proofbeak:0.7.0`.

```bash
sha256sum --check SHA256SUMS
docker load --input proofbeak-0.7.0-linux-amd64.tar.gz
```

On macOS, use `shasum -a 256 --check SHA256SUMS`. The loaded archive tag is
`proofbeak:distribution`. The release manifest records file hashes, the source commit,
the qualified image identity and the original build run. Registry access follows the
package owner's visibility settings.

## Validation and scope

The [qualified main run](https://github.com/eturkok/proofbeak/actions/runs/43472852676)
checked metadata and hashes, actual installed CLI/REST/MCP workflows on Python 3.11–3.14,
and rootless, read-only, offline containers. Public PyPI installation was verified for
both the core CLI and a `scenario.v2` connector trial, including evidence integrity and
cleanup. Separate [protocol tests](https://github.com/eturkok/proofbeak/actions/runs/43472852652)
and [Kubernetes checks](https://github.com/eturkok/proofbeak/actions/runs/43472852816)
qualified real REST/MCP/PostgreSQL/Redis interactions, persistent restart and network policy.

The qualified deployment scope is one coordinator with SQLite state. Search/model tests
use local reference data and deterministic protocol fixtures; they do not establish
external model quality or user adoption. Helm chart version: **0.5.0**.

[Connector setup and examples](https://github.com/eturkok/proofbeak/blob/v0.7.0/docs/capabilities.md)
· [Deployment](https://github.com/eturkok/proofbeak/blob/v0.7.0/docs/deployment.md)
· [Upgrade and recovery](https://github.com/eturkok/proofbeak/blob/v0.7.0/docs/state-recovery.md)
· [Changelog](https://github.com/eturkok/proofbeak/blob/v0.7.0/CHANGELOG.md)
