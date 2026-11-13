"""Machine-readable authoring contract, published through discovery."""

from proofbeak.packs.scenario import ARGUMENTS, IDENTIFIER, OPERATORS, READ_ONLY


def schema():
    atom = {"anyOf": [{"type": "string", "minLength": 1, "maxLength": 128},
                      {"type": "integer", "minimum": 0, "maximum": 63}]}
    reference = {"type": "object", "required": ["ref"], "additionalProperties": False,
                 "properties": {"ref": {"type": "array", "minItems": 2, "maxItems": 8, "items": atom}}}
    string = {"anyOf": [{"type": "string", "maxLength": 4096}, {"$ref": "#/$defs/reference"}]}

    def operation(op):
        properties = {}
        for name in sorted(ARGUMENTS[op]):
            if name == "route":
                properties[name] = {"type": "string", "pattern": "^" + IDENTIFIER + "$"}
            elif name == "params":
                properties[name] = {"type": "object", "maxProperties": 8,
                    "additionalProperties": {"anyOf": [{"type": ["string", "integer", "boolean"]},
                                                       {"$ref": "#/$defs/reference"}]}}
            else:
                properties[name] = string
        return {"type": "object", "required": ["id", "op", "args"], "additionalProperties": False,
            "properties": {"id": {"type": "string", "pattern": "^" + IDENTIFIER + "$"}, "op": {"const": op},
                "args": {"type": "object", "required": sorted(properties), "properties": properties,
                         "additionalProperties": False}}}

    def condition(root):
        return {"type": "object", "additionalProperties": False,
            "required": ["name", "actual", "op", "value"], "properties": {
                "name": {"type": "string", "pattern": "^" + IDENTIFIER + "$"},
                "actual": {"type": "array", "minItems": 3, "maxItems": 8,
                           "prefixItems": [{"const": root}], "items": atom},
                "op": {"enum": list(OPERATORS)}, "value": {"anyOf": [
                    {"$ref": "#/$defs/reference"}, {"$ref": "#/$defs/json_value"}]}}}

    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
        "additionalProperties": False,
        "required": ["id", "name", "fixture", "setup", "steps", "observe", "require", "expect"],
        "description": "No code, URLs or credentials. Semantic validation checks references, connector capabilities and byte budgets.",
        "properties": {"id": {"const": "scenario.v1"}, "name": {"type": "string", "minLength": 1, "maxLength": 120},
            "fixture": {"type": "object", "maxProperties": 64, "additionalProperties": {"$ref": "#/$defs/json_value"},
                        "description": "Synthetic input values; at most 16 KiB, nesting depth eight."},
            "setup": {"type": "array", "maxItems": 8, "items": {"oneOf": [operation(op) for op in ARGUMENTS]}},
            "steps": {"type": "array", "maxItems": 16, "items": {"oneOf": [operation(op) for op in ARGUMENTS]}},
            "observe": {"type": "array", "minItems": 1, "maxItems": 8,
                        "items": {"oneOf": [operation(op) for op in sorted(READ_ONLY)]}},
            "require": {"type": "array", "maxItems": 16, "items": condition("before")},
            "expect": {"type": "array", "minItems": 1, "maxItems": 16, "items": condition("after")}},
        "$defs": {"reference": reference, "json_value": {"anyOf": [
            {"type": ["null", "boolean", "number", "string"]},
            {"type": "array", "maxItems": 64, "items": {"$ref": "#/$defs/json_value"}},
            {"type": "object", "maxProperties": 64, "additionalProperties": {"$ref": "#/$defs/json_value"}}]}}}
