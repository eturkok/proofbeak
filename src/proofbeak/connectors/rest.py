"""Named HTTP operations, projection and OpenAPI import; no request-supplied URL."""

import json
import re
from urllib.parse import quote, urlencode, urlsplit

from proofbeak.adapters.base import ProviderError
from proofbeak.adapters.http import Transport
from proofbeak.connectors import ScopedConnector
from proofbeak.models import canonical
from proofbeak.packs.scenario import resolve


def template(value, arguments, scope):
    if isinstance(value, dict):
        if set(value) == {"arg"}:
            if value["arg"] not in arguments:
                raise ProviderError("scenario_arguments_invalid")
            return arguments[value["arg"]]
        if value == {"scope": True}:
            return scope
        return {key: template(item, arguments, scope) for key, item in value.items()}
    if isinstance(value, list):
        return [template(item, arguments, scope) for item in value]
    return value


class RESTConnector(ScopedConnector):
    def __init__(self, name, settings, secrets=None):
        super().__init__(name, settings, secrets)
        if set(settings) - {"kind", "connection", "operations", "lifecycle", "provider_version"}:
            raise ValueError("unknown REST profile field")
        connection = settings["connection"]
        if set(connection) - {"endpoint", "secret_env"}:
            raise ValueError("unknown HTTP connection field")
        url = urlsplit(connection["endpoint"])
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment \
                or url.path not in {"", "/"} or (url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "::1"}):
            raise ValueError("configure an HTTPS origin or loopback HTTP origin")
        self.origin = connection["endpoint"].rstrip("/")
        self.transport = Transport(timeout=5, max_calls=256, max_bytes=65536)
        for definition in self.catalog.operations.values():
            binding = definition["binding"]
            if set(binding) - {"method", "path", "body", "query", "project", "statuses"}:
                raise ValueError("unknown HTTP operation binding")
            method, path = binding.get("method"), binding.get("path", "")
            if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"} or not isinstance(path, str) or \
                    not path.startswith("/") or path.startswith("//") or len(path) > 512 or \
                    any(part in {".", ".."} for part in path.split("/")) or not re.fullmatch(r"/[A-Za-z0-9_./{}~-]*", path):
                raise ValueError("invalid HTTP method or fixed path template")
            placeholders = re.findall(r"\{([^{}]+)\}", path)
            allowed = set(definition["input_schema"].get("properties", {})) | {"scope"}
            if any(p not in allowed for p in placeholders) or path.count("{") != len(placeholders):
                raise ValueError("HTTP path uses an unknown parameter")
            if not definition["read_only"] and "{scope}" not in path:
                raise ValueError("HTTP mutation paths must include the owned scope")
            if method in {"GET", "HEAD"} and not definition["read_only"]:
                raise ValueError("GET/HEAD cannot be declared as mutation operations")
            statuses = binding.get("statuses", [200, 201, 204])
            if not isinstance(statuses, list) or not statuses or len(statuses) > 16 or any(type(s) is not int or not 200 <= s <= 499 for s in statuses):
                raise ValueError("invalid accepted HTTP statuses")
        self.configure_lifecycle()

    def _invoke(self, resource, operation, arguments):
        scope = self.scope(resource)
        definition = self.catalog.validate(operation, arguments)
        binding = definition["binding"]
        path = binding["path"]
        for key in re.findall(r"\{([^{}]+)\}", path):
            value = scope if key == "scope" else arguments.get(key)
            if type(value) not in {str, int, bool} or str(value) in {".", ".."} or len(str(value)) > 256:
                raise ProviderError("scenario_arguments_invalid")
            path = path.replace("{" + key + "}", quote(str(value), safe=""))
        query = template(binding.get("query", {}), arguments, scope)
        if query:
            path += "?" + urlencode(query, doseq=True)
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        for key, value in self.secrets.items():
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,63}", key) or "\r" in value or "\n" in value:
                raise ProviderError("invalid_connector_credential")
            headers[key] = value
        body = canonical(template(binding["body"], arguments, scope)).encode() if "body" in binding else None
        response = self.transport.request(binding["method"], self.origin + path, body=body, headers=headers,
                                          allowed=tuple(binding.get("statuses", [200, 201, 204])))
        try:
            data = json.loads(response.body) if response.body else None
            canonical(data)
        except (ValueError, UnicodeDecodeError):
            raise ProviderError("invalid_observation") from None
        result = {"status": response.status, "exists": response.status not in {404, 410}, "json": data}
        projection = binding.get("project")
        if projection:
            projected = {}
            for key, parts in projection.items():
                try:
                    projected[key] = resolve({"ref": parts}, result)
                except ProviderError:
                    pass
            return projected
        return result


def openapi_operations(document, selected):
    """Import an explicitly selected, bounded OpenAPI JSON subset without network resolution."""
    if not isinstance(document, dict) or not str(document.get("openapi", "")).startswith("3."):
        raise ValueError("an OpenAPI 3 JSON document is required")
    def inline(value, seen=()):
        if isinstance(value, dict):
            if "$ref" in value:
                ref = value["$ref"]
                if not isinstance(ref, str) or not ref.startswith("#/") or ref in seen or len(seen) >= 12:
                    raise ValueError("only nonrecursive document-local OpenAPI references can be imported")
                target = document
                for part in ref[2:].split("/"):
                    target = target[part.replace("~1", "/").replace("~0", "~")]
                return inline(target, (*seen, ref))
            return {key: inline(item, seen) for key, item in value.items()}
        return [inline(item, seen) for item in value] if isinstance(value, list) else value
    selected = set(selected)
    if not selected or len(selected) > 32:
        raise ValueError("select one to 32 OpenAPI operation IDs")
    result = {}
    for path, item in document.get("paths", {}).items():
        for method, operation in item.items():
            if not isinstance(operation, dict) or operation.get("operationId") not in selected:
                continue
            identifier = operation["operationId"]
            name = "api." + re.sub(r"[^a-z0-9_]", "_", identifier.lower())
            if name in result:
                raise ValueError("duplicate normalized OpenAPI operation ID")
            properties, required, query = {}, [], {}
            for parameter in item.get("parameters", []) + operation.get("parameters", []):
                parameter = inline(parameter)
                if parameter["in"] not in {"path", "query"}:
                    raise ValueError("selected header/cookie parameters require a manual trusted binding")
                key = parameter["name"]
                properties[key] = inline(parameter.get("schema", {}))
                if parameter.get("required"):
                    required.append(key)
                if parameter["in"] == "query":
                    query[key] = {"arg": key}
            binding = {"method": method.upper(), "path": path, **({"query": query} if query else {})}
            if "requestBody" in operation:
                body = inline(operation["requestBody"])
                properties["body"] = inline(body["content"]["application/json"]["schema"])
                required.append("body")
                binding["body"] = {"arg": "body"}
            result[name] = {"input_schema": {"type": "object", "properties": properties, "required": required, "additionalProperties": False},
                            "output_schema": {"type": "object"}, "read_only": method.lower() in {"get", "head"}, "binding": binding}
    if len(result) != len(selected):
        raise ValueError("some selected operation IDs are missing or ambiguous")
    return result
