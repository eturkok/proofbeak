"""Local single-owner CLI; no hosted endpoint or implicit credentials."""

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

from proofbeak import __version__
from proofbeak.adapters.filesystem import FilesystemAdapter
from proofbeak.adapters.google import GoogleCalendarAdapter
from proofbeak.adapters.radicale import RadicaleAdapter
from proofbeak.adapters.simulation import SimulationAdapter
from proofbeak.adapters.webdav import WebDAVAdapter
from proofbeak.adapters.scenario import JSONReadConnector, ScenarioAdapter
from proofbeak.engine import recover_cleanup, run_trial
from proofbeak.models import fingerprint
from proofbeak.packs import builtins
from proofbeak.report import render_html, write_private
from proofbeak.store import IdempotencyConflict, Store
from proofbeak.validation import (TrialValidationError, candidate_issues, issue_from,
                                  issue_location, validation_result)


def adapters_for(names, profiles=None):
    result = []
    for name in names:
        if name.startswith("capability:"):
            from proofbeak.connectors import configured_connector
            from proofbeak.server import read_json_file
            definitions = read_json_file(profiles or os.environ.get("PROOFBEAK_PROVIDERS_FILE", "providers.json"))
            if name not in definitions:
                raise ValueError("candidate is missing from the operator provider file")
            result.append(configured_connector(name, definitions[name], Path(".proofbeak"), "cli")())
        elif name.startswith("scenario:"):
            inner = name[len("scenario:"):]
            if inner.startswith("http:"):
                alias = inner.split(":", 1)[1].upper().replace("-", "_")
                prefix = "PROOFBEAK_HTTP_" + alias
                routes = os.environ.get(prefix + "_ROUTES", "{}")
                if len(routes.encode()) > 16384:
                    raise ValueError("oversized HTTP route profile")
                backend = JSONReadConnector(inner, os.environ.get(prefix + "_URL", ""), json.loads(routes))
            elif inner == "filesystem" or inner.startswith("webdav:"):
                backend = adapters_for([inner])[0]
            else:
                raise ValueError("unregistered scenario connector")
            result.append(ScenarioAdapter(backend))
        elif name == "filesystem":
            result.append(FilesystemAdapter(os.environ.get(
                "PROOFBEAK_FILESYSTEM_ROOT", ".proofbeak/storage")))
        elif name == "google-calendar":
            result.append(GoogleCalendarAdapter(
                os.environ.get("PROOFBEAK_GOOGLE_ACCESS_TOKEN", ""),
                os.environ.get("PROOFBEAK_GOOGLE_TEST_ACCOUNT", "")))
        elif name == "radicale":
            result.append(RadicaleAdapter(
                os.environ.get("PROOFBEAK_RADICALE_URL", "http://127.0.0.1:5232"),
                os.environ.get("PROOFBEAK_RADICALE_USERNAME", "proofbeak"),
                os.environ.get("PROOFBEAK_RADICALE_PASSWORD", "")))
        elif name.startswith("simulation:"):
            result.append(SimulationAdapter(name.split(":", 1)[1]))
        elif name.startswith("webdav:"):
            alias = name.split(":", 1)[1].upper().replace("-", "_")
            prefix = "PROOFBEAK_WEBDAV_" + alias
            result.append(WebDAVAdapter(name, os.environ.get(prefix + "_URL", ""),
                                       os.environ.get(prefix + "_USERNAME", "proofbeak"),
                                       os.environ.get(prefix + "_PASSWORD", "")))
        else:
            raise ValueError("unknown candidate; choose filesystem, webdav:<alias>, radicale, google-calendar, or simulation:<mode>")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(prog="proofbeak", description="Try before agents choose.")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("packs", help="list registered trial packages and versions")
    example = commands.add_parser("example", help="create an editable definition without running a trial")
    example.add_argument("--output", type=Path, required=True)
    example.add_argument("--trial", default="scenario.v1", help="registered format to use as a starting point")
    imported = commands.add_parser("import-openapi", help="import explicitly selected OpenAPI JSON operation bindings")
    imported.add_argument("spec", type=Path)
    imported.add_argument("--operation", action="append", required=True)
    imported.add_argument("--output", type=Path, required=True)
    commands.add_parser("google-preflight", help="read-only Google test-account identity check")
    validate = commands.add_parser("validate", help="validate a closed trial definition")
    validate.add_argument("spec", type=Path)
    validate.add_argument("--candidate", action="append", help="also validate configured candidate operations without execution")
    validate.add_argument("--json", action="store_true", help="return structured authoring diagnostics")
    run = commands.add_parser("run", help="run a local acceptance trial")
    run.add_argument("spec", type=Path)
    run.add_argument("--candidate", action="append", required=True)
    run.add_argument("--key", required=True, help="stable key; a new trial requires a new key")
    run.add_argument("--output", type=Path, default=Path("reports/trial.json"))
    run.add_argument("--html", type=Path)
    cleanup = commands.add_parser("cleanup", help="retry pending cleanup without replaying trials")
    cleanup.add_argument("--candidate", action="append", required=True)
    for subcommand in (validate, run, cleanup):
        subcommand.add_argument("--providers", type=Path, help="operator-owned capability provider map")
    for subcommand in (run, cleanup):
        subcommand.add_argument("--db", type=Path, default=Path(".proofbeak/runs.sqlite3"))
    args = parser.parse_args(argv)
    store = None
    try:
        if args.command == "packs":
            print(json.dumps(builtins().describe(), indent=2))
            return 0
        if args.command == "example":
            registry = builtins()
            pack = registry.packs.get(args.trial)
            if pack is None:
                raise ValueError("choose a registered trial format")
            spec = pack.example()
            registry.prepare(spec)
            write_private(args.output, json.dumps(spec, indent=2) + "\n", overwrite=False)
            print(f"Created: {args.output}")
            return 0
        if args.command == "import-openapi":
            from proofbeak.connectors.rest import openapi_operations
            if args.spec.stat().st_size > 1048576:
                raise ValueError("OpenAPI input exceeds 1 MiB")
            operations = openapi_operations(json.loads(args.spec.read_text()), args.operation)
            write_private(args.output, json.dumps(operations, indent=2) + "\n", overwrite=False)
            print(f"Created: {args.output}; review scope, lifecycle and credentials in the provider profile")
            return 0
        if args.command == "google-preflight":
            from proofbeak.adapters.base import ProviderError

            try:
                print(json.dumps(adapters_for(["google-calendar"])[0].preflight(), indent=2))
                return 0
            except ProviderError as exc:
                print(f"Inconclusive: {exc.reason}", file=sys.stderr)
                return 2
        if args.command == "cleanup":
            adapters = adapters_for(args.candidate, args.providers)
            store = Store(args.db)
            outcomes = recover_cleanup(store, adapters)
            print(json.dumps(outcomes, indent=2))
            return 0 if all(o["status"] == "cleaned" for o in outcomes) else 2
        if args.spec.stat().st_size > 65536:
            raise ValueError("trial definition exceeds 64 KiB")
        spec = json.loads(args.spec.read_text())
        pack, trial = builtins().prepare(spec)
        if args.command == "validate":
            if args.candidate and (len(args.candidate) > 4 or len(set(args.candidate)) != len(args.candidate)):
                raise TrialValidationError("choose one to four distinct candidates", code="invalid_candidates")
            issues = candidate_issues(pack, trial, adapters_for(args.candidate, args.providers)) if args.candidate else []
            if args.json:
                print(json.dumps(validation_result(issues, pack_id=pack.id, pack_version=pack.version,
                                                   test_hash=fingerprint(spec), candidates=args.candidate or [])))
            elif issues:
                for issue in issues:
                    print(f'Invalid input for {issue["candidate"]} at {issue_location(issue)}: {issue["message"]}',
                          file=sys.stderr)
            else:
                print(f"Valid: {pack.id} (package {pack.version})")
            return 3 if issues else 0
        if args.html and args.output.resolve() == args.html.resolve():
            raise ValueError("JSON and HTML output paths must differ")
        protected = {args.spec.resolve(), args.db.resolve(), args.db.with_suffix(args.db.suffix + ".lock").resolve()}
        protected.update(Path(str(args.db) + suffix).resolve() for suffix in ("-journal", "-wal", "-shm"))
        if args.providers:
            protected.add(args.providers.resolve())
        elif any(name.startswith("capability:") for name in args.candidate):
            protected.add(Path(os.environ.get("PROOFBEAK_PROVIDERS_FILE", "providers.json")).resolve())
        if any(path.resolve() in protected for path in (args.output, args.html) if path is not None):
            raise ValueError("report output must not replace trial or state files")
        adapters = adapters_for(args.candidate, args.providers)
        store = Store(args.db)
        report = run_trial(spec, adapters, store, args.key)
        write_private(args.output, json.dumps(report, indent=2) + "\n")
        if args.html:
            write_private(args.html, render_html(report))
        print(f'Run {report["run_id"]} · {report["state"]}')
        for candidate in report["candidates"]:
            print(f'{candidate["provider"]}: {candidate["verdict"]} '
                  f'({candidate["reason_code"]}); cleanup={candidate["cleanup_status"]}')
        if report["state"] != "completed":
            return 2
        if any(c["cleanup_status"] != "cleaned" or c["verdict"] == "inconclusive"
               for c in report["candidates"]):
            return 2
        return 1 if any(c["verdict"] == "fail" for c in report["candidates"]) else 0
    except IdempotencyConflict as exc:
        print(f"Conflict: {exc}", file=sys.stderr)
        return 3
    except sqlite3.Error:
        print("State unavailable; repair the store and retry with the same key.", file=sys.stderr)
        return 2
    except (ValueError, TypeError, RecursionError, OSError) as exc:
        issue = issue_from(exc)
        if args.command == "validate" and args.json:
            print(json.dumps(validation_result([issue])))
        else:
            print(f'Invalid input at {issue_location(issue)}: {issue["message"]}', file=sys.stderr)
        return 3
    finally:
        if store:
            store.close()
