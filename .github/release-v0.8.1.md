## Faster Studio workflows, reusable tests and operational visibility

![Proofbeak Studio](https://github.com/eturkok/proofbeak/releases/download/v0.8.1/studio-desktop.png)

Studio now uses parliament blue and orange throughout navigation, sign-in, editors, service forms, dialogs and mobile views. Dark blue text on orange actions meets 4.5:1 contrast, including hover. Recorded acceptance, evidence validity and cleanup remain distinct.

### What changed

- Search all retained history on the server, with execution status, recorded acceptance, service and UTC date filters. Pagination preserves filters; suite history ordering is indexed.
- Monitor active tests through lightweight metadata revisions. Hidden tabs pause monitoring, errors back off, and unchanged results do not redraw the workspace or reload full reports.
- Explicitly save and delete private reusable templates, copy retained definitions into new drafts, and name passing baseline references for release comparisons. Opening a definition performs no provider work.
- Find failing or unavailable conditions and cleanup issues first, use the attention-only view, and inspect existing lifecycle, CPU and HTTP-attempt measurements.
- Inspect tenant-scoped queue counts, oldest wait, unresolved cleanup, retained report size and recent sampled duration/timeout metrics in Operations.
- Use optional history filters through the existing MCP list tools. The CLI, REST, browser and external-agent execution/evidence contracts remain available.

### Install or upgrade

Python 3.11+ on Linux/macOS, in an activated virtual environment:

```bash
python -m pip install --upgrade 'proofbeak[agents,connectors]==0.8.1'
proofbeak studio
```

Restart a running Studio after upgrading. The published Linux amd64 image is `ghcr.io/eturkok/proofbeak:0.8.1`.

### Verified artifacts

The attached wheel, source archive and Linux amd64 container archive are the original tested bytes from commit `70e26352c75a83a289159007950d951eb93fda72`. They match the published PyPI files and qualified container. `release-manifest.json` and `SHA256SUMS` record identities and provenance.

All three source qualification workflows passed. Actual Chromium, Firefox and WebKit each passed eleven browser scenarios (33 total), including keyboard sign-in, durable templates/baselines, definition reuse, search beyond the first page and mobile layouts. Qualification also passed installed packages on Python 3.11–3.14, real provider/REST/MCP/SDK/signing checks, rootless containers, Kubernetes persistence and network policies. Public-index clean-install and browser qualification reports are attached.

The supported deployment remains one coordinator with SQLite state. Saved templates contain explicitly saved test data and remain until deletion or tenant purge; named baselines preserve evidence retention, freshness and comparison rules. Metrics are bounded samples. Automated qualification does not establish human adoption, a complete accessibility audit or production-load performance.

[Workspace guide](https://github.com/eturkok/proofbeak/blob/v0.8.1/docs/workspace.md) · [Changelog](https://github.com/eturkok/proofbeak/blob/v0.8.1/CHANGELOG.md) · [Changes since v0.8.0](https://github.com/eturkok/proofbeak/compare/v0.8.0...v0.8.1)
