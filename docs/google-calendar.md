# Google Calendar adapter and live access experiment

The Google adapter implements `calendar.move_one.v1` using the same package and common
worker as Radicale. Its HTTP protocol and fault behavior have automated simulated tests.
**A real Google account trial has not been run yet.** No token was requested or read while
building the adapter, and core CI never invokes Google with live credentials.

## Dedicated account and authorization

Use a separate disposable test account and user OAuth credentials from an operator-controlled
Google Cloud project with the Calendar API enabled. The adapter deliberately has no browser
login, token persistence, refresh-token flow, or service-account delegation. Inject a
short-lived access token into the process environment. Keep the same OAuth client for
creation and recovery when using app-created-calendar scopes.

The intended scopes are:

- `https://www.googleapis.com/auth/calendar.app.created` for secondary calendars created by
  this app and their events.
- `https://www.googleapis.com/auth/calendar.calendarlist.readonly` for bounded discovery of
  owned test calendars and the initial account-identity check.

The actual scope grants and account policies must be validated by the live experiment.
The metadata-only preflight does not prove write access. The adapter does not broaden OAuth
permissions or acquire credentials on the operator's behalf.

Configure `PROOFBEAK_GOOGLE_TEST_ACCOUNT` and `PROOFBEAK_GOOGLE_ACCESS_TOKEN` locally.
`.env.example` documents the configuration names; the CLI reads the environment and does
not load that file automatically. Never commit credential files.

```bash
PYTHONPATH=src python3 -m proofbeak google-preflight
```

This makes a GET for the primary calendar-list entry's identity and owner flag. It never
reads primary-calendar events or creates a calendar. The primary ID must match the
configured dedicated account. Only its fingerprint appears in the result.

## Synthetic live trial

With a valid token in the environment, explicitly run:

```bash
PROOFBEAK_RUN_GOOGLE_LIVE=1 PYTHONPATH=src \
  python3 tests/test_google_integration.py -v
```

The live test creates one synthetic secondary calendar, writes four weekly occurrences,
reads the baseline, moves only the second instance, rereads all expanded events, and
attempts to delete and verify removal of the calendar. It saves JSON and HTML reports in
`reports/google-live.*`. The durable ledger is `.proofbeak/google-live.sqlite3`; a failed
assertion or failed cleanup does not discard resource-recovery intent.

For a Google/Radicale comparison, configure both dedicated providers and run the existing
calendar JSON with `--candidate radicale --candidate google-calendar`. Neither provider
is preferred if both meet the conditions. A cross-provider result is not claimed before
this actual comparison is run.

## Resource isolation and interruption

Every calendar has an exact summary and description marker derived from its recorded run
UUID. Before writes and deletion the adapter verifies the configured account, owner role,
marker, and absence of a primary flag. Primary IDs are rejected. Event insertion uses a
stable run-scoped ID, contains no attendees, and disables notifications/default reminders.
Single-instance writes carry the event's ETag in `If-Match`.

Calendar creation does not accept a client-chosen ID. Therefore the persisted resource handle
contains a unique marker that a replacement worker can discover through the owner's calendar
list after a lost creation response. Discovery is bounded and must identify exactly one
owned, non-primary calendar. The adapter never retries a calendar creation automatically.

If a write outcome is ambiguous, the common worker reports inconclusive and attempts a
separate read when a baseline exists. Failed cleanup is quarantined. An absent discovery
result, duplicate marker, changed ownership, missing permissions, or expired token does not
justify deleting another calendar or reporting successful cleanup. For pending live-test
resources, inject a valid token for the same test account and OAuth client, then use:

```bash
PYTHONPATH=src python3 -m proofbeak cleanup \
  --candidate google-calendar --db .proofbeak/google-live.sqlite3
```

The operator must inspect unresolved markers if their state cannot be established. Tokens
and raw account identity are not placed in the evidence or SQLite ledger. The report stores
normalized synthetic event fields rather than complete Google response bodies, which may
contain creator, organizer, or other unnecessary account metadata.

## Technical references

- [Calendar and calendar-list behavior](https://developers.google.com/workspace/calendar/api/concepts/events-calendars)
- [Create a secondary calendar and accepted scopes](https://developers.google.com/workspace/calendar/api/v3/reference/calendars/insert)
- [List calendars and accepted scopes](https://developers.google.com/workspace/calendar/api/v3/reference/calendarList/list)
- [Recurring instances and original start identity](https://developers.google.com/workspace/calendar/api/guides/recurringevents)
- [Conditional updates with ETags](https://developers.google.com/workspace/calendar/api/guides/version-resources)
