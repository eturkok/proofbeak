"""Actual 0.3.0 source -> current coordinator -> snapshot/restore qualification."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from proofbeak.server import load_service
from proofbeak.service import ServiceError

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "db9ad1b4aeffa01ff637e29734d652d36e10d8fc"
BOOTSTRAP = '''
import json,sys
from proofbeak import __version__
from proofbeak.server import load_service
payload=json.load(sys.stdin)
service=load_service(payload["config"])
service.start(background=False)
try:
    def completed(key):
        run=service.submit("upgrade",payload["spec"],["filesystem"],key)["run_id"]
        service.process_next()
        return run
    historical=completed("historical")
    evidence=service.evidence("upgrade",historical+":0")
    deleted=completed("deleted")
    service.delete_run("upgrade",deleted)
    queued=service.submit("upgrade",payload["spec"],["filesystem"],"queued")["run_id"]
    print(json.dumps({"version":__version__,"historical":historical,"deleted":deleted,
                      "queued":queued,"evidence_hash":evidence["evidence_hash"],
                      "observed_at":evidence["observed_at"]}))
finally:
    service.close()
'''


def baseline_source(ref, destination):
    git = ["git", "-C", str(ROOT)]
    if (ROOT / ".proofbeak/git").is_dir():
        git = ["git", "--git-dir=" + str(ROOT / ".proofbeak/git"), "--work-tree=" + str(ROOT)]
    names = subprocess.check_output([*git, "ls-tree", "-r", "--name-only", ref, "--", "src"], text=True).splitlines()
    if not names:
        raise RuntimeError("baseline source missing; fetch the pinned 0.3.0 commit")
    for name in names:
        if not re.fullmatch(r"src/proofbeak/[A-Za-z0-9_/]+\.py", name):
            raise RuntimeError("unexpected baseline source file")
        target = destination / Path(name).relative_to("src")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(subprocess.check_output([*git, "show", ref + ":" + name]))


def cli(*args):
    completed = subprocess.run([sys.executable, "-m", "proofbeak.server", *args], cwd=ROOT,
                               text=True, capture_output=True, check=True, timeout=30)
    return json.loads(completed.stdout)


def qualify(ref):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        previous = root / "baseline"
        baseline_source(ref, previous)
        state, config = root / "state", root / "config.json"
        config.write_text(json.dumps({"state_dir": str(state), "tenants": [{
            "id": "upgrade", "token_sha256": hashlib.sha256(os.urandom(32)).hexdigest(),
            "providers": {"filesystem": {}}}]}))
        config.chmod(0o600)
        spec = json.loads((ROOT / "examples/storage.put_same_key.v1.json").read_text())
        completed = subprocess.run([sys.executable, "-c", BOOTSTRAP],
            input=json.dumps({"config": str(config), "spec": spec}), text=True,
            capture_output=True, check=True, timeout=30,
            env={"PATH": os.defpath, "PYTHONPATH": str(previous), "PYTHONUTF8": "1"})
        old = json.loads(completed.stdout)
        assert old["version"] == "0.3.0", "qualification requires the released 0.3.0 implementation"
        service = load_service(config)
        service.start(background=False)
        try:
            evidence = service.evidence("upgrade", old["historical"] + ":0")
            assert evidence["evidence_hash"] == old["evidence_hash"]
            assert evidence["observed_at"] == old["observed_at"]
            assert service.submit("upgrade", spec, ["filesystem"], "historical")["run_id"] == old["historical"]
            assert service.get_run("upgrade", old["queued"])["state"] == "queued"
            assert service.process_next()
            assert service.get_run("upgrade", old["queued"])["candidates"][0]["verdict"] == "pass"
            assert not service.process_next()
            try:
                service.submit("upgrade", spec, ["filesystem"], "deleted")
                raise AssertionError("deleted key replayed on upgrade")
            except ServiceError as error:
                assert error.code == "run_deleted"
        finally:
            service.close()
        cli("backup", "--state", str(state), "--output", str(root / "snapshot"))
        state.rename(root / "pre-restore-state")
        restored = cli("restore", "--snapshot", str(root / "snapshot"), "--state", str(state))
        assert cli("check", "--config", str(config), "--require-providers")["providers_configured"]
        service = load_service(config)
        service.start(background=False)
        try:
            again = service.evidence("upgrade", old["historical"] + ":0")
            assert again["evidence_hash"] == old["evidence_hash"]
            assert again["observed_at"] == old["observed_at"]
            assert service.submit("upgrade", spec, ["filesystem"], "historical")["run_id"] == old["historical"]
            try:
                service.submit("upgrade", spec, ["filesystem"], "unknown-before-restore")
                raise AssertionError("missing stale key accepted after restore")
            except ServiceError as error:
                assert error.code == "restore_key_prefix_required"
            new = service.submit("upgrade", spec, ["filesystem"],
                                 restored["idempotency_key_prefix"] + "explicit-new")["run_id"]
            assert service.process_next()
            assert service.get_run("upgrade", new)["candidates"][0]["verdict"] == "pass"
            assert not service.process_next()
        finally:
            service.close()
        return {"baseline": ref, "baseline_version": old["version"], "upgrade": "passed",
                "historical_run_id": old["historical"], "original_evidence_hash": old["evidence_hash"],
                "observed_at_preserved": True, "queued_work_survived_upgrade": True,
                "deleted_key_blocked": True, "backup_restore_cli": "passed",
                "stale_restore_key_blocked": True, "explicit_new_trial": "passed"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-ref", default=BASELINE)
    parser.add_argument("--output", type=Path, default=Path("reports/upgrade-smoke.json"))
    args = parser.parse_args()
    result = qualify(args.baseline_ref)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
