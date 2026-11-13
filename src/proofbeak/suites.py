"""Bounded, caller-owned test suites; parameters are values, never executable code."""

import copy
import itertools
import re
from datetime import UTC, datetime

from proofbeak.models import canonical, fingerprint
from proofbeak.packs import builtins
from proofbeak.validation import TrialValidationError

SUITE_FORMAT = "suite.v1"
REPORT_FORMAT = "proofbeak.suite-report.v1"
MAX_CASES = 64
NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,47}")


def invalid(message, path=()):
    raise TrialValidationError(message, code="invalid_suite", path=list(path))


def expand_suite(definition):
    """Validate every expanded test before any provider is accessed or job is reserved."""
    if not isinstance(definition, dict) or set(definition) != {"id", "name", "cases"}:
        invalid("A suite needs id, name and cases.")
    if definition["id"] != SUITE_FORMAT:
        invalid("Choose suite.v1.", ["id"])
    if not isinstance(definition["name"], str) or not 1 <= len(definition["name"]) <= 120:
        invalid("Give the suite a name between 1 and 120 characters.", ["name"])
    if len(canonical(definition).encode()) > 65536:
        invalid("The suite definition exceeds 64 KiB.")
    cases = definition["cases"]
    if not isinstance(cases, list) or not 1 <= len(cases) <= MAX_CASES:
        invalid("Choose between 1 and 64 cases.", ["cases"])
    expanded, identities = [], set()
    for index, case in enumerate(cases):
        path = ["cases", index]
        if not isinstance(case, dict) or set(case) - {"id", "test", "candidates", "matrix"} \
                or not {"id", "test", "candidates"} <= set(case):
            invalid("Each case needs id, test, candidates and optionally matrix.", path)
        name = case["id"]
        if not isinstance(name, str) or not NAME.fullmatch(name) or name in identities:
            invalid("Case IDs must be distinct short names beginning with a letter.", path + ["id"])
        identities.add(name)
        candidates = case["candidates"]
        if not isinstance(candidates, list) or not 1 <= len(candidates) <= 4 \
                or any(not isinstance(c, str) or not 1 <= len(c) <= 64 for c in candidates) \
                or len(set(candidates)) != len(candidates):
            invalid("Choose one to four distinct configured candidates.", path + ["candidates"])
        matrix = case.get("matrix", {})
        if not isinstance(matrix, dict) or len(matrix) > 4:
            invalid("A matrix supports up to four named dimensions.", path + ["matrix"])
        dimension_names = sorted(matrix)
        count = 1
        for key in dimension_names:
            values = matrix[key]
            if not NAME.fullmatch(key) or not isinstance(values, list) or not 1 <= len(values) <= 16:
                invalid("Each matrix dimension needs a short name and 1 to 16 values.", path + ["matrix"])
            if len({canonical(v) for v in values}) != len(values):
                invalid("Matrix values must be distinct.", path + ["matrix", key])
            count *= len(values)
        if len(expanded) + count > MAX_CASES:
            invalid("The matrix would create more than 64 executions. Reduce its dimensions.", path + ["matrix"])
        used = set()

        def substitute(value, parameters, depth=0):
            if depth > 24:
                invalid("Suite values are nested too deeply.", path + ["test"])
            if isinstance(value, dict):
                if "$param" in value:
                    key = value["$param"]
                    if set(value) != {"$param"} or not isinstance(key, str) or key not in parameters:
                        invalid("A parameter must refer to one matrix dimension.", path + ["test"])
                    used.add(key)
                    return copy.deepcopy(parameters[key])
                return {key: substitute(item, parameters, depth + 1) for key, item in value.items()}
            if isinstance(value, list):
                return [substitute(item, parameters, depth + 1) for item in value]
            return value

        for values in itertools.product(*(matrix[key] for key in dimension_names)):
            parameters = dict(zip(dimension_names, values))
            test = substitute(case["test"], parameters)
            try:
                builtins().prepare(test)
            except (ValueError, TypeError, RecursionError) as error:
                suffix = getattr(error, "path", [])
                invalid("An expanded scenario is invalid; validate its test definition.", path + ["test"] + suffix)
            expanded.append({"id": name + ":" + fingerprint(parameters)[:16], "case_id": name,
                             "parameters": parameters, "test": test, "test_hash": fingerprint(test),
                             "candidates": list(candidates)})
        if used != set(dimension_names):
            invalid("Every matrix dimension must be used by a $param value.", path + ["matrix"])
    return expanded


def example_suite():
    test = builtins().packs["scenario.v1"].example()
    # The example's fixture can be varied without introducing case-specific operations.
    key = "content" if "content" in test["fixture"] else next(iter(test["fixture"]))
    test["fixture"][key] = {"$param": "content"}
    return {"id": SUITE_FORMAT, "name": "My service acceptance checks", "cases": [{
        "id": "roundtrip", "test": test, "candidates": ["scenario:filesystem"],
        "matrix": {"content": ["Hello", "Merhaba 🌍", "Line one\nLine two"]}}]}


def summarize(entries, state="completed"):
    counts = {"pass": 0, "fail": 0, "inconclusive": 0, "not_run": 0}
    issues = []
    for entry in entries:
        report = entry.get("report")
        if not report or report.get("state") != "completed" or not report.get("candidates"):
            counts["not_run"] += 1
            issues.append({"entry": entry["id"], "code": entry.get("reason_code") or "result_not_available"})
            continue
        for candidate in report["candidates"]:
            verdict = candidate.get("verdict", "inconclusive")
            counts[verdict if verdict in counts else "inconclusive"] += 1
            code = None
            if candidate.get("cleanup_status") != "cleaned":
                code = "cleanup_required"
            elif candidate.get("evidence_level") == "simulation" \
                    or not candidate.get("eligible_for_provider_comparison", False):
                code = "real_observation_required"
            elif candidate.get("evidence") is not None and fingerprint(candidate["evidence"]) != candidate.get("evidence_hash"):
                code = "evidence_integrity_failed"
            elif candidate.get("validity", {}).get("reasons") and any(
                    item in candidate["validity"]["reasons"] for item in
                    ("integrity_failed", "revoked", "raw_purged", "raw_evidence_purged", "invalid_timestamp", "invalid_expiry",
                     "expired", "context_changed", "context_unavailable")):
                code = "evidence_unavailable"
            if code is None:
                try:
                    expiry = datetime.fromisoformat(candidate["expires_at"])
                    if expiry.tzinfo is None or expiry <= datetime.now(UTC):
                        code = "evidence_expired"
                except (ValueError, TypeError, KeyError):
                    code = "invalid_freshness_metadata"
            if code:
                issues.append({"entry": entry["id"], "candidate": candidate["provider"], "code": code})
    gate = "running" if state in {"queued", "running"} else (
        "inconclusive" if issues or counts["inconclusive"] or counts["not_run"] or not entries else
        ("fail" if counts["fail"] else "pass"))
    return {"gate": gate, "counts": counts, "issues": issues}


def suite_report(definition, entries, state="completed", suite_id=None):
    return {"schema_version": REPORT_FORMAT, "suite_id": suite_id,
            "name": definition["name"], "suite_hash": fingerprint(definition), "state": state,
            "generated_at": datetime.now(UTC).isoformat(), "entries": entries,
            **summarize(entries, state)}


def run_suite(definition, make_adapters, store, key):
    """Run through the existing durable CLI ledger; retries retrieve completed child trials."""
    from proofbeak.engine import run_trial
    from proofbeak.validation import candidate_issues

    if not isinstance(key, str) or not 1 <= len(key) <= 128:
        invalid("Provide a stable key between 1 and 128 characters.")
    plan = expand_suite(definition)
    prepared = []
    for item in plan:
        adapters = make_adapters(item["candidates"])
        pack, trial = builtins().prepare(item["test"])
        issues = candidate_issues(pack, trial, adapters)
        if issues:
            raise TrialValidationError("A suite candidate is incompatible.", code="invalid_suite", path=[item["case_id"]])
        prepared.append(adapters)
    # A parent reservation prevents reusing a suite key with a changed definition/configuration.
    context = [[{"name": a.name, "version": a.version, "profile": a.profile,
                 "evidence_level": a.evidence_level} for a in group] for group in prepared]
    parent_key = "suite:" + fingerprint(key)
    request_hash = fingerprint({"definition": definition, "context": context})
    parent_id, existing = store.reserve(parent_key, request_hash)
    if existing is not None:
        return existing
    entries, state = [], "completed"
    for item, adapters in zip(plan, prepared):
        child_key = "case:" + fingerprint({"suite": parent_id, "entry": item["id"]})
        report = run_trial(item["test"], adapters, store, child_key)
        entries.append({**{k: v for k, v in item.items() if k not in {"test", "candidates"}},
                        "run_id": report["run_id"], "report": report})
        if report["state"] != "completed" or any(c["cleanup_status"] != "cleaned" for c in report["candidates"]):
            state = "interrupted"
            break
    for item in plan[len(entries):]:
        entries.append({"id": item["id"], "case_id": item["case_id"], "parameters": item["parameters"],
                        "test_hash": item["test_hash"], "run_id": None, "report": None,
                        "reason_code": "suite_interrupted"})
    result = suite_report(definition, entries, state, parent_id)
    store.finish(parent_id, result)
    return result


def exit_code(report):
    return {"pass": 0, "fail": 1}.get(report.get("gate"), 2)
