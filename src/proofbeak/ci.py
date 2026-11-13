"""Portable CI exports; missing observations and incomplete cleanup fail the gate."""

import html
import json
from xml.etree.ElementTree import Element, SubElement, tostring

from proofbeak.suites import summarize


def entries(report):
    if "entries" in report:
        return report["entries"]
    if "candidates" in report:
        return [{"id": report.get("run_id", "trial"), "case_id": report.get("test", {}).get("name", "Trial"),
                 "report": report}]
    raise ValueError("Choose a Proofbeak trial or suite report.")


def gate(report):
    return summarize(entries(report), report.get("state", "interrupted"))


def junit(report):
    root = Element("testsuite", name="Proofbeak acceptance", tests="0", failures="0", errors="0")
    total = failures = errors = 0
    for entry in entries(report):
        child = entry.get("report") or {}
        candidates = child.get("candidates") or [{}]
        for candidate in candidates:
            total += 1
            test = SubElement(root, "testcase", classname=str(entry.get("case_id", "trial")),
                              name=str(entry["id"]) + " / " + str(candidate.get("provider", "not run")))
            status = summarize([{**entry, "report": {**child, "candidates": [candidate]}}]) if candidate else {
                "gate": "inconclusive", "issues": [{"code": "result_not_available"}]}
            if status["gate"] == "fail":
                failures += 1
                SubElement(test, "failure", message="Acceptance condition violated").text = json.dumps(candidate.get("checks", []))
            elif status["gate"] != "pass":
                errors += 1
                SubElement(test, "error", message="Evidence or cleanup is incomplete").text = json.dumps(status["issues"])
    root.set("tests", str(total))
    root.set("failures", str(failures))
    root.set("errors", str(errors))
    return '<?xml version="1.0" encoding="utf-8"?>\n' + tostring(root, encoding="unicode") + "\n"


def markdown(report):
    def cell(value):
        return str(value).replace("\n", " ").replace("\r", " ").replace("|", "\\|").replace("`", "'")
    status = gate(report)
    lines = ["## Proofbeak acceptance", "", f'**Gate: {status["gate"]}**', "",
             "| Case | Candidate | Acceptance | Cleanup |", "| --- | --- | --- | --- |"]
    for entry in entries(report):
        for candidate in (entry.get("report") or {}).get("candidates", []) or [{}]:
            lines.append("| " + " | ".join(cell(x) for x in (entry["id"], candidate.get("provider", "Not run"),
                         candidate.get("verdict", "inconclusive"), candidate.get("cleanup_status", "unavailable"))) + " |")
    lines += ["", "A pass applies to the recorded test and context. Inconclusive results are not passes.", ""]
    return "\n".join(lines)


def render_suite_html(report):
    escape = lambda value: html.escape(str(value), quote=True)
    status = gate(report)
    cards = []
    for entry in entries(report):
        rows = []
        for candidate in (entry.get("report") or {}).get("candidates", []):
            checks = "".join(f'<tr><th>{escape(c["field"])}</th><td>{escape(c.get("expected"))}</td>'
                             f'<td>{escape(c.get("observed"))}</td><td>{"Unavailable" if c.get("available") is False else ("Pass" if c["passed"] else "Fail")}</td></tr>'
                             for c in candidate.get("checks", []))
            rows.append(f'<h3>{escape(candidate["provider"])} · {escape(candidate["verdict"])}</h3>'
                        f'<p>Cleanup: {escape(candidate["cleanup_status"])}</p><div class="scroll">'
                        '<table><thead><tr><th>Condition</th><th>Expected</th><th>Observed</th><th>Result</th>'
                        f'</tr></thead><tbody>{checks}</tbody></table></div>')
        cards.append(f'<article><h2>{escape(entry.get("case_id", entry["id"]))}</h2>'
                     f'<p class="muted">{escape(entry["id"])}</p>' + ("".join(rows) or "<p>Not run. Evidence is unavailable.</p>") + "</article>")
    return ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'">'
            '<title>Proofbeak · Acceptance suite</title><style>body{font:16px system-ui;margin:auto;max-width:1100px;padding:32px 20px;background:#f4f6f8;color:#18283a}'
            'article{background:white;border:1px solid #dce2e8;border-radius:16px;padding:24px;margin:20px 0}h1{font-size:2.5rem}.muted{color:#526174;overflow-wrap:anywhere}'
            '.scroll{overflow:auto}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:10px;border-bottom:1px solid #e7ebef;overflow-wrap:anywhere}h3{margin-top:28px}</style>'
            f'<p>PROOFBEAK / ACCEPTANCE SUITE</p><h1>{escape(report.get("name", "Acceptance results"))}</h1>'
            f'<p>Gate: <strong>{escape(status["gate"])}</strong></p><p class="muted">Independent observations, with cleanup reported separately.</p>'
            + "".join(cards) + '</html>')
