"""User-defined bounded workflows over trusted connector capabilities; no case-specific code."""

import json
import math
import re
from dataclasses import asdict, dataclass, field

from proofbeak.adapters.base import ProviderError
from proofbeak.models import canonical, fingerprint, keys
from proofbeak.observer import equal
from proofbeak.validation import located

IDENTIFIER = r"[A-Za-z][A-Za-z0-9_-]{0,47}"
ARGUMENTS = {"object.put": {"key", "content"}, "object.get": {"key"},
             "object.list": set(), "object.delete": {"key"}, "http.get": {"route", "params"}}
READ_ONLY = frozenset({"object.get", "object.list", "http.get"})
OPERATORS = ("eq", "ne", "contains", "length_eq", "gt", "gte", "lt", "lte")


def json_value(value, depth=0):
    if depth > 8:
        raise ValueError("scenario values exceed nesting limit")
    if value is None or type(value) is bool:
        return
    if type(value) is int and abs(value) <= 2 ** 63 - 1:
        return
    if type(value) is float and math.isfinite(value):
        return
    if isinstance(value, str) and len(value.encode()) <= 4096:
        return
    if isinstance(value, (list, dict)) and len(value) <= 64:
        if isinstance(value, dict):
            if any(not isinstance(k, str) or not 1 <= len(k.encode()) <= 128 for k in value):
                raise ValueError("invalid scenario object keys")
        for key, item in (value.items() if isinstance(value, dict) else enumerate(value)):
            with located([key], "invalid_json_value"):
                json_value(item, depth + 1)
        return
    raise ValueError("scenario values must be bounded finite JSON")


def path(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 8 or any(
        not ((type(part) is int and 0 <= part <= 63) or
             (isinstance(part, str) and 1 <= len(part.encode()) <= 128 and
              all(ord(char) >= 32 for char in part))) for part in value
    ):
        raise ValueError("reference needs a bounded list of object keys or array indices")
    return value


def is_reference(value):
    return isinstance(value, dict) and set(value) == {"ref"}


def resolve(value, context):
    if not is_reference(value):
        return value
    current = context
    try:
        for part in value["ref"]:
            if isinstance(current, dict) and isinstance(part, str):
                current = current[part]
            elif isinstance(current, list) and type(part) is int:
                current = current[part]
            else:
                raise KeyError("wrong reference type")
        return current
    except (KeyError, IndexError, TypeError):
        raise ProviderError("scenario_value_unavailable") from None


def validate_arguments(operation, arguments):
    keys(arguments, ARGUMENTS[operation], "operation arguments")
    if operation.startswith("object."):
        with located(["key"], "invalid_object_key"):
            if "key" in arguments and (not isinstance(arguments["key"], str) or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", arguments["key"])):
                raise ValueError("object key must be a simple filename")
        with located(["content"], "invalid_object_content"):
            if "content" in arguments and (not isinstance(arguments["content"], str) or
                    not 1 <= len(arguments["content"].encode()) <= 4096):
                raise ValueError("object content must contain 1 to 4096 UTF-8 bytes")
    else:
        with located(["route"], "invalid_http_route"):
            if not isinstance(arguments["route"], str) or not re.fullmatch(IDENTIFIER, arguments["route"]):
                raise ValueError("invalid HTTP route")
        with located(["params"], "invalid_http_parameters"):
            if not isinstance(arguments["params"], dict) or len(arguments["params"]) > 8:
                raise ValueError("invalid HTTP parameters")
            for name, value in arguments["params"].items():
                if not isinstance(name, str) or not re.fullmatch(IDENTIFIER, name):
                    raise ValueError("HTTP parameter names must be simple identifiers")
                with located([name], "invalid_http_parameter"):
                    if type(value) not in {str, int, bool} \
                            or not 1 <= len(str(value).encode()) <= 128 or str(value) in {".", ".."} \
                            or any(char in "/\\%" or ord(char) < 32 or ord(char) == 127 for char in str(value)):
                        raise ValueError("invalid HTTP path parameter")


@dataclass(frozen=True)
class ScenarioTrial:
    id: str
    name: str
    fixture: dict
    setup: list
    steps: list
    observe: list
    require: list
    expect: list


@dataclass
class ScenarioSnapshot:
    values: dict
    captures: dict
    events: list
    source: str
    observed_at: str
    error: str | None = None
    baseline_checks: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


def conditions(definitions, context, source, observed_at):
    checks, missing = [], False
    for definition in definitions:
        actual, expected, available = None, None, True
        try:
            actual = resolve({"ref": definition["actual"]}, context)
            expected = resolve(definition["value"], context)
        except ProviderError:
            available, missing = False, True
        op = definition["op"]
        passed = False
        if available:
            numeric = lambda value: type(value) in {int, float} and (type(value) is int or math.isfinite(value))
            if op == "eq":
                passed = equal(expected, actual)
            elif op == "ne":
                passed = not equal(expected, actual)
            elif op == "contains":
                if isinstance(actual, str) and isinstance(expected, str):
                    passed = expected in actual
                elif isinstance(actual, list):
                    passed = any(equal(expected, item) for item in actual)
                elif isinstance(actual, dict) and isinstance(expected, str):
                    passed = expected in actual
            elif op == "length_eq":
                if type(expected) is not int or expected < 0:
                    available, missing = False, True
                else:
                    passed = isinstance(actual, (str, list, dict)) and len(actual) == expected
            elif not numeric(expected):
                available, missing = False, True
            elif numeric(actual):
                passed = {"gt": lambda: actual > expected, "gte": lambda: actual >= expected,
                          "lt": lambda: actual < expected, "lte": lambda: actual <= expected}[op]()
        def display(value):
            size = len(canonical(value).encode())
            return {"value_hash": fingerprint(value), "bytes": size, "see_evidence": True} if size > 1024 else value
        checks.append({"field": definition["name"], "operator": op, "expected": display(expected),
                       "observed": display(actual), "path": definition["actual"], "available": available, "passed": passed,
                       "source": source, "observed_at": observed_at})
    passed = bool(checks) and all(check["passed"] for check in checks)
    return {"verdict": "inconclusive" if missing else ("pass" if passed else "fail"),
            "reason_code": "missing_observation" if missing else (
                "conditions_met" if passed else "condition_violated"), "checks": checks}


class ScenarioPack:
    id, version = "scenario.v1", "0.1.0"
    description = "Define setup, operations, independent observations and your own acceptance conditions."

    def schema(self):
        from proofbeak.packs.scenario_schema import schema

        return schema()

    def example(self):
        return {"id": self.id, "name": "Deleting one object preserves another",
            "fixture": {"key": "probe.txt", "content": "synthetic probe", "other": "keep.txt"},
            "setup": [{"id": "seed", "op": "object.put", "args": {
                "key": {"ref": ["fixture", "key"]}, "content": {"ref": ["fixture", "content"]}}},
                {"id": "keep", "op": "object.put", "args": {"key": {"ref": ["fixture", "other"]},
                                                            "content": "preserve this"}}],
            "steps": [{"id": "remove", "op": "object.delete", "args": {"key": {"ref": ["fixture", "key"]}}}],
            "observe": [{"id": "target", "op": "object.get", "args": {"key": {"ref": ["fixture", "key"]}}},
                        {"id": "other", "op": "object.get", "args": {"key": {"ref": ["fixture", "other"]}}},
                        {"id": "listing", "op": "object.list", "args": {}}],
            "require": [{"name": "initial_count", "actual": ["before", "listing", "count"], "op": "eq", "value": 2}],
            "expect": [{"name": "target_absent", "actual": ["after", "target", "exists"], "op": "eq", "value": False},
                       {"name": "one_remaining", "actual": ["after", "listing", "count"], "op": "eq", "value": 1},
                       {"name": "other_preserved", "actual": ["after", "other", "content"], "op": "eq",
                        "value": {"ref": ["before", "other", "content"]}}]}

    def parse(self, spec):
        with located([], "invalid_fields"):
            keys(spec, {"id", "name", "fixture", "setup", "steps", "observe", "require", "expect"}, "scenario")
        with located(["id"], "invalid_trial_id"):
            if spec["id"] != self.id:
                raise ValueError("unsupported scenario format")
        with located(["name"], "invalid_name"):
            if not isinstance(spec["name"], str) or not 1 <= len(spec["name"]) <= 120:
                raise ValueError("scenario name must contain 1 to 120 characters")
        with located(["fixture"], "invalid_fixture"):
            if not isinstance(spec["fixture"], dict) or len(canonical(spec["fixture"]).encode()) > 16384:
                raise ValueError("fixture must be an object of at most 16 KiB")
            json_value(spec["fixture"])
        with located([], "invalid_json_value"):
            copied = json.loads(canonical(spec))
        known = {"fixture": set(copied["fixture"]), "setup": set(), "steps": set(), "before": set()}

        def expression(value, roots):
            if is_reference(value):
                with located(["ref"], "invalid_reference"):
                    reference = path(value["ref"])
                    if len(reference) < 2 or reference[0] not in roots or reference[1] not in known[reference[0]]:
                        raise ValueError("unknown or forward scenario reference")
                    if reference[0] == "fixture":
                        try:
                            resolve(value, {"fixture": copied["fixture"]})
                        except ProviderError:
                            raise ValueError("fixture reference does not exist") from None
            else:
                with located([], "invalid_json_value"):
                    json_value(value)

        for phase, limit in (("setup", 8), ("observe", 8), ("steps", 16)):
            with located([phase], "invalid_operation_list"):
                definitions = copied[phase]
                if not isinstance(definitions, list) or len(definitions) > limit or (phase == "observe" and not definitions):
                    raise ValueError("invalid bounded scenario operation list")
                seen = set()
                for index, step in enumerate(definitions):
                    with located([index], "invalid_operation"):
                        with located([], "invalid_fields"):
                            keys(step, {"id", "op", "args"}, "scenario operation")
                        with located(["id"], "invalid_operation_id"):
                            if not isinstance(step["id"], str) or not re.fullmatch(IDENTIFIER, step["id"]) or step["id"] in seen:
                                raise ValueError("operation ids must be unique identifiers within their phase")
                        seen.add(step["id"])
                        operation = step["op"]
                        with located(["op"], "unsupported_operation"):
                            if not isinstance(operation, str) or operation not in ARGUMENTS:
                                raise ValueError("unsupported operation")
                            if phase == "observe" and operation not in READ_ONLY:
                                raise ValueError("observations must use a read-only operation")
                        with located(["args"], "invalid_arguments"):
                            keys(step["args"], ARGUMENTS[operation], "operation arguments")
                            with located(["route"], "invalid_http_route"):
                                if operation == "http.get" and not isinstance(step["args"]["route"], str):
                                    raise ValueError("HTTP route must be a configured literal name")
                            roots = {"fixture", "setup"} if phase != "steps" else {"fixture", "setup", "before", "steps"}
                            for name, value in step["args"].items():
                                with located([name], "invalid_argument"):
                                    if name == "params":
                                        if not isinstance(value, dict) or len(value) > 8:
                                            raise ValueError("invalid HTTP path parameters")
                                        for parameter, part in value.items():
                                            if not isinstance(parameter, str) or not re.fullmatch(IDENTIFIER, parameter):
                                                raise ValueError("HTTP parameter names must be simple identifiers")
                                            with located([parameter], "invalid_http_parameter"):
                                                expression(part, roots)
                                    else:
                                        expression(value, roots)
                            # Runtime references are typed again before any connector call.
                            def preflight(value):
                                return "dynamic" if is_reference(value) and value["ref"][0] != "fixture" else resolve(
                                    value, {"fixture": copied["fixture"]})
                            resolved = {name: ({key: preflight(v) for key, v in value.items()} if name == "params"
                                               else preflight(value)) for name, value in step["args"].items()}
                            validate_arguments(operation, resolved)
                        known["before" if phase == "observe" else phase].add(step["id"])
        known["after"] = known["before"]
        for phase, actual_root in (("require", "before"), ("expect", "after")):
            with located([phase], "invalid_condition_list"):
                definitions = copied[phase]
                if not isinstance(definitions, list) or len(definitions) > 16 or (phase == "expect" and not definitions):
                    raise ValueError("invalid bounded scenario conditions")
                seen = set()
                for index, condition in enumerate(definitions):
                    with located([index], "invalid_condition"):
                        with located([], "invalid_fields"):
                            keys(condition, {"name", "actual", "op", "value"}, "condition")
                        with located(["name"], "invalid_condition_name"):
                            if not isinstance(condition["name"], str) or not re.fullmatch(IDENTIFIER, condition["name"]) \
                                    or condition["name"] in seen:
                                raise ValueError("condition names must be unique identifiers within their phase")
                        with located(["op"], "unsupported_predicate"):
                            if condition["op"] not in OPERATORS:
                                raise ValueError("unsupported condition operator")
                        seen.add(condition["name"])
                        with located(["actual"], "invalid_observation_reference"):
                            actual = path(condition["actual"])
                            if len(actual) < 3 or actual[0] != actual_root or actual[1] not in known[actual_root]:
                                raise ValueError("conditions must reference an independent observation")
                        with located(["value"], "invalid_expected_value"):
                            expression(condition["value"], {"fixture", "before"} | ({"after"} if phase == "expect" else set()))
        return ScenarioTrial(**copied)

    def validate_adapter(self, trial, adapter):
        if not hasattr(adapter, "validate_scenario"):
            raise ValueError("candidate has no scenario connector")
        adapter.validate_scenario(trial)

    def valid_baseline(self, trial, before):
        if not isinstance(before, ScenarioSnapshot):
            return False
        if before.error:
            raise ProviderError(before.error)
        if trial.require:
            result = conditions(trial.require, {"fixture": trial.fixture, "before": before.values},
                                before.source, before.observed_at)
            before.baseline_checks = result["checks"]
            return result["verdict"] == "pass"
        return True

    def assess(self, trial, before, after):
        if not isinstance(after, ScenarioSnapshot) or after.error:
            return {"verdict": "inconclusive", "reason_code": after.error if isinstance(after, ScenarioSnapshot)
                    else "invalid_observation", "checks": []}
        return conditions(trial.expect, {"fixture": trial.fixture, "before": before.values, "after": after.values},
                          after.source, after.observed_at)
