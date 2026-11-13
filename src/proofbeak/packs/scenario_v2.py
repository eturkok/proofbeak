"""Protocol-neutral scenarios over operator-registered capabilities."""

import json
from dataclasses import dataclass

from proofbeak.adapters.base import ProviderError
from proofbeak.capabilities import OPERATION, matches, validator
from proofbeak.models import canonical
from proofbeak.observer import equal
from proofbeak.packs.scenario import ScenarioPack, ScenarioTrial, conditions, is_reference, resolve
from proofbeak.validation import TrialValidationError

PREDICATES = ["eq", "ne", "contains", "length_eq", "gt", "gte", "lt", "lte", "schema", "set_eq", "recall_at_k"]


def resolve_tree(value, context):
    if is_reference(value):
        return resolve(value, context)
    if isinstance(value, dict):
        return {key: resolve_tree(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_tree(item, context) for item in value]
    return value


def evaluate(definitions, context, source, observed_at):
    checks, unavailable = [], False
    for definition in definitions:
        op = definition["op"]
        if op not in {"schema", "set_eq", "recall_at_k"}:
            try:
                prepared = {**definition, "value": resolve_tree(definition["value"], context)}
            except ProviderError:
                prepared = {**definition, "value": {"ref": ["unavailable_expected_value"]}}
            result = conditions([prepared], context, source, observed_at)
            checks.extend(result["checks"])
            unavailable |= result["verdict"] == "inconclusive"
            continue
        actual, expected, available, passed, metric = None, None, True, False, None
        try:
            actual = resolve({"ref": definition["actual"]}, context)
            expected = resolve_tree(definition["value"], context)
            if op == "schema":
                passed = matches(expected, actual)
            elif op == "set_eq":
                passed = isinstance(actual, list) and isinstance(expected, list) and \
                    all(any(equal(item, other) for other in expected) for item in actual) and \
                    all(any(equal(item, other) for other in actual) for item in expected)
            else:
                if not isinstance(actual, list):
                    raise ValueError("ranking must be an array")
                relevant = {canonical(item) for item in expected["relevant"]}
                observed = {canonical(item[expected["id_field"]]) for item in actual[:expected["k"]]}
                metric = len(relevant & observed) / len(relevant)
                passed = metric >= expected["min_recall"]
        except (ProviderError, ValueError, KeyError, TypeError):
            available, unavailable = False, True
        def display(value):
            from proofbeak.models import fingerprint
            size = len(canonical(value).encode())
            return {"value_hash": fingerprint(value), "bytes": size, "see_evidence": True} if size > 1024 else value
        checks.append({"field": definition["name"], "operator": op, "expected": display(expected),
                       "observed": display(actual), "available": available, "passed": passed,
                       "path": definition["actual"], "source": source, "observed_at": observed_at,
                       **({"measured_recall": metric} if metric is not None else {})})
    return {"verdict": "inconclusive" if unavailable else ("pass" if checks and all(c["passed"] for c in checks) else "fail"),
            "reason_code": "missing_observation" if unavailable else (
                "conditions_met" if checks and all(c["passed"] for c in checks) else "condition_violated"), "checks": checks}


@dataclass(frozen=True)
class CapabilityTrial(ScenarioTrial):
    eventually: dict | None = None


class CapabilityPack(ScenarioPack):
    id, version = "scenario.v2", "0.1.0"
    description = "Compose registered REST, MCP, SQL, stream, search and model operations with bounded eventual checks."

    def schema(self):
        ref = {"type": "object", "required": ["ref"], "additionalProperties": False,
               "properties": {"ref": {"type": "array", "minItems": 2, "maxItems": 12,
                   "items": {"type": ["string", "integer"]}}}}
        step = {"type": "object", "additionalProperties": False, "required": ["id", "op", "args"],
                "properties": {"id": {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9_-]{0,47}$"},
                    "op": {"type": "string", "pattern": "^" + OPERATION + "$"}, "args": {"type": "object"}}}
        def check(root):
            return {"type": "object", "additionalProperties": False, "required": ["name", "actual", "op", "value"],
                "properties": {"name": {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9_-]{0,47}$"},
                    "actual": {"type": "array", "minItems": 3, "maxItems": 12,
                        "prefixItems": [{"const": root}], "items": {"type": ["string", "integer"]}},
                    "op": {"enum": PREDICATES}, "value": {}}}
        return {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
            "additionalProperties": False, "required": ["id", "name", "fixture", "setup", "steps", "observe", "require", "expect"],
            "properties": {"id": {"const": self.id}, "name": {"type": "string", "minLength": 1, "maxLength": 120},
                "fixture": {"type": "object"},
                **{phase: {"type": "array", "maxItems": limit, "minItems": int(phase == "observe"), "items": step}
                   for phase, limit in (("setup", 8), ("steps", 16), ("observe", 8))},
                "require": {"type": "array", "maxItems": 16, "items": check("before")},
                "expect": {"type": "array", "minItems": 1, "maxItems": 16, "items": check("after")},
                "eventually": {"type": "object", "additionalProperties": False,
                    "required": ["timeout_seconds", "interval_seconds"], "properties": {
                        "timeout_seconds": {"type": "number", "minimum": 0.05, "maximum": 30},
                        "interval_seconds": {"type": "number", "minimum": 0.01, "maximum": 5},
                        "stable_samples": {"type": "integer", "minimum": 1, "maximum": 5}}}}, "$defs": {"reference": ref}}

    def example(self):
        return {"id": self.id, "name": "A committed row is independently observable", "fixture": {"key": "probe", "value": "hello"},
                "setup": [], "steps": [{"id": "write", "op": "record.put", "args": {
                    "key": {"ref": ["fixture", "key"]}, "value": {"ref": ["fixture", "value"]}}}],
                "observe": [{"id": "row", "op": "record.get", "args": {"key": {"ref": ["fixture", "key"]}}}],
                "require": [{"name": "absent", "actual": ["before", "row", "exists"], "op": "eq", "value": False}],
                "expect": [{"name": "committed", "actual": ["after", "row", "value"], "op": "eq",
                            "value": {"ref": ["fixture", "value"]}}]}

    def parse(self, spec):
        if len(canonical(spec).encode()) > 65536:
            raise ValueError("scenario exceeds 64 KiB")
        spec = json.loads(canonical(spec))
        errors = list(validator(self.schema()).iter_errors(spec))
        if errors:
            raise TrialValidationError("invalid scenario.v2 definition", code="invalid_scenario", path=list(errors[0].absolute_path))
        known = {"fixture": set(spec["fixture"]), "setup": set(), "steps": set(), "before": set()}
        def references(value, roots, depth=0):
            if depth > 16:
                raise ValueError("scenario nesting limit exceeded")
            if is_reference(value):
                parts = value["ref"]
                if not isinstance(parts, list) or not 2 <= len(parts) <= 12 or any(
                        not ((isinstance(p, str) and 1 <= len(p) <= 128) or (type(p) is int and 0 <= p <= 255)) for p in parts) \
                        or parts[0] not in roots or parts[1] not in known.get(parts[0], set()):
                    raise ValueError("unknown or forward reference")
                if parts[0] == "fixture":
                    try:
                        resolve(value, {"fixture": spec["fixture"]})
                    except ProviderError:
                        raise ValueError("fixture reference does not exist") from None
            elif isinstance(value, dict):
                for item in value.values():
                    references(item, roots, depth + 1)
            elif isinstance(value, list):
                for item in value:
                    references(item, roots, depth + 1)
        for phase in ("setup", "observe", "steps"):
            seen = set()
            for step in spec[phase]:
                if step["id"] in seen or "scope" in step["args"]:
                    raise ValueError("duplicate step id or reserved scope argument")
                seen.add(step["id"])
                references(step["args"], {"fixture", "setup"} | ({"before", "steps"} if phase == "steps" else set()))
                known["before" if phase == "observe" else phase].add(step["id"])
        known["after"] = known["before"]
        for phase, root in (("require", "before"), ("expect", "after")):
            seen = set()
            for condition in spec[phase]:
                if condition["name"] in seen or condition["actual"][1] not in known[root]:
                    raise ValueError("duplicate condition or unknown observation")
                seen.add(condition["name"])
                if any(not ((isinstance(p, str) and 1 <= len(p) <= 128) or (type(p) is int and 0 <= p <= 255))
                       for p in condition["actual"]):
                    raise ValueError("invalid observation path")
                references(condition["value"], {"fixture", "before"} | ({"after"} if phase == "expect" else set()))
                if condition["op"] == "schema":
                    try:
                        validator(resolve_tree(condition["value"], {"fixture": spec["fixture"]}))
                    except ProviderError:
                        raise ValueError("schema predicates must be literal or fixture-defined") from None
                if condition["op"] == "recall_at_k":
                    try:
                        v = resolve_tree(condition["value"], {"fixture": spec["fixture"]})
                    except ProviderError:
                        raise ValueError("ranking references must be fixture-defined") from None
                    if not isinstance(v, dict) or set(v) != {"relevant", "id_field", "k", "min_recall"} or \
                            not isinstance(v["relevant"], list) or not v["relevant"] or not isinstance(v["id_field"], str) or \
                            type(v["k"]) is not int or not 1 <= v["k"] <= 64 or type(v["min_recall"]) not in {int, float} or \
                            not 0 <= v["min_recall"] <= 1:
                        raise ValueError("invalid recall_at_k condition")
        return CapabilityTrial(**spec)

    def valid_baseline(self, trial, before):
        if before.error:
            raise ProviderError(before.error)
        result = evaluate(trial.require, {"fixture": trial.fixture, "before": before.values}, before.source, before.observed_at)
        before.baseline_checks = result["checks"]
        return not trial.require or result["verdict"] == "pass"

    def assess(self, trial, before, after):
        if after.error:
            return {"verdict": "inconclusive", "reason_code": after.error, "checks": []}
        return evaluate(trial.expect, {"fixture": trial.fixture, "before": before.values, "after": after.values},
                        after.source, after.observed_at)
