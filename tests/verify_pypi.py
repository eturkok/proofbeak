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


def install_published(command, *, cwd, env, timeout=90):
    """Retry only an index-propagation miss; never mask hash/dependency failures."""
    deadline = time.monotonic() + timeout
    while True:
        result = subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True)
        if result.returncode == 0:
            return
        output = result.stdout + result.stderr
        propagation = ("No matching distribution found for proofbeak==" in output
                       or "Could not find a version that satisfies the requirement proofbeak==" in output)
        fatal = any(marker in output for marker in ("DO NOT MATCH THE HASHES", "ResolutionImpossible", "CERTIFICATE_VERIFY_FAILED"))
        if not propagation or fatal or time.monotonic() >= deadline:
            raise subprocess.CalledProcessError(result.returncode, command, output=output)
        time.sleep(2)


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
        install_published([str(python), "-m", "pip", "install", "--disable-pip-version-check", "--no-cache-dir",
            "--index-url", "https://pypi.org/simple", "--only-binary=:all:", "--no-deps", "--require-hashes", "-r", str(requirement)],
            cwd=root, env=environment)
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
        def run(*arguments):
            return subprocess.check_output([str(cli), *arguments], cwd=root, env=environment, text=True)
        run("suite", "example", "--output", "suite.json")
        run("suite", "validate", "suite.json")
        run("suite", "run", "suite.json", "--key", "public-pypi-suite", "--output", "suite-report.json",
            "--html", "suite.html", "--junit", "junit.xml", "--summary", "summary.md")
        suite = json.loads((root / "suite-report.json").read_text())
        assert suite["gate"] == "pass" and len(suite["entries"]) == 3
        assert json.loads(run("gate", "suite-report.json"))["gate"] == "pass"
        assert json.loads(run("diff", "suite-report.json", "suite-report.json"))["gate"] == "pass"
        assert all((root / name).stat().st_size for name in ("suite.html", "junit.xml", "summary.md"))
        assert not list((root / ".proofbeak/storage").iterdir())
        subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "--no-cache-dir",
            "--index-url", "https://pypi.org/simple", "--only-binary=:all:", name + "[agents,connectors]==" + version],
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
        run("keys", "generate", "--private", "identity/private.json", "--public", "identity/public.json")
        run("bundle", "create", "suite-report.json", "--key", "identity/private.json", "--output", "suite.bundle.json")
        verification = json.loads(run("bundle", "verify", "suite.bundle.json", "--public-key", "identity/public.json"))
        assert verification["signature_valid"] and verification["content_integrity_valid"]
        assert verification["acceptance_gate"] == "pass"
        subprocess.run([str(python), "-c", "from importlib.resources import files; "
            "from pathlib import Path; from proofbeak.studio import initialize; "
            "assets=files('proofbeak').joinpath('web'); "
            "assert all(assets.joinpath(n).is_file() for n in ('index.html','app.js','style.css','mark.svg')); "
            "p,t=initialize(Path('studio')); assert t and p.is_file(); "
            "assert initialize(Path('studio')) == (p,t)"], cwd=root, env=environment, check=True)
    return {"project_url": f"https://pypi.org/project/{name}/{version}/", "version": version,
            "verified_file_hashes": observed, "public_index_install": "pass", "trial": "pass",
            "cleanup": "cleaned", "evidence_hash": digest, "public_connectors_install": "pass",
            "capability_trial": "pass", "capability_evidence_hash": capability_hash,
            "suite_trial": "pass", "suite_executions": 3, "ci_exports": "pass", "baseline_comparison": "pass",
            "signed_bundle": "pass", "workspace_assets": "pass", "studio_setup": "pass"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = verify(json.loads(args.manifest.read_text()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print("PROOFBEAK_PYPI_VERIFIED=" + json.dumps(result))
