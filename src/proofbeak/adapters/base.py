from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from proofbeak.models import Snapshot, Trial


class ProviderError(Exception):
    """Only safe reason codes cross the evidence boundary."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Resource:
    id: str
    series_id: str


class Adapter(Protocol):
    name: str
    version: str
    evidence_level: str
    profile: dict

    def resource(self, run_id: str) -> Resource: ...
    def provision(self, resource: Resource, trial: Trial) -> None: ...
    def seed(self, resource: Resource, trial: Trial) -> None: ...
    def read(self, resource: Resource, trial: Trial) -> Snapshot: ...
    def move(self, resource: Resource, trial: Trial) -> None: ...
    def cleanup(self, resource: Resource) -> None: ...
