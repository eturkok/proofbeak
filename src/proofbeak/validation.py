"""Bounded, value-free authoring diagnostics shared by CLI, REST and MCP."""

from contextlib import contextmanager


class TrialValidationError(ValueError):
    def __init__(self, message, *, code="invalid_definition", path=()):
        self.issue = {"code": code, "message": message[:200], "path": list(path)}
        super().__init__(self.issue["message"])


@contextmanager
def located(path, code="invalid_definition"):
    """Attach nested definition locations without echoing fixture/argument values."""
    try:
        yield
    except TrialValidationError as error:
        raise TrialValidationError(error.issue["message"], code=error.issue["code"],
                                   path=[*path, *error.issue["path"]]) from None
    except (ValueError, TypeError) as error:
        raise TrialValidationError(str(error), code=code, path=path) from None


def issue_from(error):
    if isinstance(error, TrialValidationError):
        return dict(error.issue)
    return {"code": "invalid_definition", "message": str(error)[:200], "path": []}


def validation_result(issues, **metadata):
    return {"valid": not issues, "issues": issues, "execution_performed": False,
            "provider_access_checked": False, **metadata}


def candidate_issues(pack, trial, adapters):
    """One diagnostic per incompatible candidate; definitions were already parsed."""
    issues = []
    for adapter in adapters:
        if pack.id not in adapter.supported_trials:
            issue = {"code": "unsupported_trial_for_provider", "path": ["id"],
                     "message": "Candidate does not support this trial format."}
        else:
            try:
                if hasattr(pack, "validate_adapter"):
                    pack.validate_adapter(trial, adapter)
                continue
            except TrialValidationError as error:
                issue = issue_from(error)
            except (ValueError, TypeError):
                issue = {"code": "scenario_not_supported_by_provider", "path": [],
                         "message": "Candidate does not support this definition."}
        issues.append({**issue, "candidate": adapter.name})
    return issues


def issue_location(issue):
    def segment(part):
        text = str(part).replace("~", "~0").replace("/", "~1")
        return "".join(f"\\u{ord(char):04x}" if ord(char) < 32 or ord(char) == 127 else char for char in text)
    return "/" + "/".join(segment(part) for part in issue["path"])
