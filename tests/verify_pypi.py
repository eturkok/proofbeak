"""Confirm public PyPI bytes and install the published version in a fresh environment."""

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import time
import venv
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


def verify(manifest):
    name, version = manifest["name"], manifest["version"]
    endpoint = f"https://pypi.org/pypi/{name}/{version}/json"
    deadline = time.monotonic() + 45
    while True:
        try:
            with urlopen(endpoint, timeout=5) as response:
                metadata = json.load(response)
            break
        except (HTTPError, URLError, OSError):
            if time.monotonic() >= deadline:
                raise RuntimeError("published PyPI metadata did not become available") from None
            time.sleep(2)
    observed = {item["filename"]: item["digests"]["sha256"] for item in metadata["urls"]}
    expected = {filename: values["sha256"] for filename, values in manifest["artifacts"].items()}
    assert observed == expected, "PyPI files differ from the tested artifacts"
    environment = {"PATH": os.defpath, "PYTHONUTF8": "1", "PYTHONNOUSERSITE": "1"}
    with tempfile.TemporaryDirectory(prefix="proofbeak-pypi-") as directory:
        root = Path(directory)
        venv.create(root / "venv", with_pip=True)
        python, cli = (root / "venv/bin" / entry for entry in ("python", "proofbeak"))
        wheel_hash = next(digest for filename, digest in expected.items() if filename.endswith(".whl"))
        requirement = root / "published.txt"
        requirement.write_text(name + "==" + version + " --hash=sha256:" + wheel_hash + "\n")
        subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "--no-cache-dir",
            "--index-url", "https://pypi.org/simple", "--only-binary=:all:", "--no-deps", "--require-hashes", "-r", str(requirement)],
            cwd=root, env=environment, check=True, stdout=subprocess.DEVNULL)
        installed = subprocess.check_output([str(cli), "--version"], cwd=root, env=environment, text=True).strip()
        assert installed == version
        subprocess.run([str(cli), "example", "--output", "scenario.json"], cwd=root, env=environment, check=True)
        subprocess.run([str(cli), "run", "scenario.json", "--candidate", "scenario:filesystem", "--key", "public-pypi",
            "--output", "report.json", "--html", "report.html"], cwd=root, env=environment, check=True)
        report = json.loads((root / "report.json").read_text())
        row = report["candidates"][0]
        assert row["verdict"] == "pass" and row["cleanup_status"] == "cleaned"
        digest = hashlib.sha256(json.dumps(row["evidence"], sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False).encode()).hexdigest()
        assert digest == row["evidence_hash"]
        assert not list((root / ".proofbeak/storage").iterdir())
        subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "--no-cache-dir",
            "--index-url", "https://pypi.org/simple", "--only-binary=:all:", name + "[connectors]==" + version],
            cwd=root, env=environment, check=True, stdout=subprocess.DEVNULL)
        subprocess.run([str(cli), "example", "--trial", "scenario.v2", "--output", "scenario-v2.json"],
            cwd=root, env=environment, check=True)
        (root / "providers.json").write_text(json.dumps({"capability:sqlite": {"kind": "sqlite", "connection": {}}}))
        subprocess.run([str(cli), "run", "scenario-v2.json", "--providers", "providers.json",
            "--candidate", "capability:sqlite", "--key", "public-pypi-capabilities", "--output", "capability-report.json"],
            cwd=root, env=environment, check=True)
        capability = json.loads((root / "capability-report.json").read_text())["candidates"][0]
        assert capability["verdict"] == "pass" and capability["cleanup_status"] == "cleaned"
        capability_hash = hashlib.sha256(json.dumps(capability["evidence"], sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        assert capability_hash == capability["evidence_hash"]
        assert not list((root / ".proofbeak/provider-data/cli/sqlite").iterdir())
    return {"project_url": f"https://pypi.org/project/{name}/{version}/", "version": version,
            "verified_file_hashes": observed, "public_index_install": "pass", "trial": "pass",
            "cleanup": "cleaned", "evidence_hash": digest, "public_connectors_install": "pass",
            "capability_trial": "pass", "capability_evidence_hash": capability_hash}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = verify(json.loads(args.manifest.read_text()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print("PROOFBEAK_PYPI_VERIFIED=" + json.dumps(result))
