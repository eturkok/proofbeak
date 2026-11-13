"""Schema-guided drafting and mutation-free execution previews, with caller-owned expectations."""

import copy

from proofbeak.models import canonical
from proofbeak.packs import builtins
from proofbeak.suites import example_suite, expand_suite


def operation_catalog(discovery):
    result = copy.deepcopy(discovery)
    for candidate, capability in result["provider_capabilities"].items():
        for operation in capability.get("operations", []):
            if "input_schema" not in operation:
                fields = operation.get("arguments", [])
                properties = {name: {"type": "object" if name == "params" else "string"} for name in fields}
                if operation.get("routes"):
                    properties["route"] = {"type": "string", "enum": [r["id"] for r in operation["routes"]]}
                operation["input_schema"] = {"type": "object", "properties": properties,
                                             "required": fields, "additionalProperties": False}
            if "output_schema" not in operation:
                fields = {"object.get": {"exists": "boolean", "content": "string"},
                          "object.list": {"count": "integer", "keys": "array"}}.get(operation["id"], {})
                operation["output_schema"] = {"type": "object", "properties": {k: {"type": v} for k, v in fields.items()}}
    result["suite"] = {"id": "suite.v1", "example": example_suite(), "max_executions": 64,
                       "max_candidates_per_case": 4, "max_matrix_dimensions": 4}
    result["authoring"] = {"business_expectations": "caller_supplied", "provider_work_performed": False}
    return result


def draft_scenario(discovery, candidates, name, observer, arguments, path, expected, predicate="eq", actions=None):
    if not isinstance(candidates, list) or not candidates or len(candidates) > 4:
        raise ValueError("Choose one to four configured candidates.")
    catalog = operation_catalog(discovery)
    capabilities = catalog["provider_capabilities"]
    if any(c not in capabilities or not capabilities[c]["configured"] for c in candidates):
        raise ValueError("Choose configured candidates.")
    common = set.intersection(*(set(capabilities[c]["supported_trials"]) for c in candidates))
    format = "scenario.v2" if "scenario.v2" in common else "scenario.v1" if "scenario.v1" in common else None
    if not format:
        raise ValueError("These candidates do not share a user-defined scenario format.")
    if not isinstance(path, list) or not 1 <= len(path) <= 10 or any(type(p) not in {str, int} for p in path):
        raise ValueError("Choose an output field path, such as [\"value\"].")
    for candidate in candidates:
        operation = next((o for o in capabilities[candidate].get("operations", []) if o["id"] == observer), None)
        if operation is None or operation["read_only"] is not True:
            raise ValueError("Choose an independently read-only observer supported by every candidate.")
    actions = copy.deepcopy(actions or [])
    if not isinstance(actions, list) or len(actions) > 16:
        raise ValueError("A draft supports up to sixteen explicit actions.")
    spec = {"id": format, "name": name, "fixture": {}, "setup": [],
            "steps": [{"id": "action" + str(i + 1), "op": a["op"], "args": a["args"]} for i, a in enumerate(actions)],
            "observe": [{"id": "result", "op": observer, "args": copy.deepcopy(arguments)}], "require": [],
            "expect": [{"name": "requirement", "actual": ["after", "result", *path], "op": predicate, "value": copy.deepcopy(expected)}]}
    builtins().prepare(spec)
    return spec


def preview_suite(service, tenant, definition):
    result = service.validate_suite(tenant, definition)
    if not result["valid"]:
        return {"validation": result, "plan": [], "execution_performed": False}
    catalog = operation_catalog(service.packs(tenant))["provider_capabilities"]
    plan = []
    for entry in expand_suite(definition):
        test = entry["test"]
        operations = []
        for phase in ("setup", "steps", "observe"):
            for step in test.get(phase, []):
                policies = []
                for candidate in entry["candidates"]:
                    operation = next((o for o in catalog[candidate].get("operations", []) if o["id"] == step["op"]), None)
                    policies.append(operation.get("read_only") if operation else None)
                operations.append({"phase": phase, "id": step["id"], "operation": step["op"],
                                   "read_only": all(p is True for p in policies) if all(p is not None for p in policies) else None})
        plan.append({"id": entry["id"], "case_id": entry["case_id"], "parameters": entry["parameters"],
                     "candidates": entry["candidates"], "test_hash": entry["test_hash"],
                     "operations": operations, "conditions": len(test.get("expect", [])),
                     "final_polling": test.get("eventually"), "cleanup": "owned_resources_only"})
    return {"validation": result, "plan": plan, "execution_performed": False,
            "note": "Logical operations shown; provider lifecycle requests and final polling add bounded I/O."}


def validate_draft_size(value):
    if len(canonical(value).encode()) > 65536:
        raise ValueError("The draft exceeds 64 KiB.")
