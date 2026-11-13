"""Explicit key generation, portable signing and independently pinned verification."""

import json
from pathlib import Path

from proofbeak.bundles import create_bundle, generate_keys, verify_bundle
from proofbeak.report import write_private
from proofbeak.workflow_cli import outputs_safe, read_report


def add_commands(commands):
    keys = commands.add_parser("keys", help="manage operator-owned evidence signing keys")
    actions = keys.add_subparsers(dest="key_command", required=True)
    generate = actions.add_parser("generate")
    generate.add_argument("--private", type=Path, required=True)
    generate.add_argument("--public", type=Path, required=True)
    bundle = commands.add_parser("bundle", help="sign and verify portable raw evidence")
    actions = bundle.add_subparsers(dest="bundle_command", required=True)
    create = actions.add_parser("create")
    create.add_argument("report", type=Path)
    create.add_argument("--key", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)
    verify = actions.add_parser("verify")
    verify.add_argument("bundle", type=Path)
    verify.add_argument("--public-key", type=Path, required=True)


def main(args):
    if args.command == "keys":
        public = generate_keys(args.private, args.public)
        print(json.dumps({"key_id": public["key_id"], "public_key_file": str(args.public),
                          "private_key_file": str(args.private)}))
    elif args.bundle_command == "create":
        outputs_safe([args.output], [args.report, args.key])
        bundle = create_bundle(read_report(args.report), args.key)
        write_private(args.output, json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", overwrite=False)
        print(json.dumps({"created": str(args.output), "key_id": bundle["payload"]["signer"]["key_id"]}))
    else:
        print(json.dumps(verify_bundle(read_report(args.bundle), args.public_key)))
    return 0
