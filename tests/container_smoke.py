"""Build-independent smoke check: rootless read-only runtime, with external networking disabled."""

import hashlib
import json
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path


VERSION_CHECK = '''
import subprocess
from importlib.metadata import version
from proofbeak import __version__
assert version("proofbeak") == __version__
assert subprocess.check_output(["proofbeak", "--version"], text=True).strip() == __version__
print(__version__)
'''


CLIENT = '''
import json, os, sys, time
from proofbeak.interfaces.mcp import BridgeClient
payload = json.load(sys.stdin)
client = BridgeClient(payload.get("endpoint", "http://127.0.0.1:8765"), payload["token"])
deadline = time.monotonic() + 15
while True:
    try:
        catalog = client.request("GET", "/packs")
        break
    except Exception:
        if time.monotonic() > deadline:
            raise
        time.sleep(.1)
spec = next(p["example_test"] for p in catalog["packages"] if p["id"] == "storage.put_same_key.v1")
run = client.request("POST", "/runs", {"test":spec,"candidates":["filesystem"]}, key="offline-container")
run_id = run["run_id"]
deadline = time.monotonic() + 10
while True:
    result = client.request("GET", "/runs/"+run_id)
    if result["state"] in {"completed","interrupted","cancelled"}:
        break
    if time.monotonic() > deadline:
        raise RuntimeError("container trial timed out")
    time.sleep(.05)
assert result["state"] == "completed", result
assert result["candidates"][0]["verdict"] == "pass", result
comparison = client.request("GET", "/runs/"+run_id+"/comparison")
evidence = client.request("GET", "/evidence/"+comparison["candidates"][0]["evidence_ref"])
assert evidence["evidence"]["before"] is not None
assert evidence["evidence"]["after"] is not None
assert evidence["cleanup_status"] == "cleaned"
assert evidence["validity"]["reusable"]
v2_result = None
if "capability:sqlite" in catalog["providers"]:
    v2 = next(p["example_test"] for p in catalog["packages"] if p["id"] == "scenario.v2")
    submitted = client.request("POST", "/runs", {"test":v2,"candidates":["capability:sqlite"]}, key="offline-capabilities")
    limit = time.monotonic() + 15
    while True:
        status = client.request("GET", "/runs/"+submitted["run_id"])
        if status["state"] in {"completed","interrupted","cancelled"}:break
        assert time.monotonic() < limit
        time.sleep(.05)
    assert status["state"] == "completed", status
    v2_comparison = client.request("GET", "/runs/"+submitted["run_id"]+"/comparison")
    v2_result = v2_comparison["candidates"][0]
    assert v2_result["verdict"] == "pass" and v2_result["cleanup_status"] == "cleaned"
assert os.geteuid() != 0
print(json.dumps({"uid":os.geteuid(),"verdict":"pass","cleanup":"cleaned",
                  "run_id":run_id,"evidence_hash":evidence["evidence_hash"],"observed_at":evidence["observed_at"],
                  "worker_backend":comparison["runner"]["backend"],
                  "capability_sqlite": v2_result["verdict"] if v2_result else "not_configured"}))
'''


def smoke(image, uid):
    with tempfile.TemporaryDirectory() as directory:
        token = secrets.token_urlsafe(32)
        config = {"state_dir": "/var/lib/proofbeak/state", "tenants": [{
            "id": "smoke", "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
            "providers": {"filesystem": {}, "capability:sqlite": {"kind":"sqlite","connection":{}}}}]}
        path = Path(directory) / "config.json"
        path.write_text(json.dumps(config))
        # Synthetic digest-only fixture, readable by arbitrary container UID; no real token in this file.
        path.chmod(0o444)
        command = ["docker", "run", "-d", "--network", "none", "--read-only", "--cap-drop", "ALL",
                   "--security-opt", "no-new-privileges:true", "--user", f"{uid}:0",
                   "--tmpfs", f"/tmp:uid={uid},gid=0,mode=0700,size=64m",
                   "--tmpfs", f"/var/lib/proofbeak:uid={uid},gid=0,mode=0700,size=64m",
                   "--volume", f"{path}:/run/proofbeak/config.json:ro", image]
        container = subprocess.check_output(command, text=True).strip()
        try:
            installed_version = subprocess.check_output(
                ["docker", "exec", container, "python", "-c", VERSION_CHECK], text=True).strip()
            completed = subprocess.run(["docker", "exec", "-i", container, "python", "-c", CLIENT],
                input=json.dumps({"token": token}), text=True, capture_output=True, timeout=40)
            if completed.returncode:
                logs = subprocess.check_output(["docker", "logs", container], stderr=subprocess.STDOUT, text=True)
                raise RuntimeError(completed.stderr + "\n" + logs)
            result = json.loads(completed.stdout)
            metadata = json.loads(subprocess.check_output(["docker", "inspect", container], text=True))[0]
            assert metadata["HostConfig"]["NetworkMode"] == "none"
            assert metadata["HostConfig"]["ReadonlyRootfs"]
            return {**result, "version": installed_version,
                    "external_network": "disabled", "rootfs": "read-only"}
        finally:
            subprocess.run(["docker", "rm", "-f", container], check=True, stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    image = sys.argv[1] if len(sys.argv) > 1 else "proofbeak:test"
    results = [smoke(image, uid) for uid in (10001, 100123)]
    output = Path("reports/container-smoke.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))
