# Pilot protocol and offline scoring

The implementation is ready to collect pilot data. No real developer interviews, external
model experiments, three-person onboarding study, or fourteen-day return observations have
been conducted. Unit-test studies are synthetic regression fixtures and do not establish
product value. The Google live comparison still requires a dedicated account.

## Collect the actual questions first

Ask consenting pilot developers for two recent service-selection decisions: the condition
they needed, candidates considered, the documentation they read, what they chose, and what
later proved wrong or uncertain. Record pseudonymous developer IDs. Do not collect production
credentials, customer records, or unrelated account data. Preparation here sends no invitations.

Choose twelve distinct, closed decision cases from those real needs. The currently executable
packages cover moving one calendar occurrence and writing one storage key twice. Cases outside
those contracts require an explicit new package before measured trials; a prose question is
not automatically compiled into arbitrary provider actions.

Each case names one to four candidates, hashes the exact test JSON and candidate documentation,
and records two reviewers' independent candidate judgments: pass, fail, or inconclusive.
Reviewers establish the reference before seeing agent answers. Agreement is required for a
scored case. If any candidate passes, selecting any passing candidate is correct. If none passes
but a candidate is inconclusive, the correct response is insufficient_evidence; when all
candidates fail, it is no_match. A passing trial with cleanup problems remains a passing
condition with a separate operational issue; resolve its resource ledger before a real pilot.

## Prepare and seal

The optional console command has no third-party dependencies and performs no model calls:

    PYTHONPATH=src python3 -m proofbeak.pilot init .proofbeak/pilot-1

This writes reference.json, answers.json, adoption.json, and an empty evidence directory.
Existing files are never overwritten. Templates contain no answers or claimed observations
and cannot be sealed until their required fields are filled.

Reference example for one case (the hashes below are field descriptions, not usable values):

    {
      "id": "calendar-second-instance",
      "question": "Move only the second weekly occurrence by one hour.",
      "test_sha256": "SHA256_OF_CANONICAL_TEST_JSON",
      "docs_sha256": "SHA256_OF_FROZEN_CANDIDATE_DOCUMENTATION",
      "candidates": ["radicale", "google-calendar"],
      "reviews": [
        {"reviewer_id": "reviewer-1", "recorded_at": "2026-11-12T09:00:00+00:00",
         "judgments": {"radicale": "pass", "google-calendar": "inconclusive"}},
        {"reviewer_id": "reviewer-2", "recorded_at": "2026-11-12T09:05:00+00:00",
         "judgments": {"radicale": "pass", "google-calendar": "inconclusive"}}
      ],
      "evidence": [
        {"path": "evidence/radicale.json", "sha256": "SHA256_OF_REPORT_FILE_BYTES",
         "provider": "radicale"}
      ]
    }

Use proofbeak.models.fingerprint(test) for the canonical test hash. File and document hashes
use SHA-256 of the frozen file bytes. Attach actual exported CLI reports in the study directory.
The scorer checks file digests, exact test hashes, candidate identity, and the normalized raw
before/after evidence hash. Paths cannot leave that directory. Only conclusive records with
real-runner/test-account provenance count toward the four-observation requirement. Simulations,
missing raw records and inconclusive observations do not. The same run/provider/evidence record
cannot be counted twice. Injected Radicale and Google transports are explicitly simulated.

After both reviews:

    PYTHONPATH=src python3 -m proofbeak.pilot seal \
      --reference .proofbeak/pilot-1/reference.json \
      --output .proofbeak/pilot-1/reference.seal.json

Copy the returned reference_sha256 into answers.json. A seal detects later reference changes.
It is a local integrity record, not a trusted timestamp, digital signature, or verification of
reviewer independence. Keep reviewer judgments and the seal out of the agent's prompts.

## Run the paired agent experiment externally

Arm A receives the fixed task, candidates, and frozen documentation. Arm B receives those same
inputs plus Proofbeak tools/evidence. Keep the model version, base instructions, candidate order,
token budget and wall budget identical; isolate sessions so answers cannot leak between arms.
Counterbalance execution order. The external model/runtime is operated separately and its
provider charges are not incurred by this scorer.

Register two to five repetitions in the shared protocol before answers are collected.
Use a unique attempt_id and response_sha256 for each attempt. Record wall_seconds and tokens
for the complete agent decision, including Proofbeak use in B. Unknown measurements remain null,
which excludes the case from budget-matched scoring. A model limit is not a measured token count.
Cost may remain null; the report then labels the arm's cost total as unmeasured.

One answers.json row:

    {
      "case_id": "calendar-second-instance", "arm": "B", "repeat": 1,
      "attempt_id": "calendar-B-1", "decision": "radicale",
      "finished_at": "2026-11-12T10:00:00+00:00",
      "response_sha256": "SHA256_OF_FROZEN_AGENT_RESPONSE",
      "wall_seconds": 12.4, "tokens": 1832, "cost_usd": null
    }

Answers must be timestamped after the seal and must use the exact registered protocol.
Duplicate attempts/repeats, future timestamps, non-finite numbers and unknown decisions are
rejected. Identical case inputs belong in repetitions, not additional decision cases.
Missing repeats, differing repeated decisions, reviewer disagreement, missing budget measurements
and budget overruns are reported separately as exclusions. They cannot improve a score.

False positives count every attempt selecting a conclusively failing candidate, including
unstable or over-budget cases that are excluded from accuracy totals. Selecting a candidate
whose reference is inconclusive is reported separately as an unsupported selection.
The B arm must have zero of each. This prevents a missing observation from appearing safe.

## Observe onboarding and return usage

For exactly three consenting developers, record started_at and first_report_at plus the first
test hash. A qualifying return includes return_at and a different return_test_sha256 within
fourteen days of the first report. Retrying or reopening the same condition is not a return
with a new condition. Leave absent timestamps and hashes null. An observation window that
has not elapsed remains pending; it is not recorded as success.

    {
      "developer_id": "developer-1",
      "started_at": "2026-11-12T09:00:00+00:00",
      "first_report_at": "2026-11-12T09:18:00+00:00",
      "first_test_sha256": "SHA256_OF_FIRST_TEST",
      "return_at": null, "return_test_sha256": null
    }

## Evaluate the collected data

    PYTHONPATH=src python3 -m proofbeak.pilot evaluate \
      --reference .proofbeak/pilot-1/reference.json \
      --seal .proofbeak/pilot-1/reference.seal.json \
      --answers .proofbeak/pilot-1/answers.json \
      --adoption .proofbeak/pilot-1/adoption.json \
      --output reports/pilot-1.json --html reports/pilot-1.html

The report includes per-case decisions/exclusions, separate fail/inconclusive reference counts,
per-arm accuracy/false positives/budgets, adoption counts, and pass/fail/pending gates.
Continue requires twelve stable valid cases, four distinct real observations, zero false
positives or unsupported selections in B, at least two more correct decisions in B, two of three
developers reporting within thirty minutes, and two returning with a new condition within fourteen days.
These are product-plan decision signals, not statistical significance or certification.

Exit 0 means all continuation signals are met; 2 means revise or insufficient_data; 3 means
invalid input. Output files are never overwritten. No public/private catalog is built from
these files automatically. Actual demand from two separate teams still gates catalog work.

## Trial resource measurements

Each newly executed candidate report contains measurements.version=1: monotonic wall time,
current worker process CPU time, and instrumented provider HTTP attempts. Scope includes resource
reservation/provisioning, seeding, independent reads, action, cleanup and ledger updates.
CPU excludes descendant processes and remote provider CPU. HTTP counts attempts, including failed
I/O, but excludes requests stopped before I/O by the budget. Cleanup's separate call-budget reset
does not reset the total counter. Unknown/uninstrumented transports report null, not zero.

Idempotent report retrieval preserves the original measurements. External agent tokens,
agent decision latency and LLM cost are outside the worker; their trial report fields remain
null. Enter actual external measurements in the paired experiment rather than treating worker
wall time as end-to-end agent cost. No measurement changes the acceptance verdict.
