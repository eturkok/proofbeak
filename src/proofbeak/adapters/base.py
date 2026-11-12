from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from typing import Any

from proofbeak.models import Observation


class ProviderError(Exception):
    """Only safe reason codes cross the evidence boundary."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Resource:
    id: str
    scope_id: str

    @classmethod
    def from_dict(cls, value):
        # Preserve cleanup for resources persisted by the initial calendar-only CLI.
        if set(value) == {"id", "series_id"}:
            return cls(value["id"], value["series_id"])
        return cls(**value)


class Adapter(Protocol):
    name: str
    version: str
    evidence_level: str
    profile: dict
    supported_trials: frozenset[str]

    def resource(self, run_id: str) -> Resource: ...
    def provision(self, resource: Resource, trial: Any) -> None: ...
    def seed(self, resource: Resource, trial: Any) -> None: ...
    def read(self, resource: Resource, trial: Any) -> Observation: ...
    def apply(self, resource: Resource, trial: Any) -> None: ...
    def cleanup(self, resource: Resource) -> None: ...
