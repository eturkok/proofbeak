"""Explicit fault injection. These results cannot support provider selection."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import os
from threading import Event

from proofbeak.adapters.base import ProviderError, Resource
from proofbeak.packs.calendar import Snapshot, Trial


class SimulationAdapter:
    version = "0.3.0"
    supported_trials = frozenset({"calendar.move_one.v1"})
    evidence_level = "simulation"

    def __init__(self, mode: str = "correct"):
        if mode not in {"correct", "move_all", "duplicate", "permission_denied", "timeout", "hang", "crash"}:
            raise ValueError("unknown simulation mode")
        self.mode = mode
        self.name = f"simulation:{mode}"
        self.profile = {"mode": mode}
        self.events = {}
        self.reads = 0

    def resource(self, run_id: str) -> Resource:
        return Resource(f"proofbeak-{run_id}-{self.mode}", f"{run_id}-{self.mode}")

    def provision(self, resource: Resource, trial: Trial) -> None:
        self.events[resource.id] = []

    def seed(self, resource: Resource, trial: Trial) -> None:
        self.events[resource.id] = trial.fixture(resource.scope_id)

    def read(self, resource: Resource, trial: Trial) -> Snapshot:
        self.reads += 1
        if self.mode == "permission_denied":
            raise ProviderError("permission_denied")
        return Snapshot(list(self.events[resource.id]), self.name, datetime.now(UTC).isoformat())

    def apply(self, resource: Resource, trial: Trial) -> None:
        if self.mode == "hang":
            Event().wait(3600)
        if self.mode == "crash":
            os._exit(23)
        if self.mode == "timeout":
            raise ProviderError("write_outcome_unknown")
        events = self.events[resource.id]
        delta = timedelta(minutes=trial.shift_minutes)
        for i, event in enumerate(events):
            if self.mode == "move_all" or i == trial.index - 1:
                events[i] = replace(event, start=event.start + delta, end=event.end + delta)
        if self.mode == "duplicate":
            events.append(events[trial.index - 1])

    def cleanup(self, resource: Resource) -> None:
        self.events.pop(resource.id, None)
