"""Offline verified state snapshots; restoration never replays accepted provider work."""

import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from proofbeak import __version__
from proofbeak.models import canonical, keys

MAX_FILES = 10000
MAX_BYTES = 512 * 1024 * 1024
MARKER = ".restore-incomplete"


class RecoveryError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def allowed_path(name, directory=False):
    if name == "ledgers" or name == "provider-data":
        return directory
    if name == "queue.sqlite3":
        return not directory
    if re.fullmatch(r"ledgers/[a-f0-9]{32}\.sqlite3", name):
        return not directory
    parts = name.split("/")
    if not 2 <= len(parts) <= 5 or parts[0] != "provider-data" \
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", parts[1]):
        return False
    if len(parts) >= 3 and parts[2] != "objects":
        return False
    if len(parts) >= 4 and not re.fullmatch(r"proofbeak-[a-f0-9]{32}", parts[3]):
        return False
    if len(parts) == 5:
        return not directory and parts[4] not in {".", ".."} \
            and re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", parts[4]) is not None
    return directory


def private_file(path, data):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def file_lock(path):
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RecoveryError("offline_state_required") from None
        yield
    finally:
        os.close(descriptor)


def inventory(root, snapshot=False):
    files, directories = [], []
    for path in sorted(root.rglob("*")):
        name = path.relative_to(root).as_posix()
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
            raise RecoveryError("unexpected_state_entry")
        if snapshot and name == "manifest.json":
            continue
        if not snapshot and (name == "worker.lock" or re.fullmatch(r"ledgers/[a-f0-9]{32}\.sqlite3\.lock", name)):
            continue
        directory = stat.S_ISDIR(mode)
        if not allowed_path(name, directory):
            raise RecoveryError("unexpected_state_entry")
        (directories if directory else files).append(name)
        if len(files) + len(directories) > MAX_FILES:
            raise RecoveryError("snapshot_entry_limit")
    if "queue.sqlite3" not in files:
        raise RecoveryError("state_database_missing")
    return files, directories


def read_regular(path, limit=MAX_BYTES):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise RecoveryError("unexpected_state_entry")
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise RecoveryError("snapshot_size_limit")
    return content


def database(path, queue=False):
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)] \
                or connection.execute("PRAGMA foreign_key_check").fetchall():
            raise RecoveryError("state_integrity_failed")
        required = {"jobs", "tombstones", "audit", "disabled_tokens"} if queue else {"runs", "resources"}
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not required <= tables or connection.execute("PRAGMA user_version").fetchone()[0] != 0:
            raise RecoveryError("unsupported_state_schema")
    except BaseException:
        connection.close()
        raise
    return connection


def private_root(path):
    path = Path(path).absolute()
    if path.is_symlink() or not path.is_dir() or path.stat().st_mode & 0o077:
        raise RecoveryError("private_state_directory_required")
    return path.resolve()


def backup(state, output):
    root = private_root(state)
    output = Path(output).absolute()
    if output.resolve().is_relative_to(root) or (root / MARKER).exists():
        raise RecoveryError("invalid_snapshot_destination_or_state")
    with ExitStack() as locks:
        locks.enter_context(file_lock(root / "worker.lock"))
        files, directories = inventory(root)
        for name in files:
            if name.startswith("ledgers/"):
                locks.enter_context(file_lock((root / name).with_suffix(".sqlite3.lock")))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.mkdir(mode=0o700)  # Exclusive: never replace an existing snapshot.
        try:
            for name in directories:
                (output / name).mkdir(mode=0o700)
            entries, total = [], 0
            for name in files:
                target = output / name
                if name.endswith(".sqlite3"):
                    source = database(root / name, queue=name == "queue.sqlite3")
                    try:
                        private_file(target, b"")
                        destination = sqlite3.connect(target)
                        try:
                            source.backup(destination)
                        finally:
                            destination.close()
                    finally:
                        source.close()
                else:
                    private_file(target, read_regular(root / name, MAX_BYTES - total))
                content = read_regular(target, MAX_BYTES - total)
                total += len(content)
                entries.append({"path": name, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()})
                with target.open("rb") as stream:
                    os.fsync(stream.fileno())
            manifest = {"format": "proofbeak-state.v1", "version": __version__,
                        "created_at": datetime.now(UTC).isoformat(), "state_dir": str(root),
                        "directories": directories, "files": entries}
            private_file(output / "manifest.json", (canonical(manifest) + "\n").encode())
            for name in reversed(directories):
                sync_directory(output / name)
            sync_directory(output)
            sync_directory(output.parent)
        except BaseException:
            shutil.rmtree(output)
            raise
    return {"snapshot": str(output), "files": len(entries), "bytes": total,
            "format": manifest["format"]}


def restore(snapshot, state):
    source = private_root(snapshot)
    target = Path(state).absolute()
    # Validate all inputs before creating the target. No archive extraction or overwrite.
    manifest = json.loads(read_regular(source / "manifest.json", 4 * 1024 * 1024))
    keys(manifest, {"format", "version", "created_at", "state_dir", "directories", "files"}, "snapshot")
    if manifest["format"] != "proofbeak-state.v1" or not isinstance(manifest["files"], list) \
            or not isinstance(manifest["directories"], list):
        raise RecoveryError("unsupported_snapshot_format")
    files, directories = inventory(source, snapshot=True)
    entries, total = {}, 0
    for entry in manifest["files"]:
        keys(entry, {"path", "size", "sha256"}, "snapshot file")
        name = entry["path"]
        if not isinstance(name, str) or not allowed_path(name) or name in entries \
                or type(entry["size"]) is not int or not 0 <= entry["size"] <= MAX_BYTES \
                or not isinstance(entry["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", entry["sha256"]):
            raise RecoveryError("invalid_snapshot_manifest")
        content = read_regular(source / name, MAX_BYTES - total)
        total += len(content)
        if len(content) != entry["size"] or hashlib.sha256(content).hexdigest() != entry["sha256"]:
            raise RecoveryError("snapshot_checksum_failed")
        entries[name] = entry
    if sorted(entries) != files or manifest["directories"] != directories:
        raise RecoveryError("snapshot_inventory_mismatch")
    for name in files:
        if name.endswith(".sqlite3"):
            database(source / name, queue=name == "queue.sqlite3").close()
    if target.resolve().is_relative_to(source):
        raise RecoveryError("invalid_restore_destination")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir(mode=0o700)
    try:
        private_file(target / MARKER, b"restore in progress\n")
        for name in directories:
            (target / name).mkdir(mode=0o700)
        for name in files:
            content = read_regular(source / name, entries[name]["size"])
            if hashlib.sha256(content).hexdigest() != entries[name]["sha256"]:
                raise RecoveryError("snapshot_changed_during_restore")
            private_file(target / name, content)
        now, prefix = datetime.now(UTC).isoformat(), "restore:" + uuid4().hex[:16] + ":"
        connection = sqlite3.connect(target / "queue.sqlite3")
        try:
            with connection:
                connection.execute("CREATE TABLE IF NOT EXISTS service_metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL)")
                connection.execute("INSERT OR REPLACE INTO service_metadata VALUES ('idempotency_key_prefix',?)", (prefix,))
                connection.execute("DROP TRIGGER IF EXISTS restore_key_fence")
                # Enforce the fence in SQLite too: an older coordinator binary must not
                # accidentally reinsert a key absent from this point-in-time snapshot.
                connection.execute("""CREATE TRIGGER restore_key_fence BEFORE INSERT ON jobs
                    WHEN substr(NEW.client_key,1,(SELECT length(value) FROM service_metadata
                         WHERE key='idempotency_key_prefix')) !=
                         (SELECT value FROM service_metadata WHERE key='idempotency_key_prefix')
                    BEGIN SELECT RAISE(ABORT,'restore_key_prefix_required'); END""")
                connection.execute("UPDATE jobs SET state='interrupted',reason='state_restored',completed_at=? "
                                   "WHERE state IN ('queued','running')", (now,))
                for (tenant,) in connection.execute("SELECT DISTINCT tenant FROM jobs"):
                    connection.execute("INSERT INTO audit (tenant,run_id,event,detail,created_at) VALUES (?,NULL,?,?,?)",
                        (tenant, "state_restored", canonical({"idempotency_key_prefix": prefix}), now))
        finally:
            connection.close()
        with (target / "queue.sqlite3").open("rb") as stream:
            os.fsync(stream.fileno())
        for name in reversed(directories):
            sync_directory(target / name)
        sync_directory(target)
        (target / MARKER).unlink()
        sync_directory(target)
        sync_directory(target.parent)
    except BaseException:
        shutil.rmtree(target)
        raise
    return {"restored": True, "state_dir": str(target.resolve()), "idempotency_key_prefix": prefix,
            "relocated": str(target.resolve()) != manifest["state_dir"],
            "queued_work_replayed": False}
