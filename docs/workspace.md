# Work in the browser

Proofbeak's self-hosted workspace uses the same tenant-bound service, queue and evidence
as CLI/REST/MCP. No model, cloud account, JavaScript build tool or external asset service
is required. The browser interface is served by the Python package.

## Start locally

Install the `agents` and `connectors` extras in a virtual environment, then run:

```bash
python -m pip install 'proofbeak[agents,connectors]==0.8.1'
proofbeak studio
```

The launcher listens only on `127.0.0.1:8765`, opens `/ui/`, and prints your local access key.
Paste that key into the sign-in form. It prepares actual disposable filesystem and SQLite
candidates, a private local configuration and an Ed25519 signing identity. Nothing is sent
to another service until you configure and explicitly run against that service.

Use `--no-browser`, `--port 8766` or `--directory .proofbeak/another-workspace` when needed.
Existing configuration and identities are preserved. The local access key is stored in an
owner-only `access-key` file under the private studio directory; keep this operator credential
private. The launcher can display it again on restart. For an existing operator configuration,
`proofbeak-server serve --config ...` exposes the same console at `/ui/`, with that deployment's
allowed providers. Standard tenant access does not gain provider-configuration privileges.

## Your first test package

1. Choose **Try a local example**. Its prepared case writes one value, observes it independently
   and checks the expected result against actual SQLite. No provider account is necessary.
2. Change the package/case name and synthetic data. The visual editor exposes configured
   actions, independent observers and caller-owned conditions. JSON mode edits the same definition.
3. Select **Try multiple values** beside a fixture value to parameterize it. Edit the resulting
   matrix below; the bounded expansion is shown before execution.
4. Choose **Validate & preview**. Diagnostics point to invalid definitions and incompatible
   candidates. A valid preview lists each expanded execution and its logical operations;
   validation never creates a job or provider resource.
5. Choose **Run test package**. Progress, measured outcomes, expected/observed values and cleanup
   appear together. Open **Inspect evidence** for the independent before/after observations.

Drafts stay in memory while navigating. **Save draft** explicitly downloads JSON; **Import JSON**
restores a suite. The browser does not silently persist fixture data. Unsaved work prompts before
leaving the page. If a submission response is lost, **Retry submission** preserves the exact
payload and idempotency key instead of creating new work. History reopens results without replay.

## Configure your own service

The local-owner Studio lets you save a new service profile through **Configure a service**.
Start from SQLite, PostgreSQL, Redis or a REST profile, or paste an operator-approved MCP,
search/model or installed-plugin profile. It validates configuration without claiming live
availability. Missing environment credentials show the service as needing setup. Existing aliases
are never silently replaced; use a new alias or the ordinary offline operator configuration flow.

OpenAPI import accepts explicitly selected named operations from a local OpenAPI 3 JSON file.
Review response projections, credentials, scope ownership and cleanup before configuring writes.
For ordinary server deployments, **Download profile** produces a provider map for the operator
to import; a tenant cannot change the server's provider allowlist or executable bindings.

## Compare and share

**Compare releases** applies the [historical comparison contract](suites.md). Matching cases
show regressions, improvements and changed observations. Incompatible contexts remain explicit.
The optional version allowance applies only to the declared provider version.

**Export results** provides JSON, HTML, JUnit/CI summaries and, when the operator configured
an identity, a [signed raw-evidence bundle](signed-evidence.md). Choose **Verify evidence** to
upload a bundle and an independently obtained public key, or explicitly use the authenticated
workspace's public key. Authenticity, acceptance and freshness are shown separately.
CLI verification remains available offline; browser verification runs in this service.

## Browser sessions and deployment

The sign-in key is exchanged for an eight-hour memory-only server session in an HttpOnly,
SameSite=Strict cookie. API keys do not enter URLs, localStorage or sessionStorage. Cookie-based
writes require a session-bound CSRF header and same-origin checks; existing bearer clients keep
their API contract. Reloading preserves an active session; logout, expiry, disabled credentials
or a server restart require sign-in again. Cookies are Secure when served over HTTPS.

Use HTTPS for remote deployment and configure reverse-proxy trust for the actual service origin.
The console uses no third-party scripts/fonts and sends no telemetry. The deployment remains
a controlled single coordinator. This browser session is not remote SSO or an additional
authorization role. Responsive layouts, keyboard controls, labeled inputs, visible focus and
textual result states support desktop and mobile use.

## Draft through CLI or MCP

`proofbeak draft` takes configured candidates, an explicitly read-only observer, JSON arguments,
an observed field path and your expected JSON value. Optional actions are explicit operation/args
JSON objects. It writes a validated scenario without contacting a provider. MCP adds
`draft_trial` and `preview_suite` to the existing discovery/validation/execution flow. These are
schema-guided helpers; business acceptance conditions remain supplied by the caller.
