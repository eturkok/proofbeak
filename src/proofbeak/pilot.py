"""Offline, explicit pilot scoring. No model calls, recruitment, or fabricated observations."""

import argparse
import hashlib
import html
import json
import math
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from proofbeak.models import canonical, fingerprint, keys

VERDICTS = {"pass", "fail", "inconclusive"}
ABSTENTIONS = {"no_match", "insufficient_evidence"}


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value):
        raise ValueError("invalid pilot identifier")
    return value


def digest(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError("expected a SHA-256 digest; fill the template before sealing")
    return value


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError("timestamp requires timezone")
        return parsed.astimezone(UTC)
    except (TypeError, ValueError):
        raise ValueError("expected an ISO timestamp with timezone") from None


def number(value, *, integer=False):
    if type(value) not in ({int} if integer else {int, float}) \
            or not math.isfinite(value) or value < 0:
        raise ValueError("expected a finite non-negative measurement")
    return value


def read_json(path):
    path = Path(path)
    if path.stat().st_size > 4_194_304:
        raise ValueError("pilot input exceeds 4 MiB")
    def reject(_):
        raise ValueError("non-finite JSON number")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    return json.loads(path.read_text(), parse_constant=reject, object_pairs_hook=unique)


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:
        stream.write(json.dumps(value, indent=2, allow_nan=False) + "\n")


def protocol(value):
    keys(value, {"model", "instructions_sha256", "repeats", "token_budget", "wall_budget_seconds"},
         "pilot protocol")
    if not isinstance(value["model"], str) or not 1 <= len(value["model"]) <= 256:
        raise ValueError("specify the identical model/version used by both arms")
    digest(value["instructions_sha256"])
    if type(value["repeats"]) is not int or not 2 <= value["repeats"] <= 5:
        raise ValueError("pilot requires two to five repetitions per arm")
    if number(value["token_budget"], integer=True) == 0 \
            or number(value["wall_budget_seconds"]) == 0:
        raise ValueError("pilot budgets must be positive")


def reference(value, root):
    keys(value, {"schema_version", "study_id", "protocol", "cases"}, "pilot reference")
    if value["schema_version"] != "1":
        raise ValueError("unknown pilot reference version")
    identifier(value["study_id"])
    protocol(value["protocol"])
    if not isinstance(value["cases"], list) or not 1 <= len(value["cases"]) <= 100:
        raise ValueError("pilot requires one to 100 cases; continuation requires twelve")
    cases, inputs, latest_review = {}, set(), datetime.min.replace(tzinfo=UTC)
    for case in value["cases"]:
        keys(case, {"id", "question", "test_sha256", "docs_sha256", "candidates", "reviews", "evidence"},
             "pilot case")
        case_id = identifier(case["id"])
        if case_id in cases:
            raise ValueError("duplicate pilot case")
        if not isinstance(case["question"], str) or not 1 <= len(case["question"]) <= 4096:
            raise ValueError("each case needs its actual acceptance question")
        digest(case["test_sha256"])
        digest(case["docs_sha256"])
        names = case["candidates"]
        if not isinstance(names, list) or not 1 <= len(names) <= 4:
            raise ValueError("each case requires one to four candidates")
        for name in names:
            identifier(name)
        if len(set(names)) != len(names) or set(names) & ABSTENTIONS:
            raise ValueError("duplicate or reserved candidate identifier")
        context = (case["test_sha256"], case["docs_sha256"], tuple(names))
        if context in inputs:
            raise ValueError("duplicate decision case; use repetitions for identical inputs")
        inputs.add(context)
        if not isinstance(case["reviews"], list) or len(case["reviews"]) != 2:
            raise ValueError("each case requires two independent reviewer records")
        reviewers, votes = set(), []
        for review in case["reviews"]:
            keys(review, {"reviewer_id", "recorded_at", "judgments"}, "review")
            reviewers.add(identifier(review["reviewer_id"]))
            latest_review = max(latest_review, timestamp(review["recorded_at"]))
            judgments = review["judgments"]
            if not isinstance(judgments, dict) or set(judgments) != set(names) \
                    or any(v not in VERDICTS for v in judgments.values()):
                raise ValueError("review judgments must cover exactly the case candidates")
            votes.append(judgments)
        if len(reviewers) != 2:
            raise ValueError("reviewer identifiers must differ")
        consensus = votes[0] if votes[0] == votes[1] else None
        observations = set()
        if not isinstance(case["evidence"], list) or len(case["evidence"]) > 20:
            raise ValueError("evidence references must be a bounded list")
        for item in case["evidence"]:
            keys(item, {"path", "sha256", "provider"}, "evidence reference")
            if item["provider"] not in names or not isinstance(item["path"], str):
                raise ValueError("evidence provider must be a case candidate")
            relative = Path(item["path"])
            path = (root / relative).resolve()
            if relative.is_absolute() or ".." in relative.parts \
                    or not path.is_relative_to(root.resolve()):
                raise ValueError("evidence paths must remain inside the study directory")
            report = read_json(path)
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest(item["sha256"]):
                raise ValueError("evidence file digest changed")
            if not isinstance(report, dict) or report.get("state") != "completed" \
                    or fingerprint(report.get("test")) != case["test_sha256"]:
                raise ValueError("evidence must be a completed report for this exact test")
            if not isinstance(report.get("run_id"), str) \
                    or not re.fullmatch(r"[a-f0-9]{32}", report["run_id"]):
                raise ValueError("trial evidence must include a run identifier")
            matched = [c for c in report.get("candidates", []) if c.get("provider") == item["provider"]]
            if len(matched) != 1:
                raise ValueError("evidence provider is missing or duplicated")
            candidate = matched[0]
            raw = candidate.get("evidence")
            if not isinstance(raw, dict) or fingerprint(raw) != candidate.get("evidence_hash") \
                    or candidate.get("test_hash") != case["test_sha256"]:
                raise ValueError("raw evidence is absent or its digest/context changed")
            if consensus and candidate.get("verdict") != consensus[item["provider"]]:
                raise ValueError("reference judgments conflict with attached trial evidence")
            if candidate.get("evidence_level") in {"local_runner", "provider_test_account"} \
                    and candidate.get("eligible_for_provider_comparison") is True \
                    and candidate.get("verdict") in {"pass", "fail"} \
                    and isinstance(raw.get("before"), dict) and isinstance(raw.get("after"), dict):
                observations.add(fingerprint({"run": report.get("run_id"), "provider": item["provider"],
                                             "evidence": candidate["evidence_hash"]}))
        cases[case_id] = {"case": case, "consensus": consensus, "observations": observations}
    return cases, latest_review


def seal_reference(path, output, *, now=None):
    value = read_json(path)
    _, latest = reference(value, Path(path).parent)
    now = now or datetime.now(UTC)
    if latest > now:
        raise ValueError("review timestamps cannot be in the future")
    seal = {"schema_version": "1", "study_id": value["study_id"],
            "reference_sha256": fingerprint(value), "sealed_at": now.isoformat()}
    write_new(output, seal)
    return seal


def adoption_metrics(value, now):
    keys(value, {"schema_version", "study_id", "developers"}, "adoption")
    if value["schema_version"] != "1" or not isinstance(value["developers"], list) \
            or len(value["developers"]) > 3:
        raise ValueError("adoption input allows at most three pilot developers")
    seen, quick, returned, matured, onboarded = set(), 0, 0, 0, 0
    for row in value["developers"]:
        keys(row, {"developer_id", "started_at", "first_report_at", "first_test_sha256",
                   "return_at", "return_test_sha256"}, "developer")
        name = identifier(row["developer_id"])
        if name in seen:
            raise ValueError("duplicate developer")
        seen.add(name)
        start = timestamp(row["started_at"])
        if start > now:
            raise ValueError("onboarding cannot start in the future")
        onboarded += row["first_report_at"] is not None or now - start >= timedelta(minutes=30)
        if row["first_report_at"] is None:
            if any(row[k] is not None for k in ("first_test_sha256", "return_at", "return_test_sha256")):
                raise ValueError("return usage requires a first report")
            continue
        first = timestamp(row["first_report_at"])
        digest(row["first_test_sha256"])
        if not start <= first <= now:
            raise ValueError("invalid first report timestamp")
        quick += first - start <= timedelta(minutes=30)
        matured += now - first >= timedelta(days=14)
        if row["return_at"] is not None:
            again = timestamp(row["return_at"])
            digest(row["return_test_sha256"])
            if not first < again <= now:
                raise ValueError("invalid return timestamp")
            returned += again - first <= timedelta(days=14) \
                and row["return_test_sha256"] != row["first_test_sha256"]
        elif row["return_test_sha256"] is not None:
            raise ValueError("return test requires a return timestamp")
    return {"developers": len(seen), "first_report_within_30m": quick,
            "new_condition_within_14d": returned, "fourteen_day_windows_observed": matured,
            "onboarding_windows_observed": onboarded}


def evaluate(reference_path, seal_path, answers_path, adoption_path, *, now=None):
    now = now or datetime.now(UTC)
    ref, seal, answers, adoption = map(read_json, (reference_path, seal_path, answers_path, adoption_path))
    cases, latest_review = reference(ref, Path(reference_path).parent)
    keys(seal, {"schema_version", "study_id", "reference_sha256", "sealed_at"}, "reference seal")
    if seal["schema_version"] != "1" or seal["reference_sha256"] != fingerprint(ref):
        raise ValueError("reference changed after sealing")
    sealed_at = timestamp(seal["sealed_at"])
    if not latest_review <= sealed_at <= now:
        raise ValueError("reference must be sealed after reviews and before answers")
    keys(answers, {"schema_version", "study_id", "reference_sha256", "protocol", "answers"}, "answers")
    if answers["schema_version"] != "1" or answers["reference_sha256"] != seal["reference_sha256"] \
            or canonical(answers["protocol"]) != canonical(ref["protocol"]):
        raise ValueError("answer protocol or reference differs from the sealed study")
    if any(item["study_id"] != ref["study_id"] for item in (seal, answers, adoption)):
        raise ValueError("study identifiers differ")
    if not isinstance(answers["answers"], list) or len(answers["answers"]) > 1000:
        raise ValueError("answer list exceeds the pilot bound")
    indexed, attempts = {}, set()
    scores = {arm: {"correct_cases": 0, "false_positive_attempts": 0,
                    "unsupported_selection_attempts": 0, "wall_seconds": 0, "tokens": 0,
                    "cost_usd": 0, "attempts": 0, "unmeasured_wall_seconds_attempts": 0,
                    "unmeasured_tokens_attempts": 0, "unmeasured_cost_attempts": 0} for arm in ("A", "B")}
    for row in answers["answers"]:
        keys(row, {"case_id", "arm", "repeat", "attempt_id", "decision", "finished_at",
                   "response_sha256", "wall_seconds", "tokens", "cost_usd"}, "answer")
        case_id, arm, repeat = row["case_id"], row["arm"], row["repeat"]
        if case_id not in cases or arm not in scores or type(repeat) is not int \
                or not 1 <= repeat <= ref["protocol"]["repeats"]:
            raise ValueError("unknown case, arm, or repeat")
        key = (case_id, arm, repeat)
        attempt = identifier(row["attempt_id"])
        if key in indexed or attempt in attempts:
            raise ValueError("duplicate answer or attempt identifier")
        attempts.add(attempt)
        scores[arm]["attempts"] += 1
        digest(row["response_sha256"])
        if not sealed_at <= timestamp(row["finished_at"]) <= now:
            raise ValueError("answers must be recorded after the reference seal")
        if row["decision"] not in set(cases[case_id]["case"]["candidates"]) | ABSTENTIONS:
            raise ValueError("unknown decision")
        issues = []
        for field, limit in (("wall_seconds", "wall_budget_seconds"), ("tokens", "token_budget")):
            if row[field] is None:
                issues.append("budget_unmeasured")
                scores[arm]["unmeasured_" + field + "_attempts"] += 1
            else:
                number(row[field], integer=field == "tokens")
                scores[arm][field] += row[field]
                if row[field] > ref["protocol"][limit]:
                    issues.append("budget_exceeded")
        if row["cost_usd"] is None:
            scores[arm]["unmeasured_cost_attempts"] += 1
        else:
            scores[arm]["cost_usd"] += number(row["cost_usd"])
        consensus = cases[case_id]["consensus"]
        if consensus:
            verdict = consensus.get(row["decision"])
            scores[arm]["false_positive_attempts"] += verdict == "fail"
            scores[arm]["unsupported_selection_attempts"] += verdict == "inconclusive"
        indexed[key] = (row, issues)
    valid, excluded, observations = [], [], set()
    for case_id, item in cases.items():
        issues, decisions = [], {}
        if item["consensus"] is None:
            issues.append("reviewer_disagreement")
        for arm in scores:
            rows = [indexed.get((case_id, arm, r)) for r in range(1, ref["protocol"]["repeats"] + 1)]
            if any(row is None for row in rows):
                issues.append("missing_repeats_" + arm)
                continue
            for _, problems in rows:
                issues.extend(problems)
            choices = {row["decision"] for row, _ in rows}
            if len(choices) != 1:
                issues.append("unstable_" + arm)
            else:
                decisions[arm] = choices.pop()
        if issues:
            excluded.append({"case_id": case_id, "reasons": sorted(set(issues))})
            continue
        judgments = item["consensus"]
        acceptable = {name for name, verdict in judgments.items() if verdict == "pass"}
        if not acceptable:
            acceptable = {"insufficient_evidence" if "inconclusive" in judgments.values() else "no_match"}
        for arm in scores:
            scores[arm]["correct_cases"] += decisions[arm] in acceptable
        observations.update(item["observations"])
        valid.append({"case_id": case_id, "acceptable_decisions": sorted(acceptable), "decisions": decisions})
    for score in scores.values():
        if score["unmeasured_cost_attempts"] or not score["attempts"]:
            score["cost_usd"] = None
        for field in ("wall_seconds", "tokens"):
            if score["unmeasured_" + field + "_attempts"] or not score["attempts"]:
                score[field] = None
    usage = adoption_metrics(adoption, now)
    complete = len(cases) == len(valid) == 12
    delta = scores["B"]["correct_cases"] - scores["A"]["correct_cases"]
    def checked(condition, ready=True):
        return "pass" if condition else ("fail" if ready else "pending")
    gates = {
        "twelve_valid_stable_cases": checked(complete, False),
        "four_real_observations": checked(len(observations) >= 4, complete),
        "zero_false_positives_B": checked(scores["B"]["false_positive_attempts"] == 0, complete),
        "zero_unsupported_selections_B": checked(scores["B"]["unsupported_selection_attempts"] == 0, complete),
        "correct_decision_gain_at_least_two": checked(delta >= 2, complete),
        "two_of_three_first_reports_within_30m": checked(usage["developers"] == 3 and
                                                        usage["first_report_within_30m"] >= 2,
                                                        usage["onboarding_windows_observed"] == 3),
        "two_return_with_new_condition_within_14d": checked(usage["developers"] == 3 and
                                                            usage["new_condition_within_14d"] >= 2,
                                                            usage["fourteen_day_windows_observed"] == 3),
    }
    # A zero count is not a positive result while answers are missing.
    for gate, count in (("zero_false_positives_B", "false_positive_attempts"),
                        ("zero_unsupported_selections_B", "unsupported_selection_attempts")):
        gates[gate] = "fail" if scores["B"][count] else ("pass" if complete else "pending")
    decision = "continue" if all(v == "pass" for v in gates.values()) \
        else ("revise" if "fail" in gates.values() else "insufficient_data")
    return {"schema_version": "1", "study_id": ref["study_id"], "reference_sha256": fingerprint(ref),
            "evaluated_at": now.isoformat(), "decision": decision, "gates": gates,
            "valid_cases": valid, "excluded_cases": excluded, "arms": scores,
            "correct_decision_gain": delta, "distinct_real_observations": len(observations),
            "cases_containing_reference_verdict": {
                v: sum(c["consensus"] is not None and v in c["consensus"].values()
                       for c in cases.values()) for v in sorted(VERDICTS)},
            "adoption": usage,
            "limits": ["Operator-declared protocol and reviewer independence are not independently verified.",
                       "Local seals and evidence hashes detect changes; they are not trusted timestamps or signatures.",
                       "No agent/model run or human pilot is performed by this evaluator."]}


def render_html(report):
    escaped = html.escape(json.dumps(report, indent=2), quote=True)
    return ('<!doctype html><html lang="en"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'">'
            '<title>Proofbeak pilot evaluation</title><style>'
            'body{font:16px system-ui;max-width:1000px;margin:40px auto;padding:0 20px}'
            'pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>'
            '<h1>Proofbeak pilot evaluation</h1><p>Missing observations stay unmeasured. '
            'A report is not independent certification.</p><pre>' + escaped + '</pre></html>')


def initialize(directory):
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    settings = {"model": "REPLACE_WITH_EXACT_MODEL_VERSION", "instructions_sha256": None,
                "repeats": 2, "token_budget": 8000, "wall_budget_seconds": 120}
    cases = [{"id": f"case-{i:02d}", "question": "REPLACE_WITH_ACTUAL_DEVELOPER_ACCEPTANCE_CONDITION",
              "test_sha256": None, "docs_sha256": None, "candidates": ["candidate-a", "candidate-b"],
              "reviews": [], "evidence": []} for i in range(1, 13)]
    ref = {"schema_version": "1", "study_id": "pilot-1", "protocol": settings, "cases": cases}
    documents = {"reference.json": ref, "answers.json": {"schema_version": "1", "study_id": "pilot-1",
                 "reference_sha256": None, "protocol": settings, "answers": []},
                 "adoption.json": {"schema_version": "1", "study_id": "pilot-1", "developers": []}}
    if any((root / name).exists() for name in documents):
        raise ValueError("pilot initialization never overwrites study files")
    for name, value in documents.items():
        write_new(root / name, value)
    (root / "evidence").mkdir(exist_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="proofbeak-pilot")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="write empty study templates without running a pilot")
    init.add_argument("directory", type=Path)
    seal = commands.add_parser("seal", help="freeze reviewed references before collecting agent answers")
    seal.add_argument("--reference", type=Path, required=True)
    seal.add_argument("--output", type=Path, required=True)
    score = commands.add_parser("evaluate", help="score explicitly collected A/B attempts and adoption")
    for name in ("reference", "seal", "answers", "adoption", "output"):
        score.add_argument("--" + name, type=Path, required=True)
    score.add_argument("--html", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            initialize(args.directory)
            print("Empty templates created. Fill actual observations before sealing.")
        elif args.command == "seal":
            print(canonical(seal_reference(args.reference, args.output)))
        else:
            report = evaluate(args.reference, args.seal, args.answers, args.adoption)
            write_new(args.output, report)
            if args.html:
                args.html.parent.mkdir(parents=True, exist_ok=True)
                with args.html.open("x") as stream:
                    stream.write(render_html(report))
            print(report["decision"])
            return 0 if report["decision"] == "continue" else 2
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f"Invalid pilot input: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
