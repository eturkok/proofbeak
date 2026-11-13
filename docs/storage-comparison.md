# Primary case: repeat a file write

Acceptance question: write the same content to the same key again; keep exactly one visible
object, its key and its content. This reuses storage.put_same_key.v1 and the generic engine.
The baseline is independently listed/read after the initial write. After another write,
PROPFIND and GET observe the actual service state; a PUT success response does not determine
the verdict. File bytes are hashed, not inferred from the submitted content.

WsgiDAV and Apache WebDAV are real self-hosted candidates. The local filesystem adapter is
a third reference using the identical package. Both HTTP servers use disposable test roots
and local fixture credentials. No cloud/Google account, LLM service or Kubernetes cluster is
required. The servers are example providers, not Proofbeak runtime dependencies.

## Run the local comparison

The core adapter uses only Python's standard library. Docker/Compose is optional packaging
for the two external fixtures:

    docker compose -f compose.storage.yaml up --build -d
    export PROOFBEAK_WEBDAV_WSGIDAV_URL=http://127.0.0.1:8801
    export PROOFBEAK_WEBDAV_WSGIDAV_PASSWORD=proofbeak-local-only
    export PROOFBEAK_WEBDAV_APACHE_URL=http://127.0.0.1:8802
    export PROOFBEAK_WEBDAV_APACHE_PASSWORD=proofbeak-local-only
    PYTHONPATH=src python3 -m proofbeak run examples/storage.put_same_key.v1.json \
      --candidate webdav:wsgidav --candidate webdav:apache --candidate filesystem \
      --key storage-comparison-1 --output reports/storage-comparison.json \
      --html reports/storage-comparison.html

The username defaults to proofbeak for these fixtures. The password above is a public demo
credential, usable only for disposable loopback fixtures. Each container runs non-root with
a read-only rootfs and temporary data. Stopping/removing the fixtures discards their contents:

    docker compose -f compose.storage.yaml down

For a remote WebDAV origin, configure a dedicated test user and HTTPS. The adapter accepts
an origin without URL credentials or arbitrary base paths. Requests never mutate existing
folders: every run uses a generated proofbeak-<run-id> collection. A named candidate has
operator-defined configuration; a trial request cannot supply a URL or credentials.

## REST/MCP operator profile

Add the allowed candidate to the tenant's providers map; supply the password in the server's
local secret environment. Existing run/compare/evidence/cleanup MCP tools need no new arguments:

    {
      "webdav:my-storage": {
        "endpoint": "https://dedicated-test-origin.example",
        "username": "proofbeak-test",
        "password_env": "MY_STORAGE_TEST_PASSWORD"
      }
    }

CLI aliases use PROOFBEAK_WEBDAV_<ALIAS>_URL, _USERNAME and _PASSWORD; uppercase the alias and
replace hyphens with underscores. Environment examples are documented in .env.example; the
CLI does not load that file automatically.

## Evidence and limits

The first PUT creates the fixture. A strong ETag from the baseline GET guards the second PUT
with If-Match; a concurrent modification or missing ETag yields inconclusive. These are two
writes of the same key/content under observed preconditions, not replay of identical HTTP
headers after a deliberately dropped response. No exact-once or crash-durability claim follows.

The observer counts visible stored files and checks their keys/content. The internal ownership
marker is verified separately and excluded from the test object count. Hidden version histories,
cross-region durability, eventual consistency bounds and multi-writer semantics need other packages.
Two services passing the same condition is a valid result; it does not establish a ranking or
prove that either is better for an untested condition.

Only a collection with the expected ownership marker can be deleted by a fresh recovery
adapter. During the same process, a confirmed new collection can also be cleaned if its
marker write failed. After a crash before marker persistence, ownership remains unresolved
and cleanup is quarantined. Existing collection conflicts never grant deletion permission.
Cleanup verifies that the collection is absent after DELETE and keeps its status separate
from the acceptance verdict.

Unknown versions have the existing bounded validity policy. Injected transports and protocol
fault controls are simulations and are excluded from provider-comparison eligibility.

## Verification

The protocol suite covers duplicates, changed content, missing data, read gaps, permission
denial, ambiguous writes, ETag conflicts, invalid/foreign/traversal listings and cleanup recovery:

    PYTHONPATH=src python3 -m unittest discover -s tests -p test_webdav.py -v

The mandatory CI job starts actual WsgiDAV 4.4.0 and Apache 2.4.71, executes the same ASCII and
UTF-8 fixtures in bounded subprocesses, verifies idempotent retrieval and resource cleanup,
and checks permission failures. JSON/HTML reports are stored as
storage-provider-comparison-evidence. These are real provider observations over synthetic
data, not a human pilot or a measured improvement in agent decisions.

To run that job's integration locally, install the fixture dependencies and build the image:

    python -m pip install 'wsgidav==4.4.0' 'cheroot==11.1.2'
    docker build -f deploy/Dockerfile.webdav-apache -t proofbeak-webdav-apache:test .
    PROOFBEAK_REQUIRE_WEBDAV=1 PYTHONPATH=src python3 tests/test_webdav_integration.py

Primary references: [Apache mod_dav](https://httpd.apache.org/docs/2.4/en/mod/mod_dav.html),
[WsgiDAV configuration](https://wsgidav.readthedocs.io/en/stable/user_guide_configure.html),
and [WsgiDAV embedding](https://wsgidav.readthedocs.io/en/stable/user_guide_lib.html).
