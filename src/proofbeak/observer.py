"""Domain-neutral assertion evaluation, shared by registered trial packages."""

import math

OBSERVER_VERSION = "0.2.0"


def equal(expected, actual):
    if isinstance(expected, bool) or isinstance(actual, bool):
        return type(expected) is type(actual) and expected == actual
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return math.isfinite(expected) and math.isfinite(actual) and expected == actual
    if type(expected) is not type(actual):
        return False
    if isinstance(expected, list):
        return len(expected) == len(actual) and all(equal(a, b) for a, b in zip(expected, actual))
    if isinstance(expected, dict):
        return expected.keys() == actual.keys() and all(equal(v, actual[k]) for k, v in expected.items())
    return expected == actual


def evaluate(expected: dict, observed: dict, source: str, observed_at: str) -> dict:
    checks = [{"field": field, "expected": value, "observed": observed.get(field),
               "passed": field in observed and equal(value, observed[field]),
               "source": source, "observed_at": observed_at}
              for field, value in expected.items()]
    if not checks or any(field not in observed for field in expected):
        return {"verdict": "inconclusive", "reason_code": "missing_observation", "checks": checks}
    passed = all(check["passed"] for check in checks)
    return {"verdict": "pass" if passed else "fail",
            "reason_code": "conditions_met" if passed else "condition_violated", "checks": checks}
