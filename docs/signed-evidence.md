# Portable signed evidence

Export a `proofbeak.bundle.v1` JSON document containing the complete report, scenarios,
provider/connector versions, before/after observations, their hashes and an Ed25519 signature.
The signature covers the format and canonical payload together. There are no archive paths,
external document references or provider calls during verification.

Install `proofbeak[signing]` for standalone CLI signing. The `agents` extra and standard service
image already include the signing dependency. Generate an operator-owned identity explicitly:

```bash
proofbeak keys generate --private .proofbeak/signing/private.json --public signing-public.json
proofbeak bundle create reports/suite.json --key .proofbeak/signing/private.json --output reports/suite.bundle.json
proofbeak bundle verify reports/suite.bundle.json --public-key signing-public.json
```

Key generation refuses existing paths. Private keys must be regular owner-only files (0600).
Keep the private key outside source control, reports and shared exports. Back up and rotate it
through your operator process; the service's database snapshots do not manage signing identities.
Verification requires an independently obtained public key; an embedded key cannot authorize
itself. Distribute the public key/fingerprint through an authenticated channel you trust.

For the REST service, set `PROOFBEAK_SIGNING_KEY_FILE` to the private key file before starting
`proofbeak-server serve`. `GET /signing` returns only configuration status and the public key.
Authenticated `POST /runs/{id}/bundle` and `POST /suites/{id}/bundle` export tenant-owned raw
records. Missing keys, purged observations or integrity failures refuse the export. Plain
JSON/HTML trial exports remain available at `GET /runs/{id}/export?format=json|html`; suites
also export JUnit and Markdown as described in [suites](suites.md).
`POST /bundles/verify` accepts `bundle` and an explicitly chosen `public_key` document, with
a bounded 16 MiB body. Ordinary execution requests remain capped at 64 KiB. CLI verification
works offline; REST verification does not call the provider or a public key service.

MCP exposes `get_signing_key` and `export_evidence_bundle` for explicit agent workflows.
The bundle includes supplied test data and observations: choose its recipients deliberately.
No credentials are added by the export. A caller can still put sensitive application data
in its fixtures or receive it from a provider; exporting raw evidence intentionally includes it.

## What verification establishes

Successful verification authenticates the record to the pinned key and detects changed
content. It separately reports acceptance, expiry, simulation and revocation state recorded
at export. Offline verification cannot determine later revocations, unchanged provider behavior,
signer honesty or reviewer independence. The timestamp is signer-supplied, not trusted time.
A valid signature can cover a failing or inconclusive trial; it never certifies the provider.
Exit 0 from `bundle verify` means valid signature/content, not a passing acceptance requirement.
Use the separately reported acceptance gate and freshness information for your decision.
