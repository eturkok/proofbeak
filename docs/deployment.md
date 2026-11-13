# Container and Helm deployment

This package runs the REST coordinator and its bounded worker subprocesses in one non-root
container. The agent runtime and MCP host stay outside. SQLite requires one writer and a
local persistent volume; the chart rejects multiple replicas and uses a Recreate update.
There is no HA, multi-DC replication, public ingress, registry publication, or automatic deployment.

## Reproducible image

`Dockerfile` pins the Python 3.13 slim base by digest. `requirements-runtime.lock` pins the
agent/calendar dependencies and build tools with hashes. Building needs the package index
to download matching wheels; installation inside the final stage uses only those wheels.
The completed image needs no package index, LLM service, or license server at runtime.
Actual remote calendar trials still need their provider endpoint and dedicated credentials.

```bash
docker build --tag proofbeak:local .
```

The image defaults to UID 10001, group 0, and keeps writable state under `/var/lib/proofbeak`.
Arbitrary non-root UIDs work when the mounted state and temporary directories permit that UID
or its groups. Read-only rootfs, dropped capabilities and resource limits are runtime settings
in Compose and Helm. A worker shares the API container's network and filesystem boundary;
this is not per-trial container or syscall isolation.

For dependency refresh, run `.github/workflows/dependency-lock.yml` manually on a feature
branch. Review the produced lock and Python base digest, commit both deliberately, and rerun
deployment CI. No dependency is resolved again when the locked image is built. This lock
targets Python 3.13; the dependency-free core separately supports Python 3.11+.

## Local Compose

Create a configuration whose state path is inside the container, saving the issued API key
in your client or MCP host's secret store:

```bash
PYTHONPATH=src python3 -m proofbeak.server init \
  --config .proofbeak/container-config.json --state /var/lib/proofbeak/state --tenant demo
```

The initializer writes a mode-0600 configuration owned by the host user. Before mounting it,
grant read access to the container UID/group using your host's ACL or a dedicated copy with
appropriate ownership. Keep it private from other host users. It contains a token digest;
the issued bearer token is not stored in it. Do not weaken an existing production configuration's
permissions just to make a demo work.

```bash
docker compose -f compose.api.yaml up --build -d
curl --fail http://127.0.0.1:8765/health
```

Use the normal REST/MCP clients against loopback port 8765. The named volume persists jobs,
evidence and cleanup intent. Stop the service before running offline maintenance or tenant
purge with that same configuration and volume. Do not run a second coordinator on the volume.
Provider environment secrets are supplied explicitly by the operator through their runtime's
secret mechanism; they are never supplied by trial requests.

## Kubernetes / Helm

Build the image, transfer it to the cluster's approved registry or node image store, and set
`image.repository` plus an immutable `image.digest`. Supply registry credentials through
`imagePullSecrets` when required. The chart does not publish an image or create secrets.

Generate the operator configuration as above, then create its Secret from the local file:

```bash
kubectl create namespace proofbeak
kubectl -n proofbeak create secret generic proofbeak-config \
  --from-file=config.json=.proofbeak/container-config.json
helm upgrade --install proofbeak charts/proofbeak --namespace proofbeak \
  --set existingSecret=proofbeak-config \
  --set image.repository=YOUR_APPROVED_REGISTRY/proofbeak \
  --set image.digest=sha256:YOUR_IMAGE_DIGEST --wait
helm test proofbeak --namespace proofbeak --logs
kubectl -n proofbeak port-forward service/proofbeak-proofbeak 8765:8765 --address 127.0.0.1
```

`existingSecret` is required. Its `config.json` is mounted read-only with mode 0440. If a
calendar provider is configured, `credentialsSecret` can name a separate operator-created
Secret whose keys become the environment variables referenced by the config. The Helm health
test receives neither config nor provider credentials. Apply cluster encryption/RBAC and
secret rotation according to your environment. These Kubernetes Secrets do not implement SSO.
The completed test pod remains available for `helm test --logs` and is replaced on the next
test. Remove it explicitly when finished if your cluster requires immediate hook cleanup.

The default filesystem trial needs no outbound network. The chart's NetworkPolicy denies
all egress and accepts service ingress only from same-namespace pods labeled
`proofbeak-client: 'true'`. Add narrowly scoped `networkPolicy.ingressFrom` and `egressRules`
in an operator-owned values file for actual clients/providers. `allowDNS` adds DNS traffic
to `dnsNamespace`; it defaults to false. DNS permission alone does not allow provider HTTPS.
Enforcement requires a NetworkPolicy-capable CNI; rendering a policy is not evidence of enforcement.
TLS termination and external identity belong at your approved gateway when a remote pilot starts.

State uses a 2-GiB RWO PVC by default. `persistence.existingClaim` accepts a provisioned claim;
`storageClass` selects a cluster class. The default fsGroup is 10001. Storage must support
the required ownership and POSIX locking. Backups/restores are offline operator procedures;
never restore a snapshot into a second live writer or replay interrupted provider writes.
`persistence.enabled=false` is for disposable tests and loses evidence on pod recreation.

For OpenShift, apply `-f charts/proofbeak/openshift-values.yaml` to let the namespace SCC
assign UID/groups. Confirm PVC ownership, Secret readability, egress rules and admission in
that cluster. This profile is provided for compatibility; an actual OpenShift installation
has not been validated by this repository's kind test.

## What CI verifies

`Deployment checks` builds the locked image, then runs an actual filesystem trial twice
with external networking disabled (`--network none`), a read-only rootfs, dropped capabilities,
and UIDs 10001 and 100123. It checks independent before/after evidence and cleanup.

It also lints/renders the chart, rejects replicaCount=2, installs it into an ephemeral kind
cluster with a persistent volume, runs `helm test`, and exercises the deployed REST service.
Reports are uploaded as `deployment-smoke-evidence`. Kindnet does not establish NetworkPolicy
enforcement. The new Calico traffic checks described below must pass before any enforcement
claim. CI uses synthetic fixtures and ephemeral credentials, and never calls a real Google account.

The full container and Kubernetes checks passed in [CI run 43313710253](https://github.com/eturkok/proofbeak/actions/runs/43313710253).

## Persistent restart verification

The Kubernetes smoke check also replaces the API pod after a completed trial, reconnects to
the same PVC and submits the identical idempotency key. It requires the same run ID, evidence
hash and original observation timestamp. This checks durable retrieval through an actual pod
replacement without treating a retry as a fresh observation. The report distinguishes the
CI client's UID from the UID obtained inside the running API pod. It does not establish
backup restore, cross-node storage failover, multi-writer safety or provider recovery during
an interrupted mutation; those have different failure modes.

## NetworkPolicy traffic checks

The policy test uses an ephemeral single-node kind cluster with Calico 3.33.0, following
[Calico's kind installation guidance](https://docs.tigera.io/calico/latest/getting-started/kubernetes/kind).
It disables kindnet and pins the kind node image to Kubernetes 1.35.0. Calico is a CI dependency,
not an extra service required by a deployed Proofbeak image; production clusters supply their CNI.

Actual IPv4 TCP probes must demonstrate all of these conditions:

- A labeled client reaches the API health endpoint; an unlabeled client cannot.
- A control client reaches a healthy synthetic sink while the API pod's default egress is denied.
- An explicit chart egress rule permits the API pod to reach only that selected sink/port.
- Healthy sinks on another port or without the selected label remain unreachable during that allow.
- Removing the rule restores the denial while the control client still reaches the sink.

Rules are changed through helm upgrade against the actual chart, with bounded waits for policy
propagation. Each probe opens a fresh connection. HTTP error responses count as reachable
traffic rather than a network denial. Probe pods receive no API/provider credentials and run
non-root with a read-only rootfs. The network-policy-smoke.json report is uploaded with the
other deployment evidence. A passing test covers this CNI and these IPv4 TCP paths; it does
not validate OpenShift admission, another CNI, IPv6, DNS policies, provider HTTPS allowlists,
or network separation between tenants/workers sharing the same API pod.
