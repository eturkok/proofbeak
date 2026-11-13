"""Signed portable observation records. A trusted signer is not a provider certification."""

import base64
import hashlib
import json
import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

from proofbeak.ci import entries, gate
from proofbeak.models import canonical, fingerprint
from proofbeak.report import write_private

FORMAT = "proofbeak.bundle.v1"
LIMIT = 16 * 1024 * 1024


def crypto():
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
    except ImportError:
        raise ValueError("Install the signing extra: python -m pip install 'proofbeak[signing]'.") from None
    return Ed25519PrivateKey, Ed25519PublicKey


def encoded(value):
    return base64.b64encode(value).decode("ascii")


def decoded(value, size):
    try:
        result = base64.b64decode(value, validate=True)
        if len(result) != size:
            raise ValueError("Wrong key or signature length.")
        return result
    except (ValueError, TypeError):
        raise ValueError("Invalid Ed25519 key or signature encoding.") from None


def public_record(key):
    raw = key.public_bytes_raw()
    return {"algorithm": "Ed25519", "key_id": hashlib.sha256(raw).hexdigest(), "public_key": encoded(raw)}


def generate_keys(private_path, public_path):
    private_path, public_path = Path(private_path), Path(public_path)
    if private_path.resolve() == public_path.resolve() or private_path.exists() or public_path.exists():
        raise ValueError("Choose two new, distinct key paths. Existing keys are never replaced.")
    private_type, _ = crypto()
    private = private_type.generate()
    public = public_record(private.public_key())
    write_private(private_path, canonical({**public, "private_key": encoded(private.private_bytes_raw())}) + "\n", overwrite=False)
    write_private(public_path, canonical(public) + "\n", overwrite=False)
    return public


def read_key(path, private=False):
    if isinstance(path, dict) and not private:
        value = path
        if set(value) != {"algorithm", "key_id", "public_key"} or value["algorithm"] != "Ed25519":
            raise ValueError("Choose a Proofbeak Ed25519 public key document.")
        raw = decoded(value["public_key"], 32)
        if hashlib.sha256(raw).hexdigest() != value["key_id"]:
            raise ValueError("Signing-key fingerprint mismatch.")
        return crypto()[1].from_public_bytes(raw)
    path = Path(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or private and info.st_mode & 0o077:
            raise ValueError("Private signing keys must be owner-only regular files (mode 0600).")
        data = stream.read(16385)
    if len(data) > 16384:
        raise ValueError("Signing key file exceeds 16 KiB.")
    def unique(pairs):
        result = {}
        for name, value in pairs:
            if name in result:
                raise ValueError("Duplicate signing-key field.")
            result[name] = value
        return result
    value = json.loads(data, object_pairs_hook=unique)
    expected = {"algorithm", "key_id", "public_key"} | ({"private_key"} if private else set())
    if not isinstance(value, dict) or set(value) != expected or value["algorithm"] != "Ed25519":
        raise ValueError("Choose a Proofbeak Ed25519 key document.")
    private_type, public_type = crypto()
    raw = decoded(value["public_key"], 32)
    if hashlib.sha256(raw).hexdigest() != value["key_id"]:
        raise ValueError("Signing-key fingerprint mismatch.")
    if private:
        key = private_type.from_private_bytes(decoded(value["private_key"], 32))
        if key.public_key().public_bytes_raw() != raw:
            raise ValueError("Private and public signing keys do not match.")
        return key
    return public_type.from_public_bytes(raw)


def check_report(report):
    if not isinstance(report, dict) or len(canonical(report).encode()) > LIMIT:
        raise ValueError("Choose a bounded Proofbeak report.")
    observations = []
    for entry in entries(report):
        child = entry.get("report")
        if child is None:
            continue
        for candidate in child.get("candidates", []):
            if candidate.get("evidence") is None or fingerprint(candidate["evidence"]) != candidate.get("evidence_hash"):
                raise ValueError("Raw evidence is missing or its integrity check failed.")
            if not isinstance(child.get("test"), dict) or fingerprint(child["test"]) != candidate.get("test_hash"):
                raise ValueError("Scenario and evidence test hashes do not match.")
            observations.append(candidate)
    if not observations:
        raise ValueError("The report has no raw provider observations to export.")
    return observations


def create_bundle(report, private_key_path, now=None):
    check_report(report)
    private = read_key(private_key_path, private=True)
    public = public_record(private.public_key())
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("Bundle export needs an aware timestamp.")
    payload = {"created_at": now.astimezone(UTC).isoformat(), "signer": public,
               "report_sha256": fingerprint(report), "report": report,
               "claim": "Signed observation record; no provider certification or trusted timestamp."}
    signed = canonical({"format": FORMAT, "payload": payload}).encode()
    if len(signed) > LIMIT:
        raise ValueError("The portable bundle exceeds 16 MiB.")
    return {"format": FORMAT, "payload": payload, "signature": encoded(private.sign(signed))}


def verify_bundle(bundle, trusted_public_key_path, now=None):
    if not isinstance(bundle, dict) or set(bundle) != {"format", "payload", "signature"} or bundle["format"] != FORMAT:
        raise ValueError("Choose a proofbeak.bundle.v1 document.")
    if len(canonical(bundle).encode()) > LIMIT + 1024:
        raise ValueError("The portable bundle exceeds 16 MiB.")
    payload = bundle["payload"]
    if not isinstance(payload, dict) or set(payload) != {"created_at", "signer", "report_sha256", "report", "claim"}:
        raise ValueError("The signed manifest has an invalid shape.")
    trusted = read_key(trusted_public_key_path)
    public = public_record(trusted)
    if payload["signer"] != public:
        raise ValueError("The signer is not the independently supplied trusted public key.")
    try:
        trusted.verify(decoded(bundle["signature"], 64), canonical({"format": FORMAT, "payload": payload}).encode())
    except Exception:
        raise ValueError("Bundle signature verification failed.") from None
    if fingerprint(payload["report"]) != payload["report_sha256"]:
        raise ValueError("Bundle report hash verification failed.")
    observations = check_report(payload["report"])
    now = now or datetime.now(UTC)
    exported = datetime.fromisoformat(payload["created_at"])
    if now.tzinfo is None or exported.tzinfo is None or exported > now + timedelta(minutes=1):
        raise ValueError("The bundle export timestamp is invalid.")
    expired, revoked, simulated = 0, 0, 0
    for observation in observations:
        try:
            at = datetime.fromisoformat(observation["observed_at"])
            expiry = datetime.fromisoformat(observation["expires_at"])
            if at.tzinfo is None or expiry.tzinfo is None or expiry <= at or at > now + timedelta(minutes=1):
                raise ValueError("Invalid observation timestamp.")
            expired += int(expiry <= now)
        except (ValueError, TypeError, KeyError):
            raise ValueError("A bundle observation has invalid freshness metadata.") from None
        revoked += int("revoked" in observation.get("validity", {}).get("reasons", []))
        simulated += int(observation.get("evidence_level") == "simulation")
    return {"format": FORMAT, "signature_valid": True, "content_integrity_valid": True,
            "trusted_key_id": public["key_id"], "acceptance_gate": gate(payload["report"])["gate"],
            "observations": len(observations), "expired_observations": expired,
            "revoked_at_export": revoked, "simulated_observations": simulated,
            "current_revocation_status": "not_checked_offline", "trusted_timestamp": False,
            "historical_only": bool(expired or revoked or simulated)}
