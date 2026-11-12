# Google Calendar access experiment

Google integration is the next provider increment. It is not implemented or verified in
the initial CLI. No Google credentials have been requested, read, or stored by this project.

Use a separate synthetic test account. The experiment must establish that the account can:

1. Create a disposable secondary calendar without touching `primary`.
2. Insert the four-week fixture without attendees or invitations.
3. Read all instances and retain `recurringEventId` plus `originalStartTime` for identity.
4. Update the second instance and independently list/read all four instances again.
5. Delete the disposable calendar and verify its removal.

The eventual adapter must keep access tokens in worker memory or injected secrets. Token
refresh is a provider concern, not part of the trial JSON. Never commit token files or put
tokens in report metadata. Record the service profile and authorized scope without secrets.

The implementation should use the minimum permissions that support creation, observation,
mutation, and deletion of a dedicated test calendar. Exact scope choice and account policy
will be verified in the access experiment; a generic calendar connection is not sufficient
evidence that the necessary operations are authorized.

Primary documentation:

- [Recurring events and single-instance changes](https://developers.google.com/workspace/calendar/api/guides/recurringevents)
- [Calendar API authorization](https://developers.google.com/workspace/calendar/api/auth)
- [Calendar creation](https://developers.google.com/workspace/calendar/api/v3/reference/calendars/insert)

The adapter must return `inconclusive` if it cannot observe the final state. A 200 response
from an update is not a substitute for the read path.
