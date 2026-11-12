"""Explicit built-in registry; trial input cannot import code or load plugins."""

from typing import Protocol

from proofbeak.models import Observation


class TrialPack(Protocol):
    id: str
    version: str

    def parse(self, spec: dict): ...
    def valid_baseline(self, trial, before: Observation) -> bool: ...
    def assess(self, trial, before: Observation, after: Observation) -> dict: ...


class PackRegistry:
    def __init__(self, packs):
        self.packs = {}
        for pack in packs:
            if pack.id in self.packs:
                raise ValueError("duplicate trial package")
            self.packs[pack.id] = pack

    def prepare(self, spec: dict):
        if not isinstance(spec, dict) or not isinstance(spec.get("id"), str):
            raise ValueError("trial needs a registered id")
        pack = self.packs.get(spec["id"])
        if pack is None:
            raise ValueError("unsupported trial id")
        return pack, pack.parse(spec)

    def describe(self):
        return [{"id": p.id, "version": p.version} for p in self.packs.values()]


def builtins() -> PackRegistry:
    from proofbeak.packs.calendar import CalendarPack
    from proofbeak.packs.storage import StoragePack

    return PackRegistry([CalendarPack(), StoragePack()])
