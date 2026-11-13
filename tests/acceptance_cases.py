"""Author-owned evaluation inputs; no additions to the product's package registry."""

import html
import json
from pathlib import Path

from proofbeak.models import fingerprint
from proofbeak.packs import builtins

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "examples/evaluation/manifest.json"
OBJECT_CANDIDATES = ["scenario:webdav:wsgidav", "scenario:webdav:apache"]
HTTP_CANDIDATES = ["scenario:http:catalog-a", "scenario:http:catalog-b"]


def load_cases():
    manifest = json.loads(MANIFEST.read_text())
    cases, hashes, identities = [], set(), set()
    for entry in manifest["cases"]:
        definition = (MANIFEST.parent / entry["definition"]).resolve()
        if not definition.is_relative_to(ROOT / "examples") or definition.stat().st_size > 65536:
            raise ValueError("evaluation input must be a bounded example definition")
        spec = json.loads(definition.read_text())
        pack, _ = builtins().prepare(spec)
        digest = fingerprint(spec)
        if pack.id != "scenario.v1" or digest in hashes or entry["id"] in identities:
            raise ValueError("evaluation cases must have distinct identities and definitions")
        if entry["candidates"] not in (OBJECT_CANDIDATES, HTTP_CANDIDATES) \
                or set(entry["expected"]) != set(entry["candidates"]) \
                or not set(entry["expected"].values()) <= {"pass", "fail", "inconclusive"}:
            raise ValueError("invalid evaluation candidate expectations")
        hashes.add(digest)
        identities.add(entry["id"])
        cases.append({**entry, "path": definition, "spec": spec, "test_hash": digest})
    return manifest, cases


def http_profiles(origins):
    profiles = {}
    for name, origin in zip(HTTP_CANDIDATES, origins):
        first = name.endswith("-a")
        prefix = "service" if first else "api"
        mapping = {"ready": [prefix, "ready" if first else "enabled"],
                   "max_items": [prefix, "capacity" if first else "limit"],
                   "retention_days": [prefix, "retention_days"], "audit_export": [prefix, "audit_export"]}
        profiles[name] = {"endpoint": origin, "routes": {"capabilities": {
            "path": "/v1/capabilities" if first else "/v2/meta", "project": mapping}}}
    return profiles


def selection(observed):
    eligible = [name for name, verdict in observed.items() if verdict == "pass"]
    status = "eligible_candidates" if eligible else (
        "insufficient_evidence" if "inconclusive" in observed.values() else "no_match")
    return {"selection_status": status, "eligible_candidates": eligible}


def render_summary(summary):
    escape = lambda value: html.escape(str(value), quote=True)
    rows = "".join('<tr><td>' + escape(row["case_id"]) + '</td><td>' + escape(row["question"]) +
        '</td><td>' + '<br>'.join(escape(name + ": " + verdict) for name, verdict in row["observed"].items()) +
        '</td><td>' + escape(row["selection_status"]) + '</td><td><a href="' + escape(row["report_html"]) +
        '">Evidence</a></td></tr>' for row in summary["cases"])
    return '''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>Proofbeak acceptance evaluation</title><style>
body{font-family:system-ui,sans-serif;margin:2rem;background:#0c182b;color:#f6f1e9}
a{color:#ff934b}table{border-collapse:collapse;width:100%}td,th{padding:.8rem;text-align:left;border:1px solid #344259}
.scroll{overflow:auto}pre{white-space:pre-wrap;overflow-wrap:anywhere}
</style><h1>Proofbeak acceptance evaluation</h1>
<p>Author-prepared synthetic questions, actual provider reads. No human adoption or external-agent quality claim.</p>
<p>Candidate pass/fail describes this trial only. Cleanup is recorded separately in each report.</p>
<div class="scroll"><table><tr><th>Case</th><th>Question</th><th>Observed</th><th>Selection</th><th>Report</th></tr>''' + \
        rows + '</table></div><h2>Summary</h2><pre>' + escape(json.dumps(
            {k: v for k, v in summary.items() if k != "cases"}, indent=2)) + '</pre></html>'
