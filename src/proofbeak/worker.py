"""Private subprocess entry point. No credentials are accepted on argv or inherited env."""

import json
import os
import resource
import signal
import sys
from dataclasses import asdict
from pathlib import Path
from threading import Event, Thread

from proofbeak.engine import recover_cleanup, run_trial
from proofbeak.executor import MAX_INPUT, MAX_OUTPUT, WorkerLimits
from proofbeak.models import canonical
from proofbeak.store import Store
from proofbeak.worker_adapters import decode_adapter


def restrict(limits):
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CPU, (limits.cpu_seconds, limits.cpu_seconds + 1))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_OUTPUT, MAX_OUTPUT))
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    ceiling = min(128, hard) if hard != resource.RLIM_INFINITY else 128
    resource.setrlimit(resource.RLIMIT_NOFILE, (min(soft, ceiling), ceiling))
    memory_enforced = sys.platform.startswith("linux")
    if memory_enforced:
        memory = limits.memory_mib * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    return {**asdict(limits), "memory_limit_enforced": memory_enforced,
            "output_bytes": MAX_OUTPUT, "process_separation": True}


def main():
    stopped = Event()
    store = None
    try:
        envelope = json.loads(sys.stdin.buffer.read(MAX_INPUT + 1))
        limits = WorkerLimits(**envelope["limits"])
        parent_pid = envelope["parent_pid"]
        if type(parent_pid) is not int or os.getppid() != parent_pid or os.getpgrp() != os.getpid():
            raise ValueError("invalid process ownership")
        # The child retains its own deadline if the API process disappears.
        signal.signal(signal.SIGALRM, lambda *_: os.killpg(os.getpgrp(), signal.SIGKILL))
        signal.setitimer(signal.ITIMER_REAL, limits.wall_seconds)
        def watch_parent():
            while not stopped.wait(0.1):
                if os.getppid() != parent_pid:
                    os.killpg(os.getpgrp(), signal.SIGKILL)
        Thread(target=watch_parent, name="proofbeak-parent-watch", daemon=True).start()
        try:
            applied = restrict(limits)
        except (OSError, ValueError):
            result = {"error": "resource_limits_unavailable"}
        else:
            adapters = [decode_adapter(item) for item in envelope["adapters"]]
            try:
                store = Store(Path(envelope["ledger"]))
            except ValueError:
                result = {"error": "run_still_active"}
            else:
                if envelope["action"] == "run":
                    report = run_trial(envelope["test"], adapters, store, envelope["key"])
                    report["runner"] = {"backend": "subprocess", "limits": applied}
                    result = {"report": report}
                elif envelope["action"] == "cleanup":
                    result = {"outcomes": recover_cleanup(store, adapters)}
                else:
                    raise ValueError("unknown worker action")
    except Exception:
        # Provider errors may contain tokens; neither traceback nor raw exception crosses IPC.
        result = {"error": "worker_failed"}
    finally:
        if store:
            store.close()
        stopped.set()
    sys.stdout.write(canonical({"pid": os.getpid(), **result}))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
