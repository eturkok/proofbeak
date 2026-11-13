# Install the Python package, service and MCP bridge

One Python distribution contains the CLI, REST service and MCP stdio bridge. The core has
no third-party Python dependencies. REST/MCP require the `agents` extra; calendar adapters
add the optional `calendar` extra. Linux/macOS and Python 3.11+ are supported.

## Install a tested build now

Until a PyPI release is confirmed, download the `python-distributions` artifact from a
successful [Distribution and publishing workflow](https://github.com/eturkok/proofbeak/actions/workflows/release.yml).
Extract it; `dist/` contains the wheel and source archive, and `manifest.json` records their
SHA-256 values. CI builds the wheel from the source distribution and tests that same wheel
outside the checkout on Python 3.11, 3.12, 3.13 and 3.14.

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --no-index --no-deps dist/proofbeak-0.6.0-py3-none-any.whl
proofbeak --version
proofbeak example --output scenario.json
proofbeak validate scenario.json --candidate scenario:filesystem --json
proofbeak run scenario.json --candidate scenario:filesystem --key first-trial \
  --output reports/trial.json --html reports/trial.html
```

Open `reports/trial.html`. Edit `scenario.json` for your own condition and choose a new key.
`example` creates a private file and refuses existing files or symlinks. It makes no provider
request. Templates come from the installed package; no source checkout is needed.

After publication to PyPI, the equivalent installation is:

```bash
python -m pip install proofbeak==0.6.0
```

The registry command is a publication target, not a claim that the version is already live.
The wheel can also be mirrored in an operator-owned package index.

## Run REST and connect an MCP host

Install the interface extra from the downloaded wheel:

```bash
python -m pip install 'dist/proofbeak-0.6.0-py3-none-any.whl[agents]'
```

Save this operator-owned map as `providers.json`:

```json
{"scenario:filesystem": {}}
```

Initialize a private configuration and start one coordinator:

```bash
proofbeak-server init --config server.json --state .proofbeak/service --providers providers.json
proofbeak-server check --config server.json --require-providers
proofbeak-server serve --config server.json --port 8765
```

Initialization prints the tenant API key once. Supply it through the MCP host's private
environment settings. Configure the host to start the installed `proofbeak-mcp` command
(use its absolute virtual-environment path), with:

| Environment variable | Value |
| --- | --- |
| `PROOFBEAK_API_URL` | `http://127.0.0.1:8765`, or the operator's HTTPS API origin |
| `PROOFBEAK_API_TOKEN` | The tenant key issued at initialization |

The MCP host starts the stdio bridge; the REST coordinator runs separately. The bridge
discovers capabilities, validates definitions, submits explicit trials and reads evidence.
It requires no embedded LLM. Existing [REST/MCP contracts](agent-interfaces.md) and
[scenario guide](scenarios.md) apply equally to the installed wheel.

## Use the tested container

The same workflow builds `deploy/Dockerfile.distribution` from the tested wheel and locked
runtime dependencies. The `tested-container-image` artifact contains a Docker archive,
`SHA256SUMS` and rootless/offline smoke results. After extracting it:

```bash
sha256sum --check SHA256SUMS
docker load --input proofbeak-0.6.0.tar.gz
```

On macOS, use `shasum -a 256 --check SHA256SUMS` for the checksum verification.

After loading, the image tag is `proofbeak:distribution`. Follow [deployment configuration](deployment.md)
for the private configuration mount, writable state volume and service networking.
The image is a Linux amd64 build; additional architectures have not been qualified.

A successful registry publication additionally supplies `ghcr.io/eturkok/proofbeak:0.6.0`.
Registry visibility and pull authentication follow the package owner's settings. Local archive
use does not require registry credentials. Prefer a recorded digest for deployments.

For maintainers: [publishing a verified version](publishing.md).
