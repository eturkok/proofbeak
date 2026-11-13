# Publish a verified distribution

The [release workflow](../.github/workflows/release.yml) builds a wheel and source archive,
checks their metadata/inventory, runs clean core and actual REST/MCP installations on four
Python versions, and builds/tests a rootless container from that wheel. PR and ordinary main
pushes produce downloadable artifacts. A version increase on main publishes them after checks.

The package version is 0.6.0. A registry release has not happened merely because these files
or a main-branch commit exist. Check the publishing jobs and registry before announcing it.

## One-time PyPI account setup

Sign in to your own PyPI account and open:

<https://pypi.org/manage/account/publishing/>

Create a pending GitHub Trusted Publisher with these exact fields:

| Field | Value |
| --- | --- |
| PyPI project name | `proofbeak` |
| GitHub owner | `eturkok` |
| GitHub repository | `proofbeak` |
| Workflow filename | `release.yml` |
| Environment name | `pypi` |

For an existing project you own, configure the same publisher under that project's Publishing
page instead. A pending publisher does not reserve the project name. The workflow records
the current name status in `pypi-status.json`; resolve an ownership/name conflict before
publication. Configuring PyPI trust requires access to that account; repository edits cannot
grant it. No long-lived PyPI API token is required by this workflow.

PyPI's primary instructions: [new project](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)
and [existing project](https://docs.pypi.org/trusted-publishers/adding-a-publisher/).

## Start the publication

Merging an increased version into `main` starts publication automatically after checks.
For a deliberate retry or a separately selected target, open **Actions → Distribution and publishing → Run workflow**.
Choose `main`, enter the exact version `0.6.0`, and enable `publish_pypi`. Enable
`publish_image` as well to push the tested image to GHCR. The flags default to false.

The workflow rebuilds and rechecks the selected source before publishing. Publication jobs
depend on every installation and container job; they download the already tested artifacts
rather than rebuilding after checks. The PyPI job verifies the distribution hashes and uses
OIDC through `pypa/gh-action-pypi-publish`; the image job verifies and loads the tested archive.
Publication starts on a version increase merged into `main`, or an explicit dispatch
from `main` with the matching version. PRs and main commits without a version change do not publish.

The PyPI job uses the `pypi` GitHub environment. Any restrictions already configured there
still apply. GHCR publication uses the workflow's scoped `GITHUB_TOKEN`, not a personal token.
Version and commit tags are published without a moving `latest` alias. A different existing
versioned image is refused; Python versions already published cannot be replaced.

A separate `verify-pypi` job checks the public version metadata against every tested file
digest, installs the exact version from PyPI in a new venv, and runs a real filesystem trial.
If only this verification fails, rerun that job rather than uploading the version again.
The image job also pulls its published tag and checks it against the tested image identity.

If only a publisher job fails (for example, trust was not configured), correct the external
setting and rerun that failed job with its original immutable artifacts. Do not silently
rebuild different bytes under a version already partly or fully published.

## Verify before announcing

- Confirm successful publisher jobs, not just successful builds.
- Inspect <https://pypi.org/project/proofbeak/0.6.0/> and install that exact release into a new venv.
- Compare downloaded wheel/source hashes with the workflow's `manifest.json`.
- Pull the versioned image, record its registry digest and run the deployment smoke checks.

Publishing is separate from changing repository visibility, enabling remote SSO or operating
a hosted service. The current single-coordinator deployment scope remains unchanged.
The repository is currently private. Publishing the wheel/source archive does not make the
GitHub repository or its issue tracker public; GitHub documentation links still require
repository access. The source archive includes the documentation for offline reading.
