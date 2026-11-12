"""Local single-owner CLI; no hosted endpoint or implicit credentials."""

import argparse
import json
import os
import sys
from pathlib import Path

from proofbeak import __version__
from proofbeak.adapters.radicale import RadicaleAdapter
from proofbeak.adapters.simulation import SimulationAdapter
from proofbeak.engine import recover_cleanup, run_trial
from proofbeak.models import Trial
from proofbeak.report import render_html
from proofbeak.store import IdempotencyConflict, Store


def adapters_for(names):
    result = []
    for name in names:
        if name == "radicale":
            result.append(RadicaleAdapter(
                os.environ.get("PROOFBEAK_RADICALE_URL", "http://127.0.0.1:5232"),
                os.environ.get("PROOFBEAK_RADICALE_USERNAME", "proofbeak"),
                os.environ.get("PROOFBEAK_RADICALE_PASSWORD", "")))
        elif name.startswith("simulation:"):
            result.append(SimulationAdapter(name.split(":", 1)[1]))
        else:
            raise ValueError("unknown candidate; choose radicale or simulation:<mode>")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(prog="proofbeak", description="Try before agents choose.")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="validate a closed trial definition")
    validate.add_argument("spec", type=Path)
    run = commands.add_parser("run", help="run a local acceptance trial")
    run.add_argument("spec", type=Path)
    run.add_argument("--candidate", action="append", required=True)
    run.add_argument("--key", required=True, help="stable key; a new trial requires a new key")
    run.add_argument("--output", type=Path, default=Path("reports/trial.json"))
    run.add_argument("--html", type=Path)
    cleanup = commands.add_parser("cleanup", help="retry pending cleanup without replaying trials")
    cleanup.add_argument("--candidate", action="append", required=True)
    for subcommand in (run, cleanup):
        subcommand.add_argument("--db", type=Path, default=Path(".proofbeak/runs.sqlite3"))
    args = parser.parse_args(argv)
    store = None
    try:
        if args.command == "cleanup":
            adapters = adapters_for(args.candidate)
            store = Store(args.db)
            outcomes = recover_cleanup(store, adapters)
            print(json.dumps(outcomes, indent=2))
            return 0 if all(o["status"] == "cleaned" for o in outcomes) else 2
        if args.spec.stat().st_size > 65536:
            raise ValueError("trial definition exceeds 64 KiB")
        spec = json.loads(args.spec.read_text())
        trial = Trial.parse(spec)
        trial.fixture("validation")
        if args.command == "validate":
            print("Valid: calendar.move_one.v1")
            return 0
        adapters = adapters_for(args.candidate)
        store = Store(args.db)
        report = run_trial(spec, adapters, store, args.key)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        if args.html:
            args.html.parent.mkdir(parents=True, exist_ok=True)
            args.html.write_text(render_html(report))
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
    except (ValueError, OSError) as exc:
        print(f"Invalid input: {exc}", file=sys.stderr)
        return 3
    finally:
        if store:
            store.close()
