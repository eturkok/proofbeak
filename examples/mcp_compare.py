"""Runnable protocol client: discover -> run -> compare -> export independent evidence."""

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

from proofbeak.interfaces.mcp import BridgeClient
from proofbeak.models import fingerprint
from proofbeak.report import render_html


class ClientError(Exception):
    def __init__(self, code, run_id=None, key_prefix=None, issues=None):
        self.code, self.run_id, self.key_prefix, self.issues = code, run_id, key_prefix, issues


def grouped_client_error(error):
    if isinstance(error, ClientError):
        return error
    if isinstance(error, BaseExceptionGroup):
        for child in error.exceptions:
            known = grouped_client_error(child)
            if known:
                return known
    return None


async def compare(args):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    endpoint = os.environ.get("PROOFBEAK_API_URL", "http://127.0.0.1:8765")
    token = os.environ.get("PROOFBEAK_API_TOKEN", "")
    BridgeClient(endpoint, token)  # Validate the operator-owned origin/token before starting the bridge.
    environment = {"PATH": os.defpath, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
                   "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1",
                   "PROOFBEAK_API_URL": endpoint, "PROOFBEAK_API_TOKEN": token}
    params = StdioServerParameters(command=sys.executable,
                                  args=["-m", "proofbeak.interfaces.mcp"], env=environment)
    state = {}
    deadline = asyncio.timeout(args.timeout)
    try:
        async with deadline:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    async def call(name, payload):
                        result = await session.call_tool(name, payload)
                        if result.isError or not isinstance(result.structuredContent, dict):
                            if name == "run_trial" and any("restore_key_prefix_required" in getattr(block, "text", "")
                                                           for block in result.content):
                                raise ClientError("restore_key_prefix_required", key_prefix=state.get("key_prefix"))
                            raise ClientError("tool_call_failed", state.get("run_id"))
                        return result.structuredContent
                    catalog = await call("list_trial_packs", {})
                    state["key_prefix"] = catalog.get("idempotency_key_prefix", "")
                    if args.spec:
                        if args.spec.stat().st_size > 65536:
                            raise ClientError("trial_too_large")
                        spec = json.loads(args.spec.read_text())
                    else:
                        pack = next((p for p in catalog["packages"] if p["id"] == args.trial), None)
                        if pack is None:
                            raise ClientError("unknown_trial")
                        spec = pack["example_test"]
                    if not isinstance(spec, dict) or not isinstance(spec.get("id"), str):
                        raise ClientError("invalid_trial")
                    if len(set(args.candidate)) != len(args.candidate) or len(args.candidate) > 4:
                        raise ClientError("invalid_candidates")
                    tools = await session.list_tools()
                    can_validate = any(tool.name == "validate_trial" for tool in tools.tools)
                    for name in args.candidate:
                        if name not in catalog["providers"]:
                            raise ClientError("candidate_not_allowed")
                        capabilities = catalog["provider_capabilities"].get(name, {})
                        if not capabilities.get("configured") \
                                or (not can_validate and spec["id"] not in capabilities.get("supported_trials", [])):
                            raise ClientError("candidate_not_configured_or_compatible")
                    if can_validate:
                        validation = await call("validate_trial", {"test": spec, "candidates": args.candidate})
                        if not validation.get("valid"):
                            raise ClientError("invalid_trial_definition", issues=validation.get("issues", []))
                    created = await call("run_trial", {
                        "test": spec, "candidates": args.candidate, "idempotency_key": args.key})
                    run_id = created["run_id"]
                    if not isinstance(run_id, str) or not re.fullmatch(r"[a-f0-9]{32}", run_id):
                        raise ClientError("invalid_run_id")
                    state["run_id"] = run_id
                    while True:
                        run = await call("get_run", {"run_id": run_id})
                        if run["state"] == "completed":
                            break
                        if run["state"] in {"interrupted", "cancelled"}:
                            raise ClientError("trial_" + run["state"], run_id)
                        if run["state"] not in {"queued", "running"}:
                            raise ClientError("invalid_run_state", run_id)
                        await asyncio.sleep(.05)
                    report = await call("compare_results", {"run_id": run_id})
                    if report["run_id"] != run_id or report["test"] != spec \
                            or [c["provider"] for c in report["candidates"]] != args.candidate:
                        raise ClientError("comparison_context_mismatch", run_id)
                    for candidate in report["candidates"]:
                        evidence = await call("get_evidence", {"evidence_id": candidate["evidence_ref"]})
                        if evidence["run_id"] != run_id or evidence["provider"] != candidate["provider"] \
                                or evidence["evidence_hash"] != candidate["evidence_hash"] \
                                or fingerprint(evidence["evidence"]) != candidate["evidence_hash"]:
                            raise ClientError("evidence_integrity_failed", run_id)
                        candidate["evidence"] = evidence["evidence"]
                    return report
    except TimeoutError:
        # A client deadline never silently cancels or retries a provider operation.
        raise ClientError("wait_timed_out", state.get("run_id")) from None
    except BaseExceptionGroup as error:
        if deadline.expired():
            raise ClientError("wait_timed_out", state.get("run_id")) from None
        raise grouped_client_error(error) or ClientError("mcp_protocol_error", state.get("run_id")) from None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Exercise Proofbeak's actual MCP tools; no model call.")
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument("--trial", default="storage.put_same_key.v1")
    choice.add_argument("--spec", type=Path)
    parser.add_argument("--candidate", action="append", required=True)
    parser.add_argument("--key", required=True, help="reuse this key for a transport retry")
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--output", type=Path, default=Path("reports/mcp-comparison.json"))
    parser.add_argument("--html", type=Path)
    args = parser.parse_args(argv)
    try:
        if not 0 < args.timeout <= 3600 or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", args.key):
            raise ClientError("invalid_timeout_or_key")
        if args.output.exists() or (args.html and args.html.exists()):
            raise ClientError("output_already_exists")
        if args.html and args.output.resolve() == args.html.resolve():
            raise ClientError("output_paths_must_differ")
        report = asyncio.run(compare(args))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as destination:
            destination.write(json.dumps(report, indent=2) + "\n")
        if args.html:
            args.html.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(args.html, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as destination:
                destination.write(render_html(report))
        print(json.dumps({"run_id": report["run_id"], "state": report["state"],
                          "candidates": [{k: c[k] for k in ("provider", "verdict", "cleanup_status")}
                                         for c in report["candidates"]]}))
        if any(c["cleanup_status"] != "cleaned" or c["verdict"] == "inconclusive"
               for c in report["candidates"]):
            return 2
        return 1 if any(c["verdict"] == "fail" for c in report["candidates"]) else 0
    except ClientError as error:
        print(json.dumps({"error": error.code, "run_id": error.run_id,
                          **({"issues": error.issues} if error.issues is not None else {}),
                          **({"idempotency_key_prefix": error.key_prefix} if error.key_prefix else {})}), file=sys.stderr)
        return 2 if error.run_id or error.code == "wait_timed_out" else 3
    except ImportError:
        print("Install proofbeak[agents] to use the optional MCP client.", file=sys.stderr)
        return 3
    except Exception as error:
        # Provider or SDK errors may contain credentials; emit only the exception type.
        print("MCP comparison failed: " + type(error).__name__, file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
