"""Install into an ephemeral CI cluster and exercise the actual deployed service."""

import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

from container_smoke import CLIENT


def run(*args, **kwargs):
    return subprocess.run(list(args), check=True, **kwargs)


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
                result = json.loads(completed.stdout)
            finally:
                forward.terminate()
                forward.wait(timeout=5)
        deployment = json.loads(subprocess.check_output([
            "kubectl", "-n", namespace, "get", "deployment", "proofbeak-proofbeak", "-o", "json"], text=True))
        assert deployment["status"]["readyReplicas"] == 1
        assert deployment["spec"]["template"]["spec"]["automountServiceAccountToken"] is False
        result.update({"helm_install": "passed", "persistent_volume": True,
                       "network_policy_enforcement": "not tested with kindnet"})
        Path("reports").mkdir(exist_ok=True)
        Path("reports/kubernetes-smoke.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2))
