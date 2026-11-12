"""Dedicated CalDAV collection; server-expanded REPORTs provide the observations."""

from __future__ import annotations

import base64
import copy
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from proofbeak.adapters.base import ProviderError, Resource
from proofbeak.adapters.http import Transport
from proofbeak.models import fingerprint
from proofbeak.packs.calendar import Occurrence, Snapshot, Trial


def icalendar_module():
    try:
        import icalendar
    except ImportError:
        raise ProviderError("calendar_dependency_missing") from None
    return icalendar


class RadicaleAdapter:
    name = "radicale"
    version = "0.2.0"
    supported_trials = frozenset({"calendar.move_one.v1"})
    evidence_level = "local_runner"

    def __init__(self, endpoint: str, username: str, password: str, transport=None):
        url = urlsplit(endpoint)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username \
                or url.password or url.query or url.fragment or url.path not in {"", "/"}:
            raise ValueError("Radicale endpoint must be an origin URL without credentials")
        if url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "::1", "radicale"}:
            raise ValueError("remote Radicale endpoints require HTTPS")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", username) or not password:
            raise ValueError("a simple dedicated test username and password are required")
        self.endpoint = endpoint.rstrip("/")
        self.username = username
        self._password = password
        self.transport = transport or Transport()
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        self.headers = {"Authorization": f"Basic {token}"}
        self.profile = {"endpoint": self.endpoint, "account_scope": fingerprint(username),
                        "provider_version": "unknown", "permissions": "dedicated_test_account"}

    def resource(self, run_id: str) -> Resource:
        if not re.fullmatch(r"[a-f0-9]{32}", run_id):
            raise ValueError("invalid run id")
        return Resource(f"{self.endpoint}/{self.username}/proofbeak-{run_id}/", run_id)

    def _request(self, resource, method, *, item=False, body=None, headers=None, allowed=None):
        # Every mutation is limited to the resource derived from this run id.
        if resource != self.resource(resource.scope_id):
            raise ProviderError("resource_scope_violation")
        url = resource.id + ("series.ics" if item else "")
        options = {"body": body, "headers": {**self.headers, **(headers or {})}}
        if allowed is not None:
            options["allowed"] = allowed
        return self.transport.request(method, url, **options)

    def provision(self, resource: Resource, trial: Trial) -> None:
        icalendar_module()
        xml = (b'<C:mkcalendar xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">'
               b'<D:set><D:prop><D:displayname>Proofbeak disposable trial</D:displayname>'
               b'</D:prop></D:set></C:mkcalendar>')
        self._request(resource, "MKCALENDAR", body=xml,
                      headers={"Content-Type": "application/xml"}, allowed=(201,))

    def seed(self, resource: Resource, trial: Trial) -> None:
        ical = icalendar_module()
        calendar = ical.Calendar()
        calendar.add("prodid", "-//Proofbeak//Calendar trial v1//EN")
        calendar.add("version", "2.0")
        calendar.add_component(ical.Timezone.from_tzinfo(
            trial.start.tzinfo, first_date=trial.start.date() - timedelta(days=366),
            last_date=trial.fixture(resource.scope_id)[-1].start.date() + timedelta(days=366)))
        event = ical.Event()
        event.add("uid", resource.scope_id)
        event.add("dtstamp", datetime.now(UTC))
        event.add("dtstart", trial.start)
        event.add("dtend", trial.start + timedelta(minutes=trial.duration_minutes))
        event.add("summary", "Proofbeak synthetic trial")
        event.add("rrule", {"freq": "weekly", "count": trial.count})
        calendar.add_component(event)
        self._request(resource, "PUT", item=True, body=calendar.to_ical(),
                      headers={"Content-Type": "text/calendar", "If-None-Match": "*"})

    def read(self, resource: Resource, trial: Trial) -> Snapshot:
        ical = icalendar_module()
        start = (trial.start - timedelta(days=2)).astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
        end = (trial.fixture(resource.scope_id)[-1].end + timedelta(days=2)).astimezone(
            UTC).strftime("%Y%m%dT%H%M%SZ")
        xml = (f'<C:calendar-query xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">'
               f'<D:prop><C:calendar-data><C:expand start="{start}" end="{end}"/>'
               '</C:calendar-data></D:prop><C:filter><C:comp-filter name="VCALENDAR">'
               '<C:comp-filter name="VEVENT"/></C:comp-filter></C:filter>'
               '</C:calendar-query>').encode()
        response = self._request(resource, "REPORT", body=xml,
                                 headers={"Content-Type": "application/xml", "Depth": "1"},
                                 allowed=(207,))
        events = []
        try:
            root = ET.fromstring(response.body)
            if root.tag != "{DAV:}multistatus":
                raise ValueError("not a multistatus")
            for item in root.findall("{DAV:}response"):
                status = item.findtext("{DAV:}status")
                if status and " 200 " not in status:
                    raise ValueError("unreadable calendar item")
                data_found = False
                for propstat in item.findall("{DAV:}propstat"):
                    if " 200 " not in propstat.findtext("{DAV:}status", ""):
                        raise ValueError("unreadable property")
                    for data in propstat.findall("{DAV:}prop/{urn:ietf:params:xml:ns:caldav}calendar-data"):
                        data_found = True
                        for event in ical.Calendar.from_ical(data.text).walk("VEVENT"):
                            if event.get("RRULE") or event.get("RDATE") or event.get("EXDATE"):
                                raise ValueError("server did not expand recurrence")
                            events.append(Occurrence(
                                str(event["UID"]), event.get("RECURRENCE-ID", event["DTSTART"]).dt,
                                event["DTSTART"].dt, event["DTEND"].dt))
                if not data_found:
                    raise ValueError("missing calendar data")
            if len(events) > 100:
                raise ValueError("too many events")
        except (ValueError, TypeError, KeyError, AttributeError, ET.ParseError):
            raise ProviderError("invalid_observation") from None
        return Snapshot(events, "radicale:REPORT/calendar-data/expand", datetime.now(UTC).isoformat())

    def apply(self, resource: Resource, trial: Trial) -> None:
        ical = icalendar_module()
        response = self._request(resource, "GET", item=True)
        etag = response.headers.get("etag")
        if not etag:
            raise ProviderError("missing_concurrency_token")
        try:
            calendar = ical.Calendar.from_ical(response.body)
            masters = [e for e in calendar.walk("VEVENT") if not e.get("RECURRENCE-ID")]
            if len(masters) != 1 or str(masters[0]["UID"]) != resource.scope_id:
                raise ValueError("unexpected master")
            exception = copy.deepcopy(masters[0])
            for field in ("RRULE", "RDATE", "EXDATE", "DTSTART", "DTEND", "DTSTAMP"):
                exception.pop(field, None)
            target = trial.fixture(resource.scope_id)[trial.index - 1]
            delta = timedelta(minutes=trial.shift_minutes)
            exception.add("recurrence-id", target.original_start)
            exception.add("dtstart", target.start + delta)
            exception.add("dtend", target.end + delta)
            exception.add("dtstamp", datetime.now(UTC))
            calendar.add_component(exception)
        except (ValueError, KeyError, TypeError):
            raise ProviderError("invalid_provider_response") from None
        self._request(resource, "PUT", item=True, body=calendar.to_ical(),
                      headers={"Content-Type": "text/calendar", "If-Match": etag})

    def cleanup(self, resource: Resource) -> None:
        # Cleanup uses a separate request budget so trial exhaustion cannot strand a resource.
        self.transport.calls = 0
        self._request(resource, "DELETE", allowed=(200, 204, 404))
