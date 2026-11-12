"""Google Calendar trial adapter; only generated, owned secondary calendars are mutable."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import quote, urlencode

from proofbeak.adapters.base import ProviderError, Resource
from proofbeak.adapters.http import Transport
from proofbeak.models import fingerprint
from proofbeak.packs.calendar import Occurrence, Snapshot

ORIGIN = "https://www.googleapis.com/calendar/v3"
ENTRY_FIELDS = "id,summary,description,primary,accessRole"


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value["dateTime"])
        if parsed.tzinfo is None:
            raise ValueError("missing offset")
        return parsed
    except (KeyError, TypeError, ValueError):
        raise ProviderError("invalid_observation") from None


class GoogleCalendarAdapter:
    name = "google-calendar"
    version = "0.1.0"
    evidence_level = "provider_test_account"
    supported_trials = frozenset({"calendar.move_one.v1"})

    def __init__(self, access_token, test_account, transport=None):
        if not isinstance(access_token, str) or not 1 <= len(access_token) <= 8192 \
                or not re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", access_token):
            raise ValueError("inject a valid short-lived Google access token")
        if not isinstance(test_account, str) or not re.fullmatch(r"[^\s@]+@[^\s@]+", test_account):
            raise ValueError("configure the dedicated Google test-account email")
        self.account = test_account.lower()
        self.headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
        self.transport = transport or Transport()
        if transport is not None:
            self.evidence_level = "simulation"
        self.profile = {"endpoint": ORIGIN, "account_scope": fingerprint(self.account),
                        "provider_version": "unknown", "permission_profile": "app_created_calendars",
                        "transport": "injected" if transport is not None else "https"}
        self.verified = False
        self.calendars = {}
        self.checked = set()
        self.attempted = set()

    def resource(self, run_id):
        if not re.fullmatch(r"[a-f0-9]{32}", run_id):
            raise ValueError("invalid run id")
        return Resource(f"proofbeak:{run_id}", run_id)

    def _scope(self, resource):
        if resource != self.resource(resource.scope_id):
            raise ProviderError("resource_scope_violation")

    def _request(self, method, path, *, payload=None, params=None, headers=None, allowed=(200,)):
        url = ORIGIN + path + ("?" + urlencode(params) if params else "")
        response = self.transport.request(
            method, url, body=json.dumps(payload).encode() if payload is not None else None,
            headers={**self.headers, **(headers or {})}, allowed=allowed)
        if not response.body:
            return response.status, {}
        try:
            data = json.loads(response.body)
            if not isinstance(data, dict):
                raise ValueError("expected object")
        except (ValueError, UnicodeDecodeError):
            raise ProviderError("invalid_provider_response") from None
        return response.status, data

    def preflight(self):
        # Read identity metadata only; primary-calendar events are never queried or mutated.
        _, entry = self._request("GET", "/users/me/calendarList/primary",
                                 params={"fields": "id,primary,accessRole"})
        if not isinstance(entry.get("id"), str) or entry["id"].lower() != self.account \
                or entry.get("primary") is not True \
                or entry.get("accessRole") != "owner":
            raise ProviderError("test_account_mismatch")
        self.verified = True
        return {"account_verified": True, "account_scope": self.profile["account_scope"],
                "write_access_verified": False}

    @staticmethod
    def _marker(resource):
        return {"summary": f"Proofbeak disposable {resource.scope_id}",
                "description": f"proofbeak:owned-trial:{resource.scope_id}"}

    def _owned_entry(self, resource, entry):
        marker = self._marker(resource)
        return all(entry.get(k) == v for k, v in marker.items()) and \
            entry.get("accessRole") == "owner" and entry.get("primary", False) is False and \
            isinstance(entry.get("id"), str) and bool(entry["id"]) and \
            entry["id"].lower() not in {"primary", self.account}

    def _pages(self, path, params):
        items, token, seen = [], None, set()
        for _ in range(4):
            query = {**params, **({"pageToken": token} if token else {})}
            _, data = self._request("GET", path, params=query)
            page = data.get("items", [])
            if not isinstance(page, list) or any(not isinstance(item, dict) for item in page):
                raise ProviderError("invalid_provider_response")
            items.extend(page)
            if len(items) > 1000:
                raise ProviderError("observation_budget_exceeded")
            token = data.get("nextPageToken")
            if not token:
                return items
            if not isinstance(token, str) or token in seen:
                raise ProviderError("invalid_pagination")
            seen.add(token)
        raise ProviderError("observation_budget_exceeded")

    def _discover(self, resource):
        entries = self._pages("/users/me/calendarList", {
            "minAccessRole": "owner", "maxResults": 250, "showHidden": "true",
            "fields": f"items({ENTRY_FIELDS}),nextPageToken"})
        matches = [entry for entry in entries if self._owned_entry(resource, entry)]
        if len(matches) > 1:
            raise ProviderError("resource_ownership_ambiguous")
        return matches[0]["id"] if matches else None

    def _calendar(self, resource):
        self._scope(resource)
        if not self.verified:
            self.preflight()
        calendar_id = self.calendars.get(resource.scope_id) or self._discover(resource)
        if not calendar_id:
            raise ProviderError("resource_not_observed")
        _, entry = self._request("GET", "/users/me/calendarList/" + quote(calendar_id, safe=""),
                                 params={"fields": ENTRY_FIELDS})
        if not self._owned_entry(resource, entry) or entry["id"] != calendar_id:
            raise ProviderError("resource_scope_violation")
        self.calendars[resource.scope_id] = calendar_id
        return calendar_id

    def provision(self, resource, trial):
        self._scope(resource)
        self.checked.add(resource.scope_id)
        if not self.verified:
            self.preflight()
        if self._discover(resource):
            raise ProviderError("resource_conflict")
        self.attempted.add(resource.scope_id)
        _, calendar = self._request("POST", "/calendars", payload={
            **self._marker(resource), "timeZone": trial.timezone}, allowed=(200, 201))
        calendar_id = calendar.get("id")
        if not isinstance(calendar_id, str) or not calendar_id \
                or calendar_id.lower() in {"primary", self.account}:
            raise ProviderError("invalid_provider_response")
        self.calendars[resource.scope_id] = calendar_id
        self._calendar(resource)

    def seed(self, resource, trial):
        calendar_id = self._calendar(resource)
        self._request("POST", f"/calendars/{quote(calendar_id, safe='')}/events", payload={
            "id": resource.scope_id, "summary": "Proofbeak synthetic trial",
            "start": {"dateTime": trial.start.isoformat(), "timeZone": trial.timezone},
            "end": {"dateTime": (trial.start + timedelta(minutes=trial.duration_minutes)).isoformat(),
                    "timeZone": trial.timezone},
            "recurrence": [f"RRULE:FREQ=WEEKLY;COUNT={trial.count}"],
            "reminders": {"useDefault": False}, "transparency": "transparent",
        }, params={"sendUpdates": "none"}, allowed=(200, 201))

    def _events(self, resource):
        calendar_id = self._calendar(resource)
        path = f"/calendars/{quote(calendar_id, safe='')}/events"
        items = self._pages(path, {"singleEvents": "true", "showDeleted": "false", "maxResults": 100,
                                  "fields": "items(id,recurringEventId,originalStartTime,start,end,status),nextPageToken"})
        if len(items) > 100:
            raise ProviderError("observation_budget_exceeded")
        return calendar_id, items

    def read(self, resource, trial):
        _, items = self._events(resource)
        events = []
        for item in items:
            if item.get("status") == "cancelled":
                continue
            series_id = item.get("recurringEventId") or item.get("id")
            if not isinstance(series_id, str) or not series_id:
                raise ProviderError("invalid_observation")
            start = timestamp(item.get("start"))
            try:
                events.append(Occurrence(series_id, timestamp(item.get("originalStartTime", item.get("start"))),
                                         start, timestamp(item.get("end"))))
            except ValueError:
                raise ProviderError("invalid_observation") from None
        return Snapshot(events, "google-calendar:events.list/singleEvents", datetime.now(UTC).isoformat())

    def apply(self, resource, trial):
        calendar_id, items = self._events(resource)
        original = trial.fixture(resource.scope_id)[trial.index - 1].original_start
        matches = [item for item in items if item.get("recurringEventId") == resource.scope_id
                   and timestamp(item.get("originalStartTime")) == original]
        if len(matches) != 1 or not isinstance(matches[0].get("id"), str):
            raise ProviderError("target_instance_ambiguous")
        path = f"/calendars/{quote(calendar_id, safe='')}/events/{quote(matches[0]['id'], safe='')}"
        _, event = self._request("GET", path)
        if event.get("recurringEventId") != resource.scope_id \
                or timestamp(event.get("originalStartTime")) != original or event.get("attendees"):
            raise ProviderError("resource_scope_violation")
        etag = event.get("etag")
        if not isinstance(etag, str) or not etag:
            raise ProviderError("missing_concurrency_token")
        delta = timedelta(minutes=trial.shift_minutes)
        payload = {"summary": "Proofbeak synthetic trial", "reminders": {"useDefault": False},
                   "transparency": "transparent",
                   "start": {"dateTime": (timestamp(event.get("start")) + delta).isoformat(),
                             "timeZone": trial.timezone},
                   "end": {"dateTime": (timestamp(event.get("end")) + delta).isoformat(),
                           "timeZone": trial.timezone}}
        self._request("PUT", path, payload=payload, params={"sendUpdates": "none"},
                      headers={"If-Match": etag})

    def cleanup(self, resource):
        self._scope(resource)
        if resource.scope_id in self.checked and resource.scope_id not in self.attempted:
            return
        self.transport.calls = 0
        calendar_id = self._calendar(resource)
        path = "/calendars/" + quote(calendar_id, safe="")
        self._request("DELETE", path, allowed=(200, 204, 404, 410))
        status, _ = self._request("GET", path, params={"fields": "id"}, allowed=(200, 404, 410))
        if status == 200:
            raise ProviderError("cleanup_unconfirmed")
        self.calendars.pop(resource.scope_id, None)
