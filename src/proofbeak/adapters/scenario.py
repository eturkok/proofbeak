"""Reusable capabilities and workflow execution, independent of individual acceptance cases."""

import copy
import json
import re
from datetime import UTC, datetime
from urllib.parse import quote, urlsplit

from proofbeak.adapters.base import ProviderError, Resource
from proofbeak.adapters.filesystem import FilesystemAdapter
from proofbeak.adapters.http import Transport
from proofbeak.adapters.webdav import WebDAVAdapter
from proofbeak.models import canonical, fingerprint, keys
from proofbeak.packs.scenario import (ARGUMENTS, IDENTIFIER, READ_ONLY, ScenarioSnapshot,
                                     json_value, path as reference_path, resolve, validate_arguments)
from proofbeak.packs.storage import StorageTrial, content_hash

OBJECT_OPERATIONS = frozenset({"object.put", "object.get", "object.list", "object.delete"})


class JSONReadConnector:
    """Operator-defined GET routes; clients cannot supply origins, methods, headers or URLs."""

    version = "0.1.0"
    evidence_level = "local_runner"

    def __init__(self, name, endpoint, routes, transport=None):
        url = urlsplit(endpoint)
        if not isinstance(name, str) or not re.fullmatch(r"http:[a-z][a-z0-9_-]{0,31}", name) \
                or url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password \
                or url.path not in {"", "/"} or url.query or url.fragment:
            raise ValueError("configure a named HTTP JSON origin without credentials")
        if url.scheme == "http" and url.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("remote HTTP JSON endpoints require HTTPS")
        if not isinstance(routes, dict) or not 1 <= len(routes) <= 16:
            raise ValueError("configure one to 16 GET routes")
        self.parameters, self.paths, self.projections = {}, {}, {}
        for route, definition in routes.items():
            template, projection = definition, {}
            if isinstance(definition, dict):
                keys(definition, {"path", "project"}, "GET route mapping")
                template, projection = definition["path"], definition["project"]
                if not isinstance(projection, dict) or not 1 <= len(projection) <= 16:
                    raise ValueError("projection needs one to 16 named JSON paths")
                for field_name, parts in projection.items():
                    if not isinstance(field_name, str) or not re.fullmatch(IDENTIFIER, field_name):
                        raise ValueError("invalid projection field name")
                    reference_path(parts)
            if not isinstance(route, str) or not re.fullmatch(IDENTIFIER, route) or not isinstance(template, str) \
                    or len(template) > 512 or not template.startswith("/") or template.startswith("//"):
                raise ValueError("invalid named GET route")
            parameters = re.findall(r"\{(" + IDENTIFIER + r")\}", template)
            sample = re.sub(r"\{(" + IDENTIFIER + r")\}", "parameter", template)
            if len(parameters) > 8 or len(set(parameters)) != len(parameters) \
                    or not re.fullmatch(r"/[A-Za-z0-9_./~-]*", sample) \
                    or any(part in {".", ".."} for part in sample.split("/")):
                raise ValueError("GET routes must be fixed paths with simple named placeholders")
            self.parameters[route] = set(parameters)
            self.paths[route], self.projections[route] = template, copy.deepcopy(projection)
        self.name, self.endpoint, self.routes = name, endpoint.rstrip("/"), copy.deepcopy(routes)
        self.transport = transport or Transport(max_calls=128, max_bytes=8192)
        if transport is not None:
            self.evidence_level = "simulation"
        self.profile = {"endpoint": self.endpoint, "routes": self.routes, "provider_version": "unknown",
                        "permissions": "allowlisted_get_only", "remote_resource_creation": False,
                        "transport": "injected" if transport is not None else url.scheme}

    def resource(self, run_id):
        if not re.fullmatch(r"[a-f0-9]{32}", run_id):
            raise ValueError("invalid run id")
        return Resource("proofbeak-json:" + self.name + ":" + run_id, run_id)

    def provision(self, resource, trial):
        if resource != self.resource(resource.scope_id):
            raise ProviderError("resource_scope_violation")

    def cleanup(self, resource):
        self.provision(resource, None)  # No remote resource was created by this read-only connector.

    def get(self, resource, arguments):
        self.provision(resource, None)
        route, parameters = arguments["route"], arguments["params"]
        if route not in self.routes or set(parameters) != self.parameters[route]:
            raise ProviderError("scenario_operation_not_allowed")
        path = self.paths[route]
        for name, value in parameters.items():
            path = path.replace("{" + name + "}", quote(str(value), safe=""))
        response = self.transport.request("GET", self.endpoint + path,
                                          headers={"Accept": "application/json"}, allowed=(200,))
        try:
            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("duplicate JSON key")
                    result[key] = value
                return result
            data = json.loads(response.body, object_pairs_hook=unique)
            json_value(data)
            if len(canonical(data).encode()) > 8192:
                raise ValueError("oversized JSON observation")
        except (ValueError, TypeError, RecursionError):
            raise ProviderError("invalid_observation") from None
        if self.projections[route]:
            projected = {}
            for name, parts in self.projections[route].items():
                try:
                    projected[name] = resolve({"ref": parts}, data)
                except ProviderError:
                    pass  # Missing fields stay missing; they are never replaced with expected/default values.
            return {"json": projected, "raw": data}
        return {"json": data}


class ScenarioAdapter:
    version = "0.1.0"
    supported_trials = frozenset({"scenario.v1"})

    def __init__(self, backend):
        if type(backend) not in {FilesystemAdapter, WebDAVAdapter, JSONReadConnector}:
            raise ValueError("unregistered scenario connector")
        self.backend = backend
        self.name = "scenario:" + backend.name
        self.evidence_level = backend.evidence_level
        self.operations = OBJECT_OPERATIONS if type(backend) in {FilesystemAdapter, WebDAVAdapter} else {"http.get"}
        self.profile = {**backend.profile, "backend_adapter_version": backend.version,
                        "scenario_operations": sorted(self.operations), "scenario_policy_version": "1",
                        "observation_budget_bytes": 65536, "request_budget": 128, "check_value_limit_bytes": 1024}
        if hasattr(backend, "transport"):
            self.transport = backend.transport
            self.transport.max_calls = 128
        self.values = {}
        self.captures = {"setup": {}, "steps": {}}
        self.events, self.error, self.observation_bytes = [], None, 0
        self.before = None

    def describe_operations(self):
        return [{"id": op, "read_only": op in READ_ONLY, "arguments": sorted(ARGUMENTS[op]),
                 **({"routes": [{"id": name, "parameters": sorted(parameters),
                                 "output_fields": sorted(self.backend.projections[name])}
                                for name, parameters in self.backend.parameters.items()]}
                    if op == "http.get" else {})} for op in sorted(self.operations)]

    def validate_scenario(self, trial):
        for step in [*trial.setup, *trial.steps, *trial.observe]:
            if step["op"] not in self.operations:
                raise ValueError("scenario operation is not supported by this candidate")
            if step["op"] == "http.get":
                route = step["args"]["route"]
                if not isinstance(route, str) or route not in self.backend.parameters \
                        or set(step["args"]["params"]) != self.backend.parameters[route]:
                    raise ValueError("unknown HTTP route or path parameters")

    def resource(self, run_id):
        return self.backend.resource(run_id)

    def provision(self, resource, trial):
        self.captures = {"setup": {}, "steps": {}}
        self.events, self.error, self.observation_bytes, self.before = [], None, 0, None
        if hasattr(self, "transport"):
            self.transport.calls = 0
        self.backend.provision(resource, trial)

    def cleanup(self, resource):
        self.backend.cleanup(resource)

    def _object(self, resource, operation, arguments):
        backend = self.backend
        if type(backend) is FilesystemAdapter:
            root = backend._path(resource)
            if not root.is_dir():
                raise ProviderError("observation_unavailable")
            target = root / arguments["key"] if "key" in arguments else None
            if target is not None and target.is_symlink():
                raise ProviderError("resource_scope_violation")
            if operation == "object.put":
                backend.apply(resource, StorageTrial("scenario.v1", arguments["key"], arguments["content"]))
                return {"accepted": True}
            if operation == "object.delete":
                target.unlink(missing_ok=True)
                return {"accepted": True}
            if operation == "object.list":
                names = []
                for item in root.iterdir():
                    if item.is_symlink() or not item.is_file() or len(names) >= 32:
                        raise ProviderError("invalid_observation")
                    names.append(item.name)
                return {"keys": sorted(names), "count": len(names)}
            try:
                with target.open("rb") as stream:
                    content = stream.read(4097)
            except FileNotFoundError:
                return {"key": arguments["key"], "exists": False}
        else:
            backend._owned(resource)
            if operation == "object.put":
                backend._request(resource, "PUT", key=arguments["key"], body=arguments["content"].encode(),
                    headers={"Content-Type": "application/octet-stream"}, allowed=(200, 201, 204))
                return {"accepted": True}
            if operation == "object.delete":
                backend._request(resource, "DELETE", key=arguments["key"], allowed=(200, 204, 404))
                return {"accepted": True}
            names = backend._listing(resource)
            if len(names) > 32:
                raise ProviderError("observation_budget_exceeded")
            if operation == "object.list":
                return {"keys": sorted(names), "count": len(names)}
            response = backend._request(resource, "GET", key=arguments["key"], allowed=(200, 404))
            if response.status == 404:
                if arguments["key"] in names:
                    raise ProviderError("observation_unavailable")
                return {"key": arguments["key"], "exists": False}
            if arguments["key"] not in names:
                raise ProviderError("invalid_observation")
            content = response.body
        if len(content) > 4096:
            raise ProviderError("invalid_observation")
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            raise ProviderError("invalid_observation") from None
        return {"key": arguments["key"], "exists": True, "content": text,
                "content_hash": content_hash(content), "size_bytes": len(content)}

    def _perform(self, resource, operation, arguments):
        if operation not in self.operations:
            raise ProviderError("scenario_operation_not_allowed")
        try:
            validate_arguments(operation, arguments)
        except (ValueError, TypeError):
            raise ProviderError("scenario_arguments_invalid") from None
        try:
            result = self.backend.get(resource, arguments) if operation == "http.get" \
                else self._object(resource, operation, arguments)
        except OSError:
            raise ProviderError("observation_unavailable" if operation in READ_ONLY else "write_outcome_unknown") from None
        self.observation_bytes += len(canonical(result).encode())
        if self.observation_bytes > 65536:
            raise ProviderError("observation_budget_exceeded")
        return result

    def _execute(self, resource, definitions, phase, context):
        results = {}
        for step in definitions:
            event = {"phase": phase, "id": step["id"], "operation": step["op"],
                     "read_only": step["op"] in READ_ONLY,
                     "source": self.name + ":" + step["op"], "started_at": datetime.now(UTC).isoformat()}
            try:
                arguments = {name: ({key: resolve(value, context) for key, value in expression.items()}
                                    if name == "params" else resolve(expression, context))
                             for name, expression in step["args"].items()}
                event["arguments_hash"] = fingerprint(arguments)
                result = self._perform(resource, step["op"], arguments)
                results[step["id"]] = result
                event["result_hash"] = fingerprint(result)
                if phase in self.captures:
                    self.captures[phase][step["id"]] = result
            except ProviderError as error:
                self.error = error.reason
                event["error"] = error.reason
            event["observed_at"] = datetime.now(UTC).isoformat()
            self.events.append(event)
            if self.error:
                break
        return results

    def seed(self, resource, trial):
        self._execute(resource, trial.setup, "setup", {"fixture": trial.fixture, **self.captures})

    def apply(self, resource, trial):
        if not self.error:
            self._execute(resource, trial.steps, "steps",
                          {"fixture": trial.fixture, "before": self.before.values, **self.captures})

    def read(self, resource, trial):
        phase = "observe_before" if self.before is None else "observe_after"
        values = self._execute(resource, trial.observe, phase,
                               {"fixture": trial.fixture, "setup": self.captures["setup"]}) if not self.error else {}
        snapshot = ScenarioSnapshot(copy.deepcopy(values), copy.deepcopy(self.captures), copy.deepcopy(self.events),
                                    self.name + ":scenario-observations", datetime.now(UTC).isoformat(), self.error)
        if self.before is None:
            self.before = snapshot
        return snapshot
