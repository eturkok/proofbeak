"""Operator-selected protocols. User scenarios contain logical operation names only."""

import copy
import re

from proofbeak.adapters.base import ProviderError, Resource
from proofbeak.capabilities import OperationCatalog
from proofbeak.models import fingerprint


class Connector:
    version = "0.1.0"
    evidence_level = "local_runner"

    def __init__(self, name, settings, secrets=None):
        if not isinstance(name, str) or not re.fullmatch(r"capability:[a-z][a-z0-9_-]{0,31}", name):
            raise ValueError("invalid capability candidate name")
        self.name, self.settings, self.secrets = name, copy.deepcopy(settings), dict(secrets or {})
        self.catalog = OperationCatalog(settings["operations"])
        self.profile = {"kind": settings["kind"], "settings": self.settings,
                        "provider_version": settings.get("provider_version", "unknown"), "connector_version": self.version}

    def resource(self, run_id):
        if not re.fullmatch(r"[a-f0-9]{32}", run_id):
            raise ValueError("invalid run id")
        return Resource("proofbeak-capability:" + fingerprint({"candidate": self.name, "settings": self.settings})[:16] + ":" + run_id, run_id)

    def scope(self, resource):
        if resource != self.resource(resource.scope_id):
            raise ProviderError("resource_scope_violation")
        return resource.scope_id


class ScopedConnector(Connector):
    """Mutating remote profiles must independently attest ownership and cleanup."""

    def configure_lifecycle(self):
        self.lifecycle = self.settings.get("lifecycle")
        self.mutating = any(not d["read_only"] for d in self.catalog.operations.values())
        if self.mutating:
            if not isinstance(self.lifecycle, dict) or set(self.lifecycle) != {"create", "inspect", "destroy"}:
                raise ValueError("mutating profiles require create, inspect and destroy lifecycle operations")
            for phase, op in self.lifecycle.items():
                definition = self.catalog.validate(op, {}, observation=phase == "inspect")
                if phase != "inspect" and definition["read_only"]:
                    raise ValueError("lifecycle mutation must be explicitly declared")

    def _inspect(self, resource):
        value = self._invoke(resource, self.lifecycle["inspect"], {})
        if type(value.get("exists")) is not bool:
            raise ProviderError("invalid_ownership_observation")
        return value

    def _owned(self, resource):
        value = self._inspect(resource)
        if not value["exists"] or value.get("owner") != self.scope(resource):
            raise ProviderError("ownership_unverified")

    def provision(self, resource, trial):
        self.scope(resource)
        if self.mutating:
            if self._inspect(resource)["exists"]:
                raise ProviderError("resource_conflict")
            self._invoke(resource, self.lifecycle["create"], {})
            self._owned(resource)

    def cleanup(self, resource):
        self.scope(resource)
        if self.mutating:
            if not self._inspect(resource)["exists"]:
                return
            self._owned(resource)
            self._invoke(resource, self.lifecycle["destroy"], {})
            if self._inspect(resource)["exists"]:
                raise ProviderError("cleanup_unconfirmed")

    def invoke(self, resource, operation, arguments):
        self.scope(resource)
        definition = self.catalog.validate(operation, arguments)
        if not definition["read_only"]:
            self._owned(resource)
        return self._invoke(resource, operation, arguments)


def build_connector(name, settings, secrets=None):
    from proofbeak.connectors.rest import RESTConnector
    from proofbeak.connectors.sql import SQLConnector
    from proofbeak.connectors.stream import RedisConnector
    from proofbeak.connectors.mcp import MCPConnector
    constructors = {"rest": RESTConnector, "search": RESTConnector, "model": RESTConnector,
                    "sqlite": SQLConnector, "postgres": SQLConnector, "redis": RedisConnector, "mcp": MCPConnector}
    if not isinstance(settings, dict) or settings.get("kind") not in constructors:
        raise ValueError("unknown capability connector kind")
    return constructors[settings["kind"]](name, settings, secrets)


def configured_connector(name, settings, root, tenant):
    """Resolve operator-only environment names; persist no credential in public profiles."""
    import os
    from pathlib import Path

    config = copy.deepcopy(settings)
    if config.get("kind") == "sqlite":
        config.setdefault("connection", {})["root"] = str(Path(root) / "provider-data" / tenant / name.split(":")[1])
    connection = config.get("connection", {})
    environment = connection.get("secret_env", {})
    if not isinstance(environment, dict) or any(not isinstance(key, str) or not isinstance(value, str) or
            not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", value) for key, value in environment.items()):
        raise ValueError("invalid connector secret environment mapping")
    def make():
        secrets = {key: os.environ.get(value, "") for key, value in environment.items()}
        if any(not value for value in secrets.values()):
            raise ValueError("connector secret is unavailable")
        from proofbeak.adapters.capability import CapabilityAdapter
        return CapabilityAdapter(build_connector(name, config, secrets))
    # Configuration validation itself performs no connection or subprocess launch.
    build_connector(name, config)
    return make
