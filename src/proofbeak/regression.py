"""Explicit historical comparisons. A changed test or untrusted context cannot establish a regression."""

import copy

from proofbeak.models import fingerprint


def context(candidate, allow_provider_version_change=False):
    profile = copy.deepcopy(candidate.get("profile"))
    if allow_provider_version_change and isinstance(profile, dict):
        profile.pop("provider_version", None)
        if isinstance(profile.get("settings"), dict):
            profile["settings"].pop("provider_version", None)
    return {key: candidate.get(key) for key in (
        "provider", "adapter_version", "pack_id", "pack_version", "observer_version", "evidence_level")} | {"profile": profile}


def compare_trial(baseline, current, allow_provider_version_change=False):
    changes, incompatible = [], []
    if baseline.get("state") != "completed" or current.get("state") != "completed":
        return {"comparable": False, "reasons": ["run_not_completed"], "changes": [], "regressions": 0}
    old = {c["provider"]: c for c in baseline.get("candidates", [])}
    new = {c["provider"]: c for c in current.get("candidates", [])}
    if not old or set(old) != set(new):
        incompatible.append("candidate_set_changed")
    for name in sorted(set(old) & set(new)):
        before, after = old[name], new[name]
        reason = None
        if any(c.get("evidence") is not None and fingerprint(c["evidence"]) != c.get("evidence_hash") for c in (before, after)):
            reason = "integrity_failed"
        elif not before.get("test_hash") or before.get("test_hash") != after.get("test_hash"):
            reason = "test_changed"
        elif before.get("evidence_level") == "simulation" or after.get("evidence_level") == "simulation":
            reason = "real_observation_required"
        elif fingerprint(context(before, allow_provider_version_change)) != fingerprint(context(after, allow_provider_version_change)):
            reason = "context_changed"
        elif any(c.get("validity", {}).get("reasons") and any(v in c["validity"]["reasons"] for v in
                ("integrity_failed", "revoked", "raw_purged", "raw_evidence_purged", "invalid_timestamp", "invalid_expiry")) for c in (before, after)):
            reason = "evidence_unavailable"
        elif any(not c.get("checks") or c.get("evidence") is None and "evidence_ref" not in c
                 or any(check.get("redacted") for check in c.get("checks", [])) for c in (before, after)):
            reason = "observations_unavailable"
        if reason:
            incompatible.append({"candidate": name, "code": reason})
            continue
        old_checks = {c["field"]: c for c in before["checks"]}
        new_checks = {c["field"]: c for c in after["checks"]}
        if set(old_checks) != set(new_checks):
            incompatible.append({"candidate": name, "code": "conditions_changed"})
            continue
        for field in sorted(old_checks):
            a, b = old_checks[field], new_checks[field]
            if any(fingerprint(a.get(k)) != fingerprint(b.get(k)) for k in ("expected", "operator", "path")):
                incompatible.append({"candidate": name, "condition": field, "code": "expectation_changed"})
                continue
            outcome_a = "inconclusive" if a.get("available") is False else ("pass" if a["passed"] else "fail")
            outcome_b = "inconclusive" if b.get("available") is False else ("pass" if b["passed"] else "fail")
            status = "regression" if outcome_a == "pass" and outcome_b == "fail" else (
                "evidence_lost" if outcome_a != "inconclusive" and outcome_b == "inconclusive" else (
                "improvement" if outcome_a == "fail" and outcome_b == "pass" else (
                "changed" if fingerprint(a.get("observed")) != fingerprint(b.get("observed")) else "unchanged")))
            changes.append({"candidate": name, "condition": field, "status": status,
                            "baseline": outcome_a, "current": outcome_b, "expected": b.get("expected"),
                            "before": a.get("observed"), "after": b.get("observed")})
        if before.get("cleanup_status") == "cleaned" and after.get("cleanup_status") != "cleaned":
            changes.append({"candidate": name, "condition": "cleanup", "status": "regression",
                            "baseline": "pass", "current": "fail", "before": "cleaned",
                            "after": after.get("cleanup_status")})
    return {"comparable": not incompatible, "reasons": incompatible, "changes": changes,
            "regressions": sum(c["status"] == "regression" for c in changes),
            "evidence_lost": sum(c["status"] == "evidence_lost" for c in changes)}


def compare_reports(baseline, current, allow_provider_version_change=False):
    if not isinstance(baseline, dict) or not isinstance(current, dict):
        raise ValueError("Choose two Proofbeak JSON reports.")
    suite = "entries" in baseline or "entries" in current
    if suite and ("entries" not in baseline or "entries" not in current):
        raise ValueError("Compare a suite with another suite, or a trial with another trial.")
    if suite:
        old = {e["id"]: e for e in baseline["entries"]}
        new = {e["id"]: e for e in current["entries"]}
        removed, added = sorted(set(old) - set(new)), sorted(set(new) - set(old))
        entries = []
        for key in sorted(set(old) & set(new)):
            a, b = old[key].get("report"), new[key].get("report")
            result = compare_trial(a, b, allow_provider_version_change) if a and b else {
                "comparable": False, "reasons": ["result_not_available"], "changes": [], "regressions": 0}
            entries.append({"id": key, **result})
        comparable = bool(entries) and not added and not removed and all(e["comparable"] for e in entries)
        regressions = sum(e["regressions"] for e in entries)
        lost = sum(e.get("evidence_lost", 0) for e in entries)
        result = {"comparable": comparable, "entries": entries, "added": added, "removed": removed,
                  "regressions": regressions, "evidence_lost": lost}
    else:
        result = compare_trial(baseline, current, allow_provider_version_change)
    return {"schema_version": "proofbeak.diff.v1", **result,
            "provider_version_change_allowed": allow_provider_version_change,
            "gate": "inconclusive" if not result["comparable"] or result.get("evidence_lost") else (
                "fail" if result["regressions"] else "pass")}
