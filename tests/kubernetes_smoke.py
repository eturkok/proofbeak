"""Install into an ephemeral CI cluster and verify evidence/idempotency through a pod restart."""

import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

from container_smoke import CLIENT, VERSION_CHECK


def run(*args, **kwargs):
    return subprocess.run(list(args), check=True, **kwargs)


def deployed_trial(namespace, root, token):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with (root / "forward.log").open("w+") as log:
        forward = subprocess.Popen(["kubectl", "-n", namespace, "port-forward",
            "service/proofbeak-proofbeak", f"{port}:8765", "--address", "127.0.0.1"],
            stdout=log, stderr=log)
        try:
            completed = subprocess.run([sys.executable, "-c", CLIENT],
                input=json.dumps({"token": token, "endpoint": f"http://127.0.0.1:{port}"}),
                text=True, capture_output=True, timeout=40,
                env={**os.environ, "PYTHONPATH": str(Path.cwd() / "src")})
            if completed.returncode:
                log.seek(0)
                raise RuntimeError(completed.stderr + "\n" + log.read())
            return json.loads(completed.stdout)
        finally:
            forward.terminate()
            forward.wait(timeout=5)


def pod_uid(namespace):
    pods = json.loads(subprocess.check_output(["kubectl", "-n", namespace, "get", "pods",
        "-l", "app.kubernetes.io/instance=proofbeak,app.kubernetes.io/component=server", "-o", "json"],
        text=True))["items"]
    assert len(pods) == 1, "the singleton deployment must have one server pod"
    return pods[0]["metadata"]["uid"]


if __name__ == "__main__":
    namespace = "proofbeak-smoke"
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        token = secrets.token_urlsafe(32)
        config = {"state_dir": "/var/lib/proofbeak/state", "tenants": [{
            "id": "smoke", "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
            "providers": {"filesystem": {}}}]}
        path = root / "config.json"
        path.write_text(json.dumps(config))
        run("kubectl", "create", "namespace", namespace)
        run("kubectl", "-n", namespace, "create", "secret", "generic", "proofbeak-config",
            "--from-file=config.json=" + str(path))
        run("helm", "upgrade", "--install", "proofbeak", "charts/proofbeak", "--namespace", namespace,
            "--set", "existingSecret=proofbeak-config", "--set", "image.repository=proofbeak",
            "--set", "image.tag=test", "--set", "image.pullPolicy=Never", "--wait", "--timeout", "180s")
        run("helm", "test", "proofbeak", "--namespace", namespace, "--logs", "--timeout", "90s")
        first = deployed_trial(namespace, root, token)
        original_pod = pod_uid(namespace)
        run("kubectl", "-n", namespace, "rollout", "restart", "deployment/proofbeak-proofbeak")
        run("kubectl", "-n", namespace, "rollout", "status", "deployment/proofbeak-proofbeak", "--timeout=120s")
        assert pod_uid(namespace) != original_pod
        result = deployed_trial(namespace, root, token)
        for field in ("run_id", "evidence_hash", "observed_at"):
            assert first[field] == result[field], "a restart/retry must preserve the original evidence"
        deployment = json.loads(subprocess.check_output([
            "kubectl", "-n", namespace, "get", "deployment", "proofbeak-proofbeak", "-o", "json"], text=True))
        assert deployment["status"]["readyReplicas"] == 1
        assert deployment["spec"]["template"]["spec"]["automountServiceAccountToken"] is False
        actual_uid = int(subprocess.check_output(["kubectl", "-n", namespace, "exec",
            "deployment/proofbeak-proofbeak", "--", "python", "-c", "import os; print(os.geteuid())"], text=True))
        assert actual_uid != 0
        installed_version = subprocess.check_output(["kubectl", "-n", namespace, "exec",
            "deployment/proofbeak-proofbeak", "--", "python", "-c", VERSION_CHECK], text=True).strip()
        result.update({"client_uid": result.pop("uid"), "uid": actual_uid, "version": installed_version,
                       "helm_install": "passed", "persistent_volume": True,
                       "pod_replaced": True, "evidence_survived_restart": True,
                       "idempotency_survived_restart": True,
                       "network_policy_enforcement": "assessed separately by network_policy_smoke.py"})
        Path("reports").mkdir(exist_ok=True)
        Path("reports/kubernetes-smoke.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2))
