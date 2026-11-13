"""Authorized raw exports and operator-key signing shared by HTTP and the console."""

import json
import os
from pathlib import Path

from proofbeak.bundles import create_bundle, public_record, read_key
from proofbeak.service import ServiceError


def key_path():
    value = os.environ.get("PROOFBEAK_SIGNING_KEY_FILE")
    return Path(value) if value else None


def signing_status():
    path = key_path()
    if path is None:
        return {"configured": False, "reason_code": "signing_not_configured"}
    try:
        key = read_key(path, private=True)
    except (ValueError, OSError):
        return {"configured": False, "reason_code": "signing_key_unavailable"}
    return {"configured": True, "public_key": public_record(key.public_key()),
            "claim": "Signatures authenticate records, not provider capabilities."}


def portable_report(service, tenant, identity, kind="trial"):
    if kind == "suite":
        report = service.get_suite(tenant, identity)
        if report.get("raw_purged_at"):
            raise ServiceError("raw_evidence_purged", 410)
        for entry in report["entries"]:
            if entry.get("report"):
                entry["report"] = portable_report(service, tenant, entry["run_id"])
        return report
    row = service._row(tenant, identity)
    if row["raw_purged_at"]:
        raise ServiceError("raw_evidence_purged", 410)
    if row["result"] is None:
        raise ServiceError("result_not_available", 409)
    report = json.loads(row["result"])
    for index, candidate in enumerate(report["candidates"]):
        evidence = service.evidence(tenant, f"{identity}:{index}")
        candidate["validity"] = evidence["validity"]
    return report


def signed_export(service, tenant, identity, kind="trial"):
    # Authorize the record before exposing operator signing configuration.
    report = portable_report(service, tenant, identity, kind)
    path = key_path()
    if path is None:
        raise ServiceError("signing_not_configured", 409)
    try:
        return create_bundle(report, path)
    except (ValueError, OSError):
        raise ServiceError("bundle_export_unavailable", 409) from None
