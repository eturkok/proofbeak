"""Operator-owned provider configuration; request bodies never contain credentials or URLs."""

import argparse
import hashlib
import json
import os
import re
import secrets
from pathlib import Path

from proofbeak.adapters.filesystem import FilesystemAdapter
from proofbeak.adapters.google import GoogleCalendarAdapter
from proofbeak.adapters.radicale import RadicaleAdapter
from proofbeak.adapters.simulation import SimulationAdapter
from proofbeak.adapters.webdav import WebDAVAdapter
from proofbeak.models import keys
from proofbeak.executor import SubprocessExecutor, WorkerLimits
from proofbeak.service import Tenant, TrialService


def provider_factory(name, settings, root, tenant_id):
    if name == "filesystem":
        keys(settings, set(), "filesystem settings")
        return lambda: FilesystemAdapter(root / "provider-data" / tenant_id / "objects")
    if name.startswith("simulation:"):
        keys(settings, set(), "simulation settings")
        mode = name.split(":", 1)[1]
        SimulationAdapter(mode)
        return lambda: SimulationAdapter(mode)
    if name.startswith("webdav:"):
        keys(settings, {"endpoint", "username", "password_env"}, "WebDAV settings")
        WebDAVAdapter(name, settings["endpoint"], settings["username"], "profile-validation")
        credential_variables(settings, "password_env")
        return lambda: WebDAVAdapter(name, settings["endpoint"], settings["username"],
                                    os.environ.get(settings["password_env"], ""))
    if name == "radicale":
        keys(settings, {"endpoint", "username", "password_env"}, "Radicale settings")
        RadicaleAdapter(settings["endpoint"], settings["username"], "profile-validation")
        credential_variables(settings, "password_env")
        return lambda: RadicaleAdapter(settings["endpoint"], settings["username"],
                                        os.environ.get(settings["password_env"], ""))
    if name == "google-calendar":
        keys(settings, {"access_token_env", "test_account_env"}, "Google settings")
        credential_variables(settings, "access_token_env", "test_account_env")
        return lambda: GoogleCalendarAdapter(os.environ.get(settings["access_token_env"], ""),
                                              os.environ.get(settings["test_account_env"], ""))
    raise ValueError("unknown configured provider")


def credential_variables(settings, *names):
    for name in names:
        if not isinstance(settings[name], str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", settings[name]):
            raise ValueError("invalid credential environment variable")


def load_service(config_path):
    path = Path(config_path)
    if path.stat().st_size > 65536:
        raise ValueError("server configuration exceeds 64 KiB")
    config = json.loads(path.read_text())
    keys(config, {"state_dir", "tenants"} | ({"execution"} if "execution" in config else set()),
         "server configuration")
    root = Path(config["state_dir"]).resolve()
    tenants = []
    for entry in config["tenants"]:
        keys(entry, {"id", "token_sha256", "providers"}, "tenant")
        if not isinstance(entry["id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", entry["id"]):
            raise ValueError("invalid tenant id")
        providers = {name: provider_factory(name, settings, root, entry["id"])
                     for name, settings in entry["providers"].items()}
        tenants.append(Tenant(entry["id"], entry["token_sha256"], providers))
    limits = WorkerLimits(**config.get("execution", {}))
    return TrialService(root, tenants, executor=SubprocessExecutor(limits))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="proofbeak-server")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="create a local tenant and print its key once")
    init.add_argument("--config", type=Path, default=Path(".proofbeak/server.json"))
    init.add_argument("--state", type=Path, default=Path(".proofbeak/server"))
    init.add_argument("--tenant", default="demo")
    init.add_argument("--providers", type=Path,
                      help="import an operator-owned registered provider map; default is filesystem only")
    serve = commands.add_parser("serve", help="run one REST coordinator (loopback by default)")
    serve.add_argument("--config", type=Path, default=Path(".proofbeak/server.json"))
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--host", choices=["127.0.0.1", "0.0.0.0", "::1"], default="127.0.0.1")
    maintain = commands.add_parser("maintain", help="apply local evidence retention while the API is stopped")
    maintain.add_argument("--config", type=Path, default=Path(".proofbeak/server.json"))
    purge = commands.add_parser("purge-tenant", help="erase a tenant's stored data after resource cleanup")
    purge.add_argument("--config", type=Path, default=Path(".proofbeak/server.json"))
    purge.add_argument("--tenant", required=True)
    args = parser.parse_args(argv)
    if args.command == "init":
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args.tenant):
            parser.error("invalid tenant id")
        providers = {"filesystem": {}}
        if args.providers:
            try:
                if args.providers.stat().st_size > 65536:
                    raise ValueError("oversized provider map")
                providers = json.loads(args.providers.read_text())
                if not isinstance(providers, dict) or not providers or len(providers) > 32:
                    raise ValueError("invalid provider map")
                for name, settings in providers.items():
                    if not isinstance(name, str) or not isinstance(settings, dict):
                        raise ValueError("invalid provider map")
                    provider_factory(name, settings, args.state.resolve(), args.tenant)
            except (ValueError, TypeError, OSError):
                parser.error("provider file must contain one to 32 registered provider profiles, at most 64 KiB")
        token = secrets.token_urlsafe(32)
        config = {"state_dir": str(args.state.resolve()), "tenants": [{
            "id": args.tenant, "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
            "providers": providers}]}
        if len(json.dumps(config).encode()) > 65536:
            parser.error("combined server configuration exceeds 64 KiB")
        args.config.parent.mkdir(parents=True, exist_ok=True)
        # Never overwrite an existing tenant configuration or issue a token silently.
        descriptor = os.open(args.config, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as destination:
            destination.write(json.dumps(config, indent=2) + "\n")
        print(f"Tenant: {args.tenant}\nAPI key (shown once): {token}")
        return 0
    if args.command in {"maintain", "purge-tenant"}:
        service = load_service(args.config)
        service.start(background=False)
        try:
            result = service.maintain() if args.command == "maintain" else service.purge_tenant(args.tenant)
            print(json.dumps(result, indent=2))
        finally:
            service.close()
        return 0
    try:
        import uvicorn

        from proofbeak.interfaces.http import create_app
    except ImportError:
        parser.error("install proofbeak[agents] to run the optional agent interfaces")
    service = load_service(args.config)
    uvicorn.run(create_app(service), host=args.host, port=args.port, workers=1,
                access_log=False, timeout_graceful_shutdown=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
