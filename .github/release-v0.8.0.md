## Acceptance tests you define, evidence you can inspect

Proofbeak 0.8.0 adds a self-hosted browser workspace to the same CLI, REST and MCP execution service. Start locally, define the behavior your application requires, preview without provider work, run against configured services and inspect independently observed outcomes.

```bash
python -m pip install 'proofbeak[agents,connectors]==0.8.0'
proofbeak studio
```

Python 3.11+ on Linux/macOS. The launcher prepares private local configuration, actual SQLite/filesystem candidates and a signing identity. No cloud account, embedded model or JavaScript runtime is required.

### Included

- Schema-guided visual/JSON authoring, explicit expected results, local service configuration and selected OpenAPI import.
- Bounded suites and parameter matrices with durable progress, cancellation and safe same-key retrieval after a lost submission response.
- Compatible historical baseline comparisons with per-condition regressions, improvements and changed observations.
- Conservative CI gates and JSON/HTML/JUnit/Markdown exports; incomplete evidence and unresolved cleanup do not pass.
- Responsive authenticated web console with memory-only drafts, HttpOnly sessions and CSRF/same-origin checks.
- Independently installed trusted connector packages with scaffolding, provenance and actual worker qualification.
- Portable Ed25519-signed evidence and offline verification against an independently trusted public key. Authenticity, acceptance and freshness are separate.
- Twenty-two MCP tools, including suites, comparisons, guided drafts/previews and signed exports.

### Artifacts and qualification

The attached Python distributions and Linux amd64 container archive are the original tested bytes. SHA256SUMS and release-manifest.json identify their exact source commit and checksums. Public PyPI installation verification is attached; it exercises real trials, suites, exports, comparisons, signing and Studio initialization outside the checkout.

Qualification includes actual Chromium desktop/mobile workflows with SQLite workers, safe submission retry and tamper rejection; installed wheels on Python 3.11–3.14; real provider/REST/MCP/connector checks; rootless containers and Kubernetes/network policies. Source and qualification links are recorded in the release manifest.

The deployment scope remains one coordinator with SQLite state. Remote SSO, multi-DC and a shared public evidence catalog are outside this release. Automated synthetic checks do not establish human adoption, an accessibility audit or external-agent decision-quality gains. A valid signature does not certify provider behavior.

[Workspace guide](https://github.com/eturkok/proofbeak/blob/v0.8.0/docs/workspace.md) · [Suite and CI guide](https://github.com/eturkok/proofbeak/blob/v0.8.0/docs/suites.md) · [Connector SDK](https://github.com/eturkok/proofbeak/blob/v0.8.0/docs/connector-sdk.md) · [Signed evidence](https://github.com/eturkok/proofbeak/blob/v0.8.0/docs/signed-evidence.md) · [Full changelog](https://github.com/eturkok/proofbeak/blob/v0.8.0/CHANGELOG.md)
