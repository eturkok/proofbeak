# Develop an independently installed connector

Most new services on existing protocols need a provider profile. For a new protocol or
custom transport, publish an operator-reviewed Python package with an entry point in
`proofbeak.connectors`. Selecting `kind: plugin:<name>` in an operator-owned provider map
loads that installed entry point. Scenario JSON cannot provide module names or import paths.
Built-in kinds cannot be shadowed; duplicate installed plugin names are rejected.

```bash
proofbeak connector init ./my-connector --name my-service
python -m pip install -e ./my-connector
proofbeak connector list
proofbeak connector qualify ./my-connector/scenario.json \
  --providers ./my-connector/providers.json --candidate capability:example \
  --output reports/connector-qualification.json
```

The generated package is a working read-only REST health connector, with a provider map,
scenario and declared entry point. Point it at an authorized service and adjust its expected
result. Extend the trusted `Connector` implementation for another protocol. No provider work
may happen in the constructor. Install the same package/version in coordinator and worker;
the installed distribution name/version becomes part of evidence provenance and reuse context.

## Contract

Implement `resource(run_id)`, `provision(resource, trial)`, `invoke(resource, operation, arguments)`
and `cleanup(resource)`, and publish an `OperationCatalog`. Inputs are closed JSON objects;
outputs have declared schemas; observers are read-only. The shared `CapabilityAdapter` handles
scenario validation, operation budgets, independent observations and bounded final polling.
Use the common `Connector` resource/scope checks. Mutating remote connectors can inherit
`ScopedConnector` to require independently verified ownership before writes and cleanup.

Inject secrets through `connection.secret_env`. Do not put values in profiles, observation
metadata or error messages. Raise `ProviderError` with a stable safe code. Mutation permissions,
run isolation and confirmed cleanup remain explicit connector responsibilities.

Qualification executes the supplied scenario in the actual bounded subprocess worker and
checks its acceptance/cleanup gate and absence of injected credential values in the public
profile. Its report retains the real evidence and installed plugin identity. On interruption
or cleanup failure, the worker ledger survives under `.proofbeak/connector-qualification/`;
use `proofbeak cleanup --providers ... --candidate ... --db <ledger>` for explicit recovery.
No mutation is replayed automatically.

Add scenarios for ownership conflicts, read-only observations, invalid arguments, ambiguous
writes and failure cleanup appropriate to your protocol. One successful health scenario does
not establish write safety or certify every operation. Installed code is trusted deployment
code; subprocess limits are not an untrusted plugin sandbox. Review packages before installing.
