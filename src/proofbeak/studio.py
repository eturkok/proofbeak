"""An explicit local-owner launcher and provider configuration boundary for the web console."""

import hashlib
import json
import os
import secrets
from pathlib import Path
from threading import Lock, Timer

from proofbeak.bundles import generate_keys
from proofbeak.models import canonical
from proofbeak.report import write_private
from proofbeak.server import load_service, provider_factory, read_json_file
from proofbeak.service import ServiceError


def initialize(directory):
    directory = Path(directory).resolve()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink() or directory.stat().st_mode & 0o077:
        raise ValueError("The local studio directory must be private to its owner (0700).")
    config_path, access_path = directory / "server.json", directory / "access-key"
    if config_path.exists():
        config = read_json_file(config_path)
        token = None
        if access_path.is_file() and not access_path.is_symlink() and not access_path.stat().st_mode & 0o077:
            value = access_path.read_text().strip()
            if len(config.get("tenants", [])) == 1 and hashlib.sha256(value.encode()).hexdigest() == config["tenants"][0]["token_sha256"]:
                token = value
        return config_path, token
    token = secrets.token_urlsafe(32)
    config = {"state_dir": str(directory / "state"), "tenants": [{"id": "local", "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
        "providers": {"scenario:filesystem": {}, "capability:sqlite": {"kind": "sqlite", "connection": {}}}}]}
    # This launcher is an explicit local-owner setup; normal server init keeps its existing contract.
    for name, profile in config["tenants"][0]["providers"].items():
        provider_factory(name, profile, directory / "state", "local")
    private, public = directory / "signing-private.json", directory / "signing-public.json"
    generate_keys(private, public)
    write_private(access_path, token + "\n", overwrite=False)
    write_private(config_path, json.dumps(config, indent=2) + "\n", overwrite=False)
    return config_path, token


class StudioProviders:
    """Only the loopback local-owner launcher enables this configuration capability."""
    def __init__(self, service, config_path):
        self.service, self.config_path = service, Path(config_path)
        self.lock = Lock()

    def add(self, owner, name, settings):
        with self.lock:
            config = read_json_file(self.config_path)
            if len(config["tenants"]) != 1 or config["tenants"][0]["id"] != owner:
                raise ServiceError("operator_configuration_required", 403)
            providers = config["tenants"][0]["providers"]
            if name in providers:
                raise ServiceError("provider_already_configured", 409)
            if len(providers) >= 32:
                raise ServiceError("provider_limit_reached", 422)
            try:
                factory = provider_factory(name, settings, self.service.root, owner)
            except (ValueError, TypeError, KeyError, OSError):
                raise ServiceError("invalid_provider_profile", 422) from None
            providers[name] = settings
            if len(canonical(config).encode()) > 65536:
                raise ServiceError("configuration_too_large", 413)
            write_private(self.config_path, json.dumps(config, indent=2) + "\n")
            self.service.tenants[owner].providers[name] = factory
            self.service.record_event(owner, "provider_configured", detail={"provider": name})
            return {"configured": True, "provider": name, "availability_checked": False}


def add_command(commands):
    studio = commands.add_parser("studio", help="start the local web workspace with disposable filesystem and SQLite candidates")
    studio.add_argument("--directory", type=Path, default=Path(".proofbeak/studio"))
    studio.add_argument("--port", type=int, default=8765)
    studio.add_argument("--no-browser", action="store_true")


def main(args):
    try:
        import uvicorn
        from proofbeak.interfaces.http import create_app
        from proofbeak.capabilities import validator
        validator({"type": "object"})
    except ImportError:
        raise ValueError("Install the web workspace: python -m pip install 'proofbeak[agents,connectors]'.") from None
    if not 1 <= args.port <= 65535:
        raise ValueError("Choose a port between 1 and 65535.")
    config_path, token = initialize(args.directory)
    directory = config_path.parent
    if "PROOFBEAK_SIGNING_KEY_FILE" not in os.environ and (directory / "signing-private.json").is_file():
        os.environ["PROOFBEAK_SIGNING_KEY_FILE"] = str(directory / "signing-private.json")
    service = load_service(config_path)
    url = f"http://127.0.0.1:{args.port}/ui/"
    print(f"Proofbeak workspace: {url}", flush=True)
    if token:
        print(f"Local access key: {token}\nKeep this operator key private. It is stored owner-only in {directory / 'access-key'}.", flush=True)
    else:
        print("Sign in with your operator-issued tenant key.", flush=True)
    if not args.no_browser:
        import webbrowser
        opener = Timer(1, webbrowser.open, args=[url])
        opener.daemon = True
        opener.start()
    app = create_app(service, studio_providers=StudioProviders(service, config_path))
    uvicorn.run(app, host="127.0.0.1", port=args.port, workers=1, access_log=False, timeout_graceful_shutdown=5)
    return 0
