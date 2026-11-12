"""Calendar acceptance package: schema, fixture, normalization, and assertions."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from proofbeak.models import canonical, integer, keys
from proofbeak.observer import evaluate

@dataclass(frozen=True)
class Trial:
    id: str
    start: datetime
    timezone: str
    count: int
    duration_minutes: int
    index: int
    shift_minutes: int

    @classmethod
    def parse(cls, data: dict) -> Trial:
        keys(data, {"id", "fixture", "action", "expect"}, "trial")
        if data["id"] != "calendar.move_one.v1":
            raise ValueError("unsupported trial id")
        fixture = keys(data["fixture"], {
            "series", "count", "timezone", "start", "duration_minutes",
        }, "fixture")
        if fixture["series"] != "weekly":
            raise ValueError("only weekly recurrence is supported")
        if not isinstance(fixture["timezone"], str):
            raise ValueError("timezone must be an IANA timezone")
        try:
            zone = ZoneInfo(fixture["timezone"])
            start = datetime.fromisoformat(fixture["start"])
        except (ZoneInfoNotFoundError, TypeError, ValueError) as exc:
            raise ValueError("start and timezone must be explicit and valid") from exc
        if start.tzinfo is None or start.utcoffset() != start.astimezone(zone).utcoffset():
            raise ValueError("start must have an offset matching the named timezone")
        if start.second or start.microsecond:
            raise ValueError("start must have minute precision")
        count = integer(fixture["count"], 2, 12, "count")
        duration = integer(fixture["duration_minutes"], 1, 1440, "duration_minutes")
        action = keys(data["action"], {"op", "index", "shift_minutes"}, "action")
        if action["op"] != "move_instance":
            raise ValueError("unsupported operation")
        index = integer(action["index"], 1, count, "index")
        shift = integer(action["shift_minutes"], -1440, 1440, "shift_minutes")
        if shift == 0:
            raise ValueError("shift_minutes must be nonzero")
        trial = cls(data["id"], start.astimezone(zone), zone.key, count, duration, index, shift)
        expected = [{"field": field, "eq": value} for field, value in trial.expected.items()]
        if canonical(data["expect"]) != canonical(expected):
            raise ValueError("expect must match the four ordered move-one acceptance conditions")
        return trial

    @property
    def expected(self) -> dict:
        return {
            "instance_count": self.count,
            "shifted_instances": [self.index],
            "moved_minutes": self.shift_minutes,
            "duration_unchanged_for_all": True,
        }

    def fixture(self, series_id: str) -> list[Occurrence]:
        result = []
        for i in range(self.count):
            start = self.start + timedelta(weeks=i)
            end = start + timedelta(minutes=self.duration_minutes)
            values = [start, end]
            if i == self.index - 1:
                values += [start + timedelta(minutes=self.shift_minutes),
                           end + timedelta(minutes=self.shift_minutes)]
            for value in values:
                if value.astimezone(UTC).astimezone(value.tzinfo) != value:
                    raise ValueError("fixture contains a nonexistent local time")
                if value.replace(fold=0).utcoffset() != value.replace(fold=1).utcoffset():
                    raise ValueError("v1 does not support ambiguous local times")
            result.append(Occurrence(series_id, start, start, end))
        return result


@dataclass(frozen=True)
class Occurrence:
    series_id: str
    original_start: datetime
    start: datetime
    end: datetime

    def __post_init__(self):
        for value in (self.original_start, self.start, self.end):
            if not isinstance(value, datetime) or value.tzinfo is None:
                raise ValueError("observations need timezone-aware datetimes")
        if self.end.astimezone(UTC) <= self.start.astimezone(UTC):
            raise ValueError("observation has a non-positive duration")

    @property
    def identity(self) -> tuple[str, datetime]:
        return self.series_id, self.original_start

    def to_dict(self) -> dict:
        return {key: value.isoformat() if isinstance(value, datetime) else value
                for key, value in asdict(self).items()}


@dataclass(frozen=True)
class Snapshot:
    events: list[Occurrence]
    source: str
    observed_at: str

    def to_dict(self) -> dict:
        return {"events": [e.to_dict() for e in self.events], "source": self.source,
                "observed_at": self.observed_at}


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
    return evaluate({**trial.expected, "instance_identities_preserved": True}, metrics,
                    after.source, after.observed_at)


class CalendarPack:
    id = "calendar.move_one.v1"
    version = "0.2.0"

    def parse(self, spec):
        trial = Trial.parse(spec)
        trial.fixture("validation")
        return trial

    def valid_baseline(self, trial, before):
        return isinstance(before, Snapshot) and bool(before.events) and Counter(before.events) == Counter(
            trial.fixture(before.events[0].series_id))

    def assess(self, trial, before, after):
        if not isinstance(after, Snapshot):
            return {"verdict": "inconclusive", "reason_code": "invalid_observation", "checks": []}
        return assess(trial, before, after)
