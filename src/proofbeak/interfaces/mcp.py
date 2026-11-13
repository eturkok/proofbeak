"""Stdio MCP bridge to the authenticated REST service; no model runtime is embedded."""

import json
import os
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit, urlencode
from urllib.request import ProxyHandler, Request, build_opener

from proofbeak.adapters.http import NoRedirect
from proofbeak.models import canonical
from proofbeak.service import ServiceError


class BridgeClient:
    def __init__(self, endpoint, token):
        url = urlsplit(endpoint)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password \
                or url.path not in {"", "/"} or url.query or url.fragment:
            raise ValueError("configure an origin URL without credentials for the Proofbeak API")
        if url.scheme == "http" and url.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("remote API endpoints require HTTPS")
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token):
            raise ValueError("inject the tenant API token issued by proofbeak-server")
        self.endpoint, self.token = endpoint.rstrip("/"), token
        self.opener = build_opener(NoRedirect(), ProxyHandler({}))

    def request(self, method, path, payload=None, key=None):
        body = canonical(payload).encode() if payload is not None else None
        if body is not None and len(body) > 65536:
            raise ServiceError("request_too_large", 413)
        headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}
        if key is not None:
            if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", key):
                raise ServiceError("invalid_idempotency_key", 422)
            headers["Idempotency-Key"] = key
        request = Request(self.endpoint + path, data=body, method=method, headers=headers)
        try:
            response = self.opener.open(request, timeout=15)
        except HTTPError as error:
            response = error
        except (URLError, OSError):
            raise ServiceError("api_unavailable", 503) from None
        with response:
            content = response.read(16_777_217)
            if len(content) > 16_777_216:
                raise ServiceError("response_too_large", 502)
            try:
                data = json.loads(content)
                if not isinstance(data, dict):
                    raise ValueError("expected object")
            except (ValueError, UnicodeDecodeError):
                raise ServiceError("invalid_api_response", 502) from None
            if response.code not in {200, 202}:
                code = data.get("detail")
                if not isinstance(code, str) or not re.fullmatch(r"[a-z_]{1,64}", code):
                    code = "api_request_failed"
                raise ServiceError(code, response.code)
        return data

    @staticmethod
    def run_path(run_id):
        if not isinstance(run_id, str) or not re.fullmatch(r"[a-f0-9]{32}", run_id):
            raise ServiceError("not_found", 404)
        return "/runs/" + run_id


def create_server(client):
    # Pin the maintained SDK major; wire behavior is covered by the stdio integration test.
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations

    server = FastMCP("Proofbeak")
    read_only = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False)
    write = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True)

    def call(*args, **kwargs):
        try:
            return client.request(*args, **kwargs)
        except ServiceError as exc:
            raise ValueError(exc.code) from None

    @server.tool(annotations=read_only, structured_output=True)
    def list_trial_packs() -> dict[str, Any]:
        """List registered trial packages and this tenant's allowed providers."""
        return call("GET", "/packs")

    @server.tool(annotations=write, structured_output=True)
    def run_trial(test: dict, candidates: list[str], idempotency_key: str) -> dict[str, Any]:
        """Queue a closed trial; reuse retry keys. New keys must use discovery's idempotency_key_prefix after restore."""
        return call("POST", "/runs", {"test": test, "candidates": candidates}, key=idempotency_key)

    @server.tool(annotations=read_only, structured_output=True)
    def validate_trial(test: dict, candidates: list[str]) -> dict[str, Any]:
        """Validate a user-defined trial and connector capabilities; create no job or provider resource."""
        return call("POST", "/trials/validate", {"test": test, "candidates": candidates})

    @server.tool(annotations=read_only, structured_output=True)
    def get_run(run_id: str) -> dict[str, Any]:
        """Read the tenant-owned run's progress and separate candidate verdicts."""
        return call("GET", client.run_path(run_id))

    @server.tool(annotations=read_only, structured_output=True)
    def compare_results(run_id: str) -> dict[str, Any]:
        """Read acceptance checks, provenance, and cleanup status after a run completes."""
        return call("GET", client.run_path(run_id) + "/comparison")

    @server.tool(annotations=read_only, structured_output=True)
    def get_evidence(evidence_id: str) -> dict[str, Any]:
        """Read authorized before/after observations and their integrity fingerprint."""
        if not re.fullmatch(r"[a-f0-9]{32}:[0-3]", evidence_id):
            raise ValueError("not_found")
        return call("GET", "/evidence/" + quote(evidence_id, safe=""))

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True,
                                           idempotentHint=True, openWorldHint=True), structured_output=True)
    def retry_cleanup(run_id: str) -> dict[str, Any]:
        """Retry cleanup of this completed/interrupted trial's owned disposable resources only."""
        return call("POST", client.run_path(run_id) + "/cleanup")

    @server.tool(annotations=write, structured_output=True)
    def cancel_run(run_id: str) -> dict[str, Any]:
        """Cancel this tenant's queued or executing run; never replay its writes."""
        return call("POST", client.run_path(run_id) + "/cancel")

    @server.tool(annotations=read_only, structured_output=True)
    def list_runs(limit: int = 25, before: str | None = None) -> dict[str, Any]:
        """Read this tenant's bounded run history; use the returned cursor for older rows."""
        query = {"limit": limit, **({"before": before} if before else {})}
        return call("GET", "/runs?" + urlencode(query))

    @server.tool(annotations=read_only, structured_output=True)
    def find_evidence(test: dict, candidates: list[str], fresh: bool = False) -> dict[str, Any]:
        """Find intact, unexpired, unrevoked real evidence in the exact current context; never create a run."""
        return call("POST", "/evidence/lookup", {"test": test, "candidates": candidates, "fresh": fresh})

    @server.tool(annotations=write, structured_output=True)
    def revoke_evidence(run_id: str) -> dict[str, Any]:
        """Prevent future reuse of this tenant's run while retaining its historical observations."""
        return call("POST", client.run_path(run_id) + "/revoke")

    @server.tool(annotations=read_only, structured_output=True)
    def list_audit_events(limit: int = 50, before: int | None = None) -> dict[str, Any]:
        """Read this tenant's metadata-only audit history; credentials and fixture payloads are excluded."""
        query = {"limit": limit, **({"before": before} if before else {})}
        return call("GET", "/audit?" + urlencode(query))

    @server.tool(annotations=read_only, structured_output=True)
    def validate_suite(suite: dict) -> dict[str, Any]:
        """Validate all expanded suite cases before any provider work; report bounded matrix size."""
        return call("POST", "/suites/validate", {"suite": suite})

    @server.tool(annotations=write, structured_output=True)
    def run_suite(suite: dict, idempotency_key: str) -> dict[str, Any]:
        """Queue a tenant-owned suite of scenarios and bounded value matrices; reuse keys for retries."""
        return call("POST", "/suites", {"suite": suite}, key=idempotency_key)

    @server.tool(annotations=read_only, structured_output=True)
    def get_suite(suite_id: str) -> dict[str, Any]:
        """Read suite progress, per-condition evidence references and the conservative acceptance gate."""
        path = client.run_path(suite_id).replace("/runs/", "/suites/", 1)
        return call("GET", path)

    @server.tool(annotations=read_only, structured_output=True)
    def list_suites(limit: int = 25, before: str | None = None) -> dict[str, Any]:
        """List this tenant's acceptance suites, with an older-page cursor."""
        return call("GET", "/suites?" + urlencode({"limit": limit, **({"before": before} if before else {})}))

    @server.tool(annotations=write, structured_output=True)
    def cancel_suite(suite_id: str) -> dict[str, Any]:
        """Cancel the active suite member and prevent execution of all remaining planned cases."""
        path = client.run_path(suite_id).replace("/runs/", "/suites/", 1)
        return call("POST", path + "/cancel")

    @server.tool(annotations=read_only, structured_output=True)
    def compare_runs(baseline: str, current: str, kind: str = "trial",
                     allow_provider_version_change: bool = False) -> dict[str, Any]:
        """Compare tenant-owned trial or suite reports; incompatible contexts remain inconclusive."""
        client.run_path(baseline)
        client.run_path(current)
        return call("POST", "/comparisons", {"baseline": baseline, "current": current, "kind": kind,
                    "allow_provider_version_change": allow_provider_version_change})

    @server.tool(annotations=read_only, structured_output=True)
    def get_signing_key() -> dict[str, Any]:
        """Read the authenticated service's public signing-key fingerprint; no private key is exposed."""
        return call("GET", "/signing")

    @server.tool(annotations=read_only, structured_output=True)
    def export_evidence_bundle(identity: str, kind: str = "trial") -> dict[str, Any]:
        """Export a signed raw-evidence record; independently pin the public key for offline verification."""
        if kind not in {"trial", "suite"}:
            raise ValueError("Choose trial or suite.")
        path = client.run_path(identity)
        if kind == "suite":
            path = path.replace("/runs/", "/suites/", 1)
        return call("POST", path + "/bundle")

    @server.tool(annotations=read_only, structured_output=True)
    def draft_trial(candidates: list[str], name: str, observer: str, arguments: dict,
                    path: list, expected: Any, predicate: str = "eq", actions: list[dict] | None = None) -> dict[str, Any]:
        """Create a validated scenario draft from configured operations and caller-supplied acceptance; perform no provider work."""
        return call("POST", "/authoring/draft", {"candidates": candidates, "name": name, "observer": observer,
            "arguments": arguments, "path": path, "expected": expected, "predicate": predicate, "actions": actions or []})

    @server.tool(annotations=read_only, structured_output=True)
    def preview_suite(suite: dict) -> dict[str, Any]:
        """Inspect all expanded suite cases and logical read/write operations before explicit submission."""
        return call("POST", "/authoring/preview", {"suite": suite})

    return server


def main():
    client = BridgeClient(os.environ.get("PROOFBEAK_API_URL", "http://127.0.0.1:8765"),
                          os.environ.get("PROOFBEAK_API_TOKEN", ""))
    server = create_server(client)
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
