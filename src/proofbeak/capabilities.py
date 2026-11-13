"""Trusted, discoverable operation contracts; scenarios never select executable code."""

import copy
import re

from proofbeak.models import canonical

OPERATION = r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*){1,3}"


def validator(schema):
    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        raise ValueError("scenario.v2 requires the connectors extra: pip install 'proofbeak[connectors]'") from None
    if not isinstance(schema, (dict, bool)) or len(canonical(schema).encode()) > 16384:
        raise ValueError("invalid bounded JSON schema")

    def inspect(value, depth=0):
        if depth > 20:
            raise ValueError("schema nesting limit exceeded")
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"$dynamicRef", "$recursiveRef"} or (key == "$ref" and
                        (not isinstance(item, str) or not item.startswith("#/"))):
                    raise ValueError("only document-local schema references are supported")
                inspect(item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                inspect(item, depth + 1)
    inspect(schema)
    try:
        Draft202012Validator.check_schema(schema)
    except Exception:
        raise ValueError("invalid JSON Schema 2020-12 definition") from None
    return Draft202012Validator(schema)


def matches(schema, value):
    try:
        return validator(schema).is_valid(value)
    except ValueError:
        raise
    except Exception:
        raise ValueError("unresolvable local JSON schema") from None


class OperationCatalog:
    """Registered by trusted connector code, shared by discovery and runtime validation."""

    def __init__(self, operations):
        if not isinstance(operations, dict) or not 1 <= len(operations) <= 64:
            raise ValueError("configure one to 64 operations")
        self.operations = copy.deepcopy(operations)
        for name, definition in self.operations.items():
            if not isinstance(name, str) or not re.fullmatch(OPERATION, name) or not isinstance(definition, dict):
                raise ValueError("invalid operation registration")
            if set(definition) - {"input_schema", "output_schema", "read_only", "binding", "description"} \
                    or not {"input_schema", "output_schema", "read_only", "binding"} <= set(definition) \
                    or type(definition["read_only"]) is not bool or not isinstance(definition["binding"], dict):
                raise ValueError("operation needs input/output schemas, read_only and binding")
            for field in ("input_schema", "output_schema"):
                validator(definition[field])
            schema = definition["input_schema"]
            if not isinstance(schema, dict) or schema.get("type") != "object" \
                    or schema.get("additionalProperties") is not False or "scope" in schema.get("properties", {}):
                raise ValueError("operation inputs must be closed objects without the reserved scope field")

    def describe(self):
        return [{"id": name, **{key: copy.deepcopy(value) for key, value in definition.items() if key != "binding"}}
                for name, definition in sorted(self.operations.items())]

    def get(self, name):
        if name not in self.operations:
            raise ValueError("operation is not configured for this candidate")
        return self.operations[name]

    def validate(self, name, arguments, *, observation=False):
        definition = self.get(name)
        if observation and not definition["read_only"]:
            raise ValueError("observations must use an operator-declared read-only operation")
        if not matches(definition["input_schema"], arguments):
            raise ValueError("arguments do not satisfy the operation input schema")
        return definition
