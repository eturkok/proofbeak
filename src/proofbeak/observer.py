"""Compare separate reads, never a mutation response."""

from collections import Counter
from datetime import UTC

from proofbeak.models import Snapshot, Trial

OBSERVER_VERSION = "0.1.0"


def assess(trial: Trial, before: Snapshot, after: Snapshot) -> dict:
    baseline = before.events
    if not baseline:
        return {"verdict": "inconclusive", "reason_code": "invalid_baseline", "checks": []}
    expected = trial.fixture(baseline[0].series_id)
    if Counter(baseline) != Counter(expected):
        return {"verdict": "inconclusive", "reason_code": "invalid_baseline", "checks": []}
    old = {e.identity: e for e in expected}
    new = {e.identity: e for e in after.events}
    identities_match = len(new) == len(after.events) and set(new) == set(old)
    shifted = [i for i, e in enumerate(expected, 1)
               if e.identity in new and new[e.identity].start != e.start]
    target = expected[trial.index - 1]
    moved = (new[target.identity].start.astimezone(UTC) - target.start.astimezone(UTC)).total_seconds() / 60 \
        if target.identity in new else None
    metrics = {
        "instance_count": len(after.events),
        "shifted_instances": shifted,
        "moved_minutes": moved,
        "duration_unchanged_for_all": identities_match and all(
            new[e.identity].end.astimezone(UTC) - new[e.identity].start.astimezone(UTC)
            == e.end.astimezone(UTC) - e.start.astimezone(UTC) for e in expected
        ),
        "instance_identities_preserved": identities_match,
    }
    checks = [{"field": field, "expected": value, "observed": metrics[field],
               "passed": metrics[field] == value, "source": after.source,
               "observed_at": after.observed_at}
              for field, value in {**trial.expected, "instance_identities_preserved": True}.items()]
    passed = all(check["passed"] for check in checks)
    return {"verdict": "pass" if passed else "fail",
            "reason_code": "conditions_met" if passed else "condition_violated", "checks": checks}
