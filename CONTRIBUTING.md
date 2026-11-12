# Contributing

Start from the latest `main` and use a scoped `feat/`, `fix/`, or `docs/` branch.
Open a draft pull request while an increment is in progress. Do not push product changes
directly to `main` or merge without review. Keep the first pilot's exit criteria visible in
`docs/roadmap.md` and distinguish implemented, locally tested, and real-provider-verified work.

Run the offline suite with `PYTHONPATH=src python3 -m unittest discover -s tests -v`.
Adapter changes also require the actual Radicale integration job. Add regression coverage
for observed false positives, observation gaps, scope violations, and cleanup failures.

An adapter supplies `resource`, `provision`, `seed`, `read`, `apply`, and `cleanup` functions,
plus its version, non-secret profile, supported package IDs, and evidence level. Provision resources only in a
dedicated account. Declare all requested permissions in the provider documentation. Reads
must come from the provider and preserve occurrence identity; do not synthesize observations
from expected values. Raise a safe `ProviderError` reason code, never a raw credential-bearing
request or response. Simulations always use the `simulation` evidence level.

The initial worker supports Linux/macOS, Python 3.11+, and one local owner. Keep the core
small; the future REST/Pydantic and MCP boundaries should call the same application service.

Domain-specific schema and assertion logic belongs in `packs/`, outside the common worker,
store, and evaluator. Register a trusted package explicitly; trial input must never select an
import path or executable code. See `docs/trial-packs.md` for the extension contract.
