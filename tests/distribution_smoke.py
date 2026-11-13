"""Build-artifact inspection and real installation outside the source checkout."""

import argparse
import configparser
import hashlib
import json
import os
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
import zipfile
from email.parser import Parser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = {".git", ".proofbeak", ".venv", "__pycache__", "reports"}


def inspect_artifacts(directory):
    wheels, sources = list(directory.glob("*.whl")), list(directory.glob("*.tar.gz"))
    assert len(wheels) == len(sources) == 1, "one wheel and one source distribution required"
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    inventories = {}
    for path in [*wheels, *sources]:
        if path.suffix == ".whl":
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
                metadata = Parser().parsestr(archive.read(next(n for n in names if n.endswith(".dist-info/METADATA"))).decode())
                entrypoints = configparser.ConfigParser()
                entrypoints.read_string(archive.read(next(n for n in names if n.endswith(".dist-info/entry_points.txt"))).decode())
                assert set(entrypoints["console_scripts"]) == set(project["scripts"])
                assert metadata["Name"] == project["name"] and metadata["Version"] == project["version"]
                assert metadata["License-Expression"] == "Apache-2.0"
                assert all("extra ==" in requirement for requirement in metadata.get_all("Requires-Dist", []))
                assert set(metadata.get_all("Provides-Extra", [])) == set(project["optional-dependencies"])
                assert "Documentation, https://github.com/eturkok/proofbeak/blob/main/docs/installation.md" in metadata.get_all("Project-URL")
                assert {"proofbeak/web/index.html", "proofbeak/web/app.js", "proofbeak/web/style.css", "proofbeak/web/mark.svg"} <= set(names)
        else:
            with tarfile.open(path) as archive:
                names = archive.getnames()
                assert any(n.endswith("/pyproject.toml") for n in names)
                assert any(n.endswith("/tests/distribution_smoke.py") for n in names)
        for name in names:
            parts = Path(name).parts
            assert not FORBIDDEN.intersection(parts), "private/generated path in distribution"
            assert not any(p == ".env" or p.startswith(".env.") for p in parts)
            assert not name.endswith((".sqlite3", ".db", ".pem", ".key", ".pyc"))
        inventories[path.name] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size,
                                  "entries": len(names)}
    return {"name": project["name"], "version": project["version"], "artifacts": inventories}


def clean_environment():
    return {"PATH": os.defpath, "PYTHONUTF8": "1", "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"}


MCP_CHECK = '''
import asyncio, json, os, sys, time
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from proofbeak.models import fingerprint
import proofbeak
assert Path(proofbeak.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
async def check():
    parameters = StdioServerParameters(command=sys.executable, args=["-I", "-m", "proofbeak.interfaces.mcp"],
        env={"PATH":os.defpath,"PROOFBEAK_API_URL":os.environ["PROOFBEAK_API_URL"],
             "PROOFBEAK_API_TOKEN":os.environ["PROOFBEAK_API_TOKEN"]})
    async with stdio_client(parameters) as (read,write):
        async with ClientSession(read,write) as session:
            await session.initialize()
            async def call(name,args):
                response=await session.call_tool(name,args)
                assert not response.isError
                assert isinstance(response.structuredContent,dict)
                return response.structuredContent
            catalog=await call("list_trial_packs",{})
            assert "scenario:filesystem" in catalog["providers"]
            spec=json.loads(Path("scenario.json").read_text())
            validation=await call("validate_trial",{"test":spec,"candidates":["scenario:filesystem"]})
            assert validation["valid"] and not validation["execution_performed"]
            args={"test":spec,"candidates":["scenario:filesystem"],"idempotency_key":"installed-wheel"}
            created=await call("run_trial",args)
            deadline=time.monotonic()+20
            while True:
                state=await call("get_run",{"run_id":created["run_id"]})
                if state["state"] in {"completed","interrupted","cancelled"}:break
                assert time.monotonic()<deadline
                await asyncio.sleep(.05)
            assert state["state"]=="completed"
            report=await call("compare_results",{"run_id":created["run_id"]})
            row=report["candidates"][0]
            assert row["verdict"]=="pass" and row["cleanup_status"]=="cleaned"
            evidence=await call("get_evidence",{"evidence_id":row["evidence_ref"]})
            assert fingerprint(evidence["evidence"])==row["evidence_hash"]
            retried=await call("run_trial",args)
            assert retried["run_id"]==created["run_id"]
            v2=next(p["example_test"] for p in catalog["packages"] if p["id"]=="scenario.v2")
            v2args={"test":v2,"candidates":["capability:sqlite"],"idempotency_key":"installed-capabilities"}
            assert (await call("validate_trial",{"test":v2,"candidates":["capability:sqlite"]}))["valid"]
            v2created=await call("run_trial",v2args)
            deadline=time.monotonic()+20
            while True:
                v2state=await call("get_run",{"run_id":v2created["run_id"]})
                if v2state["state"] in {"completed","interrupted","cancelled"}:break
                assert time.monotonic()<deadline
                await asyncio.sleep(.05)
            assert v2state["state"]=="completed"
            v2report=await call("compare_results",{"run_id":v2created["run_id"]})
            v2row=v2report["candidates"][0]
            assert v2row["verdict"]=="pass" and v2row["cleanup_status"]=="cleaned"
            v2evidence=await call("get_evidence",{"evidence_id":v2row["evidence_ref"]})
            assert fingerprint(v2evidence["evidence"])==v2row["evidence_hash"]
            assert (await call("run_trial",v2args))["run_id"]==v2created["run_id"]
            print(json.dumps({"version":proofbeak.__version__,"run_id":created["run_id"],
                "verdict":row["verdict"],"cleanup":row["cleanup_status"],"evidence_hash":row["evidence_hash"],
                "installed_module":str(Path(proofbeak.__file__).resolve()),"mcp":"actual_stdio","worker":report["runner"]["backend"],
                "scenario_v2":{"verdict":v2row["verdict"],"cleanup":v2row["cleanup_status"],"evidence_hash":v2row["evidence_hash"]}}))
asyncio.run(check())
'''


def installation(directory, agents):
    wheel = next(directory.glob("*.whl")).resolve()
    with tempfile.TemporaryDirectory(prefix="proofbeak-installed-") as temporary:
        root = Path(temporary)
        environment = clean_environment()
        venv = root / "venv"
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True, env=environment)
        python, cli, server = (venv / "bin" / name for name in ("python", "proofbeak", "proofbeak-server"))
        install = [str(python), "-m", "pip", "install", "--disable-pip-version-check"]
        install += [str(wheel) + "[agents,calendar,connectors]"] if agents else ["--no-index", "--no-deps", str(wheel)]
        subprocess.run(install, check=True, env=environment, stdout=subprocess.DEVNULL)
        subprocess.run([str(python), "-m", "pip", "check"], check=True, env=environment)
        work = root / "work"
        work.mkdir()
        def run(args):
            result = subprocess.run([str(arg) for arg in args], cwd=work, env=environment, capture_output=True, text=True, timeout=30)
            assert result.returncode == 0, "installed command failed: " + str(args[0])
            return result.stdout
        version = run([cli, "--version"]).strip()
        run([cli, "studio", "--help"])
        run([cli, "example", "--output", "scenario.json"])
        run([cli, "validate", "scenario.json", "--candidate", "scenario:filesystem", "--json"])
        run([cli, "run", "scenario.json", "--candidate", "scenario:filesystem", "--key", "installed-core",
             "--output", "report.json", "--html", "report.html"])
        report = json.loads((work / "report.json").read_text())
        assert report["candidates"][0]["verdict"] == "pass"
        assert report["candidates"][0]["cleanup_status"] == "cleaned"
        installed = json.loads(run([python, "-I", "-c", "import json,proofbeak; from importlib.metadata import version; "
            "print(json.dumps({'path':proofbeak.__file__,'module':proofbeak.__version__,'package':version('proofbeak')}))"]))
        location = installed["path"]
        assert installed["module"] == installed["package"] == version
        assert Path(location).resolve().is_relative_to(venv.resolve())
        assert not list((work / ".proofbeak/storage").iterdir())
        run([cli, "suite", "example", "--output", "suite.json"])
        run([cli, "suite", "validate", "suite.json"])
        run([cli, "suite", "run", "suite.json", "--key", "installed-suite", "--output", "suite-report.json",
             "--html", "suite.html", "--junit", "junit.xml", "--summary", "summary.md"])
        suite = json.loads((work / "suite-report.json").read_text())
        assert suite["gate"] == "pass" and len(suite["entries"]) == 3
        assert json.loads(run([cli, "gate", "suite-report.json"]))["gate"] == "pass"
        assert json.loads(run([cli, "diff", "suite-report.json", "suite-report.json"]))["gate"] == "pass"
        assert all((work / name).stat().st_size for name in ("suite.html", "junit.xml", "summary.md"))
        result = {"version": version, "python": sys.version.split()[0], "core": "pass", "installed_module": location,
                  "source_checkout_imported": False, "html_export": (work / "report.html").is_file(), "suite_executions": 3}
        if not agents:
            return result
        run([cli, "keys", "generate", "--private", "identity/private.json", "--public", "identity/public.json"])
        run([cli, "bundle", "create", "suite-report.json", "--key", "identity/private.json", "--output", "suite.bundle.json"])
        signed = json.loads(run([cli, "bundle", "verify", "suite.bundle.json", "--public-key", "identity/public.json"]))
        assert signed["signature_valid"] and signed["content_integrity_valid"] and signed["acceptance_gate"] == "pass"
        run([python, "-I", "-c", "from importlib.resources import files; from pathlib import Path; "
             "from proofbeak.studio import initialize; assets=files('proofbeak').joinpath('web'); "
             "assert all(assets.joinpath(n).is_file() for n in ('index.html','app.js','style.css','mark.svg')); "
             "p,t=initialize(Path('studio')); assert t and p.is_file(); assert initialize(Path('studio')) == (p,t)"])
        result.update(signed_bundle="pass", studio_setup="pass", workspace_assets="pass")
        (work / "providers.json").write_text(json.dumps({"scenario:filesystem": {}, "capability:sqlite": {"kind":"sqlite","connection":{}}}))
        initialized = run([server, "init", "--config", "server.json", "--state", "state", "--providers", "providers.json"])
        token = next(line.removeprefix("API key (shown once): ") for line in initialized.splitlines()
                     if line.startswith("API key (shown once): "))
        run([server, "check", "--config", "server.json", "--require-providers"])
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        with (work / "server.log").open("w+") as log:
            api = subprocess.Popen([str(server), "serve", "--config", "server.json", "--port", str(port)], cwd=work,
                env=environment, stdout=subprocess.DEVNULL, stderr=log)
            try:
                endpoint = f"http://127.0.0.1:{port}"
                deadline = time.monotonic() + 20
                while True:
                    try:
                        with urlopen(endpoint + "/health", timeout=1):
                            break
                    except (HTTPError, URLError, OSError):
                        assert api.poll() is None and time.monotonic() < deadline, "installed API did not become ready"
                        time.sleep(.05)
                (work / "mcp_check.py").write_text(MCP_CHECK)
                with urlopen(endpoint + "/ui/", timeout=5) as page:
                    assert b"Proofbeak" in page.read()
                with urlopen(endpoint + "/ui/assets/app.js", timeout=5) as asset:
                    assert asset.headers.get_content_type() in {"text/javascript", "application/javascript"}
                completed = subprocess.run([str(python), "-I", "mcp_check.py"], cwd=work,
                    env={**environment, "PROOFBEAK_API_URL": endpoint, "PROOFBEAK_API_TOKEN": token},
                    capture_output=True, text=True, timeout=45)
                assert completed.returncode == 0, "installed MCP workflow failed"
                result["interfaces"] = json.loads(completed.stdout.splitlines()[-1])
                assert token not in json.dumps(result)
            finally:
                api.terminate()
                api.wait(timeout=10)
        return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("inspect", "core", "agents"))
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = inspect_artifacts(args.dist) if args.mode == "inspect" else installation(args.dist, args.mode == "agents")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
