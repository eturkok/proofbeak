"""Portable comparison report. All provider-controlled text is escaped."""

import html
import json


def render_html(report: dict) -> str:
    def escape(value):
        return html.escape(str(value), quote=True)

    cards = []
    for candidate in report["candidates"]:
        rows = "".join(
            f'<tr><th>{escape(c["field"])}</th><td>{escape(c["expected"])}</td>'
            f'<td>{escape(c["observed"])}</td><td>{"✓" if c["passed"] else "×"}</td></tr>'
            for c in candidate["checks"])
        cards.append(
            f'<article><p class="eyebrow">{escape(candidate["evidence_level"])}</p>'
            f'<h2>{escape(candidate["provider"])}</h2>'
            f'<p class="verdict {escape(candidate["verdict"])}">{escape(candidate["verdict"])}</p>'
            f'<p>{escape(candidate["reason_code"])}</p>'
            '<div class="scroll"><table><thead><tr><th>Condition</th><th>Expected</th>'
            f'<th>Observed</th><th>Result</th></tr></thead><tbody>{rows}</tbody></table></div>'
            f'<p>Cleanup: {escape(candidate["cleanup_status"])}</p>'
            f'<p class="hash">Evidence SHA-256: {escape(candidate["evidence_hash"])}</p>'
            '<details><summary>Inspect before / after observations</summary>'
            f'<pre>{escape(json.dumps(candidate["evidence"], indent=2))}</pre></details></article>')
    return '''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>Proofbeak · Trial evidence</title><style>
:root{color-scheme:dark;font-family:system-ui,sans-serif;background:#0c182b;color:#f6f1e9}
*{box-sizing:border-box}body{margin:0 auto;max-width:1100px;padding:40px 20px}
header{border-bottom:1px solid #344259;margin-bottom:28px;padding-bottom:24px}
h1{font-size:clamp(2rem,5vw,3.5rem);margin:8px 0}h2{overflow-wrap:anywhere}
.eyebrow{color:#ff934b;text-transform:uppercase;letter-spacing:.12em;font-size:.8rem}
.notice{border-left:3px solid #ff934b;padding:12px 18px;background:#17253a}
article{border:1px solid #344259;border-radius:16px;padding:24px;margin:22px 0;background:#101f34}
.verdict{font-size:1.3rem;font-weight:700}.pass{color:#7ee2b8}.fail{color:#ff9292}
.inconclusive{color:#ffd381}.scroll{overflow:auto}table{width:100%;border-collapse:collapse}
td,th{text-align:left;padding:12px 8px;border-bottom:1px solid #344259;font-size:.9rem}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#0c182b;padding:16px}
.hash{font-size:.8rem;color:#a6b4ca;overflow-wrap:anywhere}summary{cursor:pointer}
</style><header><p class="eyebrow">Proofbeak / acceptance trial</p>
<h1>Choose with evidence.</h1><p>One condition. The same trial. Independent reads.</p></header>
<p class="notice">Simulation results are fault-injection controls and cannot establish provider
capability. A pass describes only this trial in the recorded context. Evidence hashes detect changes;
they are not signatures or certification.</p>''' + \
        f'<p class="hash">Run {escape(report["run_id"])}</p>' + "".join(cards) + "</html>"
