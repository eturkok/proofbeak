# Bounded worker processes

The API process owns authentication, a durable queue, and a scheduling thread. Every trial
and cleanup operation now executes in a fresh Python subprocess. A provider failure does
not execute in the API's interpreter. The core CLI can still run synchronously.

## Operator limits

The server configuration optionally accepts an `execution` object:

```json
{"wall_seconds": 120, "cpu_seconds": 30, "memory_mib": 512}
```

These defaults cap wall time per worker, CPU seconds, and Linux address space. Output is
bounded to 4 MiB, open file descriptors to 128, and core dumps are disabled. Memory address
space enforcement is reported as false on macOS; wall time, process separation, CPU, and
output limits still apply. Client run bodies cannot change these settings. Limits and the
executor version are included in queued-context fingerprints, so a changed execution policy
does not silently execute an older queued request under different conditions.

The parent supervises wall time and kills the worker's owned process group on timeout. The
child also has a timer and watches its parent identity, so an API process disappearance
terminates an orphan worker. There is one trial worker plus at most one concurrent cleanup
worker. Further cleanup attempts return 429. Shutdown cancels owned workers and retains the
service lock if its coordinator has not finished.

## Credentials and extension boundary

Worker credentials travel through a private anonymous stdin pipe, never argv, the queue,
logs, or a persisted job manifest. The subprocess environment is deliberately minimal; API
tokens, unrelated cloud credentials, and parent environment secrets are not inherited.
Output is a private temporary file, bounded in the child and validated in the parent.

Only explicitly registered adapter codecs may construct worker adapters. Trial input cannot
select an import path, executable, shell command, or codec. To extend the service with a new
trusted provider, implement its adapter and register a codec in `worker_adapters.py`; the
common executor does not interpret calendar or storage semantics. Injected fake transports
are not silently promoted into real-provider workers.

This is process separation and resource bounding on a trusted host. It is not a container,
user-namespace, filesystem, syscall, or network sandbox. Container/job isolation and enforced
egress policies remain deployment-level work; arbitrary untrusted plugin code is not allowed.

## Timeout, crash, cancellation, and recovery

| Event | Recorded behavior |
| --- | --- |
| Wall time exhausted | Interrupted with `trial_timed_out`; no provider-failure verdict is fabricated. |
| Abrupt worker exit | Interrupted with `worker_crashed`; the resource ledger remains available. |
| Queued cancellation | Cancelled before dispatch; no provider writes occur. |
| Running cancellation | Terminate the owned worker; retain cleanup intent; report `worker_cancelled`. |
| API restart | Existing claimed jobs remain interrupted; writes are never replayed. |

REST exposes `POST /runs/{id}/cancel`; MCP exposes `cancel_run`. Both are tenant-bound and
idempotent. If a trial finished before the cancellation arrived, its completed result is
retained. Cleanup is a separate bounded operation and may be retried only for terminal runs.
A killed worker's `finally` block cannot be assumed to run, so pending resources are not
reported as cleaned. Ledger ownership prevents cleanup from racing a still-alive worker.

Automated checks use real subprocesses for normal completion, timeout, abrupt crash,
cancellation, locked-ledger recovery, and API-parent disappearance. A REST regression hangs
one worker, checks that `/health` stays responsive, and verifies the following filesystem
trial still completes. All fault controls remain labeled simulation evidence.
