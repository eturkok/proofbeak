"""Installed operator-selected connector extensions and scenario-based worker qualification."""

import importlib.metadata
import json
import re
from pathlib import Path
from uuid import uuid4

from proofbeak.models import canonical
from proofbeak.report import write_private

GROUP = "proofbeak.connectors"
PLUGIN_NAME = re.compile(r"[a-z][a-z0-9_-]{0,31}")


def registered_plugins():
    result = {}
    for point in importlib.metadata.entry_points(group=GROUP):
        if not PLUGIN_NAME.fullmatch(point.name) or point.name in result:
            raise ValueError("Installed connector entry-point names must be valid and unique.")
        result[point.name] = point
    return result


def plugin_metadata(point):
    return {"name": point.name, "distribution": point.dist.metadata["Name"], "version": point.dist.version}


def list_plugins():
    return [plugin_metadata(point) for _, point in sorted(registered_plugins().items())]


def load_plugin(name):
    if not isinstance(name, str) or not PLUGIN_NAME.fullmatch(name):
        raise ValueError("Choose a valid installed connector name.")
    point = registered_plugins().get(name)
    if point is None:
        raise ValueError("Install the operator-approved connector package in the coordinator and worker.")
    from proofbeak.connectors import Connector
    constructor = point.load()
    if not isinstance(constructor, type) or not issubclass(constructor, Connector):
        raise ValueError("Connector entry points must expose a Connector subclass.")
    return constructor, plugin_metadata(point)


def qualify(connector, scenario):
    """Run a caller-supplied scenario in the actual bounded worker, never certify unseen behaviors."""
    from proofbeak.adapters.capability import CapabilityAdapter
    from proofbeak.ci import gate
    from proofbeak.executor import SubprocessExecutor
    from proofbeak.packs import builtins

    adapter = CapabilityAdapter(connector)
    pack, trial = builtins().prepare(scenario)
    if pack.id != "scenario.v2":
        raise ValueError("Connector qualification takes a scenario.v2 definition.")
    pack.validate_adapter(trial, adapter)
    profile = canonical(adapter.profile)
    if any(isinstance(value, str) and len(value) >= 8 and value in profile for value in connector.secrets.values()):
        raise ValueError("Connector profiles must not include injected credential values.")
    executor = SubprocessExecutor()
    executor.validate([adapter])
    directory = Path(".proofbeak/connector-qualification") / uuid4().hex
    directory.mkdir(mode=0o700, parents=True)
    ledger = directory / "ledger.sqlite3"
    result = executor.run(scenario, [adapter], ledger, uuid4().hex)
    report = result.payload["report"] if result.payload else None
    status = gate(report) if report else {"gate": "inconclusive"}
    # Keep the actual worker ledger even on a crash; explicit cleanup must remain possible.
    recovery = str(ledger) if not report or any(c["cleanup_status"] != "cleaned" for c in report["candidates"]) else None
    return {"contract": "proofbeak.connector-qualification.v1", "connector": connector.profile.get("plugin", {"kind": connector.settings["kind"]}),
            "worker": "bounded_subprocess", "profile_contains_no_supplied_secrets": True,
            "scope": "Only the supplied scenario and configured operations were exercised.",
            "gate": status["gate"], "cleanup_ledger": recovery, "reason_code": result.reason, "report": report}


def scaffold(directory, name):
    directory = Path(directory)
    if not isinstance(name, str) or not PLUGIN_NAME.fullmatch(name):
        raise ValueError("Use a lower-case connector name of at most 32 characters.")
    if directory.exists():
        raise ValueError("Choose a new connector directory; existing files are never replaced.")
    module = "proofbeak_connector_" + name.replace("-", "_")
    directory.mkdir(mode=0o700, parents=True)
    project = ('[build-system]\nrequires = ["setuptools>=77"]\nbuild-backend = "setuptools.build_meta"\n\n'
        f'[project]\nname = "proofbeak-connector-{name}"\nversion = "0.1.0"\n'
        'requires-python = ">=3.11"\ndependencies = ["proofbeak[connectors]>=0.8,<1"]\n\n'
        f'[project.entry-points."proofbeak.connectors"]\n{name} = "{module}:ExampleConnector"\n\n'
        '[tool.setuptools.packages.find]\nwhere = ["src"]\n')
    code = ('"""Operator-installed connector. Extend the transport or bindings for your service."""\n\n'
            'from proofbeak.connectors.rest import RESTConnector\n\n\n'
            'class ExampleConnector(RESTConnector):\n'
            '    """A working scoped REST starting point; constructors must perform no provider I/O."""\n'
            '    version = "0.1.0"\n')
    operation = {"input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
                 "output_schema": {"type": "object", "properties": {"status": {"type": "string"}}, "required": ["status"]},
                 "read_only": True, "binding": {"method": "GET", "path": "/health", "project": {"status": ["json", "status"]}}}
    providers = {"capability:example": {"kind": "plugin:" + name, "connection": {"endpoint": "http://127.0.0.1:8765"},
                                       "operations": {"health.read": operation}}}
    scenario = {"id": "scenario.v2", "name": "The configured service reports ready", "fixture": {}, "setup": [], "steps": [],
                "observe": [{"id": "health", "op": "health.read", "args": {}}], "require": [],
                "expect": [{"name": "ready", "actual": ["after", "health", "status"], "op": "eq", "value": "ready"}]}
    readme = (f"# {name} connector\n\nThis operator-installed package starts with a real read-only JSON health check.\n\n"
        "Install with `python -m pip install -e .` in both the coordinator and worker environment.\n"
        "Edit providers.json to point to your authorized loopback service (or an HTTPS origin).\n"
        "Run `proofbeak connector qualify scenario.json --providers providers.json --candidate capability:example --output qualification.json`.\n\n"
        "Extend the registered operations or the Connector implementation for your protocol.\n"
        "Mutations need owned-resource creation, independent ownership inspection and confirmed cleanup.\n"
        "Observers must be independently read-only. Use environment names for credentials; keep values out of profiles and errors.\n"
        "Keep constructors offline. Package code is trusted deployment code, not an untrusted sandbox.\n"
        "Qualification covers the supplied scenario; add scenarios for ownership conflicts, rollback and failure cleanup.\n")
    for path, content in ((directory / "pyproject.toml", project), (directory / "src" / module / "__init__.py", code),
                          (directory / "providers.json", json.dumps(providers, indent=2) + "\n"),
                          (directory / "scenario.json", json.dumps(scenario, indent=2) + "\n"), (directory / "README.md", readme)):
        write_private(path, content, overwrite=False)
    return {"directory": str(directory), "kind": "plugin:" + name, "entry_point_group": GROUP}
