"""Small second domain to exercise the same execution and evidence core."""

import hashlib
import re
from dataclasses import asdict, dataclass

from proofbeak.models import canonical, keys
from proofbeak.observer import evaluate


def content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


@dataclass(frozen=True)
class StorageTrial:
    id: str
    key: str
    content: str

    @property
    def expected(self):
        return {"object_count": 1, "keys_unchanged": True, "content_unchanged": True}


@dataclass(frozen=True)
class StorageSnapshot:
    objects: dict[str, str]
    source: str
    observed_at: str

    def to_dict(self):
        return asdict(self)


class StoragePack:
    id = "storage.put_same_key.v1"
    version = "0.1.0"
    description = "Repeat a write to the same key; preserve one object and the same content."

    def example(self):
        return {"id": self.id,
                "fixture": {"key": "probe.txt", "content": "Proofbeak synthetic acceptance trial"},
                "action": {"op": "put_same_key"},
                "expect": [{"field": "object_count", "eq": 1},
                           {"field": "keys_unchanged", "eq": True},
                           {"field": "content_unchanged", "eq": True}]}

    def parse(self, spec):
        keys(spec, {"id", "fixture", "action", "expect"}, "trial")
        if spec["id"] != self.id:
            raise ValueError("unsupported trial id")
        fixture = keys(spec["fixture"], {"key", "content"}, "fixture")
        if not isinstance(fixture["key"], str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", fixture["key"]
        ):
            raise ValueError("object key must be a simple filename with at most 64 characters")
        if not isinstance(fixture["content"], str) or not 1 <= len(fixture["content"].encode()) <= 4096:
            raise ValueError("content must contain 1 to 4096 UTF-8 bytes of synthetic text")
        if spec["action"] != {"op": "put_same_key"}:
            raise ValueError("unsupported operation")
        trial = StorageTrial(self.id, fixture["key"], fixture["content"])
        if canonical(spec["expect"]) != canonical([
            {"field": field, "eq": value} for field, value in trial.expected.items()
        ]):
            raise ValueError("expect must match the three put-same-key acceptance conditions")
        return trial

    def valid_baseline(self, trial, before):
        return isinstance(before, StorageSnapshot) and before.objects == {
            trial.key: content_hash(trial.content.encode())}

    def assess(self, trial, before, after):
        if not isinstance(after, StorageSnapshot):
            return {"verdict": "inconclusive", "reason_code": "invalid_observation", "checks": []}
        metrics = {"object_count": len(after.objects),
                   "keys_unchanged": set(after.objects) == set(before.objects),
                   "content_unchanged": after.objects.get(trial.key) == before.objects[trial.key]}
        return evaluate(trial.expected, metrics, after.source, after.observed_at)
