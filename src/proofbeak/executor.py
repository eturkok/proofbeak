"""Bounded POSIX subprocess execution; this is process separation, not a container sandbox."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Event, Lock, Semaphore

from proofbeak.models import canonical
from proofbeak.worker_adapters import encode_adapter

MAX_INPUT = 262144
MAX_OUTPUT = 4194304


@dataclass(frozen=True)
class WorkerLimits:
    wall_seconds: float = 120
    cpu_seconds: int = 30
    memory_mib: int = 512

    def __post_init__(self):
        if type(self.wall_seconds) not in {int, float} or not 0.1 <= self.wall_seconds <= 3600:
            raise ValueError("wall_seconds must be between 0.1 and 3600")
        if type(self.cpu_seconds) is not int or not 1 <= self.cpu_seconds <= 300:
            raise ValueError("cpu_seconds must be between one and 300")
        if type(self.memory_mib) is not int or not 128 <= self.memory_mib <= 4096:
            raise ValueError("memory_mib must be between 128 and 4096")


@dataclass(frozen=True)
class WorkerResult:
    payload: dict | None
    reason: str | None
    pid: int | None
    elapsed: float


def kill_group(process, sig=signal.SIGKILL):
    try:
        os.killpg(process.pid, sig)
    except ProcessLookupError:
        pass


class SubprocessExecutor:
    version = "1"

    def __init__(self, limits=None):
        self.limits = limits or WorkerLimits()
        self.guard = Lock()
        self.active = {}
        self.cancelled_keys = set()
        self.stopping = Event()
        self.cleanup_slot = Semaphore(1)

    def context(self):
        return {"backend": "subprocess", "version": self.version, **asdict(self.limits)}

    def validate(self, adapters):
        try:
            encoded = [encode_adapter(adapter) for adapter in adapters]
            if len(canonical(encoded).encode()) > MAX_INPUT // 2:
                raise ValueError("worker configuration too large")
        except (TypeError, ValueError):
            raise ValueError("provider is not configured for subprocess execution") from None

    def reset(self):
        with self.guard:
            if self.active:
                raise RuntimeError("workers are still active")
            self.stopping.clear()
            self.cancelled_keys.clear()

    def cancel_all(self):
        self.stopping.set()
        with self.guard:
            for process in self.active.values():
                kill_group(process)

    def cancel(self, key):
        with self.guard:
            self.cancelled_keys.add(key)
            for process in self.active.values():
                if getattr(process, "proofbeak_key", None) == key:
                    kill_group(process)

    def run(self, spec, adapters, ledger, key):
        return self._launch({"action": "run", "test": spec, "key": key,
                             "adapters": [encode_adapter(a) for a in adapters], "ledger": str(ledger)})

    def cleanup(self, adapters, ledger):
        if not self.cleanup_slot.acquire(blocking=False):
            return WorkerResult(None, "cleanup_busy", None, 0)
        try:
            return self._launch({"action": "cleanup", "adapters": [encode_adapter(a) for a in adapters],
                                 "ledger": str(ledger)})
        finally:
            self.cleanup_slot.release()

    def _launch(self, job):
        started = time.monotonic()
        envelope = canonical({**job, "parent_pid": os.getpid(), "limits": asdict(self.limits)}).encode()
        if len(envelope) > MAX_INPUT:
            return WorkerResult(None, "worker_input_too_large", None, 0)
        environment = {"PATH": os.defpath, "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
                       "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"}
        with tempfile.TemporaryFile() as output:
            with self.guard:
                if self.stopping.is_set() or job.get("key") in self.cancelled_keys:
                    return WorkerResult(None, "worker_cancelled", None, 0)
                process = subprocess.Popen([sys.executable, "-m", "proofbeak.worker"],
                    stdin=subprocess.PIPE, stdout=output, stderr=subprocess.DEVNULL,
                    env=environment, start_new_session=True, close_fds=True)
                self.active[process.pid] = process
                process.proofbeak_key = job.get("key")
            reason = None
            try:
                try:
                    process.communicate(input=envelope, timeout=self.limits.wall_seconds)
                except subprocess.TimeoutExpired:
                    reason = "trial_timed_out"
                    kill_group(process, signal.SIGTERM)
                    try:
                        process.wait(timeout=0.3)
                    except subprocess.TimeoutExpired:
                        kill_group(process)
                        process.wait(timeout=3)
                if self.stopping.is_set() or job.get("key") in self.cancelled_keys:
                    reason = "worker_cancelled"
                if reason is None and process.returncode != 0:
                    reason = "worker_crashed"
                output.seek(0)
                content = output.read(MAX_OUTPUT + 1)
                if len(content) > MAX_OUTPUT:
                    reason = "worker_output_too_large"
                payload = None
                if reason is None:
                    try:
                        decoded = json.loads(content)
                        if not isinstance(decoded, dict) or decoded.get("pid") != process.pid:
                            raise ValueError("invalid worker response")
                        if decoded.get("error"):
                            allowed = {"worker_failed", "run_still_active", "resource_limits_unavailable"}
                            reason = decoded["error"] if decoded["error"] in allowed else "worker_failed"
                        else:
                            payload = decoded
                    except (ValueError, UnicodeDecodeError):
                        reason = "worker_protocol_error"
                return WorkerResult(payload, reason, process.pid, time.monotonic() - started)
            finally:
                # Reap any descendants still in the owned process group, even after a normal exit.
                kill_group(process)
                if process.poll() is None:
                    process.wait(timeout=3)
                with self.guard:
                    self.active.pop(process.pid, None)
