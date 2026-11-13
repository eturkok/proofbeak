"""Shared lifecycle, evidence and bounded polling for every capability connector."""

import copy
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from proofbeak.adapters.base import ProviderError
from proofbeak.models import canonical, fingerprint
from proofbeak.packs.scenario import ScenarioSnapshot
from proofbeak.packs.scenario_v2 import evaluate, resolve_tree
from proofbeak.validation import located


@dataclass
class CapabilitySnapshot(ScenarioSnapshot):
    polls: list = field(default_factory=list)


class CapabilityAdapter:
    version = "0.1.0"
    supported_trials = frozenset({"scenario.v2"})

    def __init__(self, connector):
        self.backend = connector
        self.name, self.evidence_level = connector.name, connector.evidence_level
        self.profile = {**connector.profile, "capability_contract": "1", "operation_budget": 128,
                        "observation_budget_bytes": 262144}
        self.captures, self.events, self.error, self.before = {"setup": {}, "steps": {}}, [], None, None
        self.calls, self.bytes = 0, 0
        if hasattr(connector, "transport"):
            self.transport = connector.transport

    def describe_operations(self):
        return self.backend.catalog.describe()

    def validate_scenario(self, trial):
        for phase in ("setup", "steps", "observe"):
            for index, step in enumerate(getattr(trial, phase)):
                with located([phase, index]):
                    definition = self.backend.catalog.get(step["op"])
                    if phase == "observe" and not definition["read_only"]:
                        raise ValueError("observations require a read-only capability")
                    # Resolve fixture references now. Captured values are checked immediately before I/O.
                    def dynamic(value):
                        if isinstance(value, dict):
                            return (set(value) == {"ref"} and value["ref"][0] != "fixture") or any(dynamic(v) for v in value.values())
                        return isinstance(value, list) and any(dynamic(v) for v in value)
                    if not dynamic(step["args"]):
                        args = resolve_tree(step["args"], {"fixture": trial.fixture})
                        self.backend.catalog.validate(step["op"], args, observation=phase == "observe")

    def resource(self, run_id):
        return self.backend.resource(run_id)

    def provision(self, resource, trial):
        self.captures, self.events, self.error, self.before = {"setup": {}, "steps": {}}, [], None, None
        self.calls, self.bytes = 0, 0
        self.backend.provision(resource, trial)

    def cleanup(self, resource):
        self.backend.cleanup(resource)

    def _execute(self, resource, definitions, phase, context):
        values = {}
        for step in definitions:
            started = time.monotonic()
            event = {"id": step["id"], "phase": phase, "operation": step["op"],
                     "started_at": datetime.now(UTC).isoformat(), "source": self.name}
            try:
                self.calls += 1
                if self.calls > 128:
                    raise ProviderError("request_budget_exceeded")
                arguments = resolve_tree(step["args"], context)
                try:
                    definition = self.backend.catalog.validate(step["op"], arguments, observation=phase.startswith("observe"))
                except ValueError:
                    raise ProviderError("scenario_arguments_invalid") from None
                event.update(read_only=definition["read_only"], arguments_hash=fingerprint(arguments))
                result = self.backend.invoke(resource, step["op"], arguments)
                from proofbeak.capabilities import matches
                if not isinstance(result, dict) or "_proofbeak" in result or not matches(definition["output_schema"], result):
                    raise ProviderError("invalid_observation")
                result = {**result, "_proofbeak": {"elapsed_ms": (time.monotonic() - started) * 1000}}
                self.bytes += len(canonical(result).encode())
                if self.bytes > 262144:
                    raise ProviderError("observation_budget_exceeded")
                event["result_hash"] = fingerprint(result)
                values[step["id"]] = result
                if phase in self.captures:
                    self.captures[phase][step["id"]] = result
            except (ProviderError, OSError, ValueError) as error:
                self.error = error.reason if isinstance(error, ProviderError) else "connector_operation_failed"
                event["error"] = self.error
            event["observed_at"] = datetime.now(UTC).isoformat()
            self.events.append(event)
            if self.error:
                break
        return values

    def seed(self, resource, trial):
        self._execute(resource, trial.setup, "setup", {"fixture": trial.fixture, **self.captures})

    def apply(self, resource, trial):
        if not self.error:
            self._execute(resource, trial.steps, "steps", {"fixture": trial.fixture, "before": self.before.values, **self.captures})

    def read(self, resource, trial):
        initial = self.before is None
        phase = "observe_before" if initial else "observe_after"
        watch = None if initial or self.error else trial.eventually
        polls, consecutive, values = [], 0, {}
        deadline = time.monotonic() + (watch["timeout_seconds"] if watch else 0)
        if not self.error:
            while True:
                self.error = None
                values = self._execute(resource, trial.observe, phase, {"fixture": trial.fixture, "setup": self.captures["setup"]})
                now = datetime.now(UTC).isoformat()
                result = evaluate(trial.expect, {"fixture": trial.fixture, "before": self.before.values if self.before else {},
                                  "after": values}, self.name, now) if watch and not self.error else None
                passed = result is not None and result["verdict"] == "pass"
                consecutive = consecutive + 1 if passed else 0
                polls.append({"observed_at": now, "observations_hash": fingerprint(values),
                              "verdict": result["verdict"] if result else "inconclusive", "error": self.error})
                if not watch or consecutive >= watch.get("stable_samples", 1):
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0 or self.calls >= 128 or self.bytes >= 262144:
                    if passed:
                        self.error = "stability_not_established"
                    break
                time.sleep(min(remaining, watch["interval_seconds"]))
        snapshot = CapabilitySnapshot(copy.deepcopy(values), copy.deepcopy(self.captures), copy.deepcopy(self.events),
            self.name + ":independent-observations", datetime.now(UTC).isoformat(), self.error, polls=polls)
        if initial:
            self.before = snapshot
        return snapshot
