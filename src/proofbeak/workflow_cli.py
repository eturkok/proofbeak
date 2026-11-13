"""Suite and regression CLI boundaries sharing the existing adapters and private exports."""

import json
from pathlib import Path

from proofbeak import ci
from proofbeak.regression import compare_reports
from proofbeak.report import write_private
from proofbeak.suites import example_suite, exit_code, expand_suite, run_suite


def add_commands(commands):
    connector = commands.add_parser("connector", help="scaffold and qualify trusted installed connector packages")
    actions = connector.add_subparsers(dest="connector_command", required=True)
    actions.add_parser("list", help="list installed entry points without loading their code")
    init = actions.add_parser("init")
    init.add_argument("directory", type=Path)
    init.add_argument("--name", required=True)
    qualify = actions.add_parser("qualify")
    qualify.add_argument("spec", type=Path)
    qualify.add_argument("--providers", type=Path, required=True)
    qualify.add_argument("--candidate", required=True)
    qualify.add_argument("--output", type=Path, required=True)
    suite = commands.add_parser("suite", help="compose, validate and run acceptance test packages")
    actions = suite.add_subparsers(dest="suite_command", required=True)
    example = actions.add_parser("example", help="create an editable parameterized suite")
    example.add_argument("--output", type=Path, required=True)
    for name in ("validate", "run"):
        sub = actions.add_parser(name)
        sub.add_argument("spec", type=Path)
        sub.add_argument("--providers", type=Path)
        sub.add_argument("--json", action="store_true")
        if name == "run":
            sub.add_argument("--key", required=True)
            sub.add_argument("--db", type=Path, default=Path(".proofbeak/runs.sqlite3"))
            sub.add_argument("--output", type=Path, default=Path("reports/suite.json"))
            sub.add_argument("--html", type=Path)
            sub.add_argument("--junit", type=Path)
            sub.add_argument("--summary", type=Path)
    diff = commands.add_parser("diff", help="compare two recorded trial or suite reports")
    diff.add_argument("baseline", type=Path)
    diff.add_argument("current", type=Path)
    diff.add_argument("--allow-provider-version-change", action="store_true")
    diff.add_argument("--output", type=Path)
    gate = commands.add_parser("gate", help="evaluate a stored report and export JUnit/GitHub summaries")
    gate.add_argument("report", type=Path)
    gate.add_argument("--junit", type=Path)
    gate.add_argument("--summary", type=Path)


def read_report(path, limit=16 * 1024 * 1024):
    if path.stat().st_size > limit:
        raise ValueError("The JSON report exceeds the input limit.")
    from proofbeak.server import read_json_file
    if limit == 65536:
        return read_json_file(path)
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate report key.")
            result[key] = value
        return result
    return json.loads(path.read_text(), object_pairs_hook=unique)


def outputs_safe(outputs, inputs):
    paths = [p.resolve() for p in outputs if p]
    protected = {p.resolve() for p in inputs if p}
    if len(set(paths)) != len(paths) or any(p in protected for p in paths):
        raise ValueError("Choose distinct export paths outside input, configuration and state files.")


def main(args):
    if args.command == "connector":
        from proofbeak.connector_sdk import list_plugins, qualify, scaffold
        if args.connector_command == "list":
            print(json.dumps({"plugins": list_plugins()}))
            return 0
        if args.connector_command == "init":
            print(json.dumps(scaffold(args.directory, args.name)))
            return 0
        outputs_safe([args.output], [args.spec, args.providers])
        from proofbeak.cli import adapters_for
        adapter = adapters_for([args.candidate], args.providers)[0]
        if not hasattr(adapter, "backend"):
            raise ValueError("Choose a capability connector candidate.")
        report = qualify(adapter.backend, read_report(args.spec, 65536))
        write_private(args.output, json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        print(json.dumps({"gate": report["gate"], "output": str(args.output), "cleanup_ledger": report["cleanup_ledger"]}))
        return exit_code(report)
    if args.command == "suite":
        if args.suite_command == "example":
            write_private(args.output, json.dumps(example_suite(), indent=2, ensure_ascii=False) + "\n", overwrite=False)
            print(f"Created: {args.output}")
            return 0
        definition = read_report(args.spec, 65536)
        plan = expand_suite(definition)
        from proofbeak.cli import adapters_for
        from proofbeak.packs import builtins
        from proofbeak.validation import candidate_issues
        for entry in plan:
            pack, trial = builtins().prepare(entry["test"])
            if candidate_issues(pack, trial, adapters_for(entry["candidates"], args.providers)):
                raise ValueError("A configured candidate cannot run a suite case.")
        if args.suite_command == "validate":
            print(json.dumps({"valid": True, "executions": len(plan), "provider_work_performed": False}))
            return 0
        import os
        providers = args.providers or Path(os.environ.get("PROOFBEAK_PROVIDERS_FILE", "providers.json"))
        state_paths = [args.db, args.db.with_suffix(args.db.suffix + ".lock")]
        state_paths += [Path(str(args.db) + suffix) for suffix in ("-journal", "-wal", "-shm")]
        outputs_safe([args.output, args.html, args.junit, args.summary], [args.spec, providers, *state_paths])
        from proofbeak.store import Store
        store = Store(args.db)
        try:
            report = run_suite(definition, lambda names: adapters_for(names, args.providers), store, args.key)
        finally:
            store.close()
        write_private(args.output, json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        if args.html:
            write_private(args.html, ci.render_suite_html(report))
        for path, render in ((args.junit, ci.junit), (args.summary, ci.markdown)):
            if path:
                write_private(path, render(report))
        print(json.dumps({"suite_id": report.get("suite_id", report.get("run_id")), **ci.gate(report)}))
        return exit_code(ci.gate(report))
    if args.command == "diff":
        outputs_safe([args.output], [args.baseline, args.current])
        result = compare_reports(read_report(args.baseline), read_report(args.current), args.allow_provider_version_change)
        if args.output:
            write_private(args.output, json.dumps(result, indent=2, ensure_ascii=False) + "\n")
        print(json.dumps(result, ensure_ascii=False))
        return exit_code(result)
    report = read_report(args.report)
    outputs_safe([args.junit, args.summary], [args.report])
    for path, render in ((args.junit, ci.junit), (args.summary, ci.markdown)):
        if path:
            write_private(path, render(report))
    result = ci.gate(report)
    print(json.dumps(result))
    return exit_code(result)
