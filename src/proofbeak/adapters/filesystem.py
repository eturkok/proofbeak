"""Real local filesystem reference adapter, restricted to generated disposable directories."""

import os
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

from proofbeak.adapters.base import ProviderError, Resource
from proofbeak.packs.storage import StorageSnapshot, content_hash


class FilesystemAdapter:
    name = "filesystem"
    version = "0.1.0"
    evidence_level = "local_runner"
    supported_trials = frozenset({"storage.put_same_key.v1"})

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.profile = {"root": str(self.root), "provider_version": "local-filesystem-v1",
                        "permissions": "generated_trial_directories_only"}

    def resource(self, run_id):
        if not re.fullmatch(r"[a-f0-9]{32}", run_id):
            raise ValueError("invalid run id")
        return Resource(str(self.root / f"proofbeak-{run_id}"), run_id)

    def _path(self, resource):
        if resource != self.resource(resource.scope_id):
            raise ProviderError("resource_scope_violation")
        path = Path(resource.id)
        if path.is_symlink() or self.root.is_symlink() or path.parent.resolve() != self.root:
            raise ProviderError("resource_scope_violation")
        return path

    def provision(self, resource, trial):
        try:
            path = self._path(resource)
            self.root.mkdir(parents=True, exist_ok=True)
            path.mkdir()
        except FileExistsError:
            raise ProviderError("resource_conflict") from None
        except OSError:
            raise ProviderError("filesystem_unavailable") from None

    def seed(self, resource, trial):
        try:
            path = self._path(resource) / trial.key
            with path.open("xb") as target:
                target.write(trial.content.encode())
        except OSError:
            raise ProviderError("filesystem_unavailable") from None

    def read(self, resource, trial):
        try:
            objects = {}
            for path in self._path(resource).iterdir():
                if len(objects) >= 100 or path.is_symlink() or not path.is_file():
                    raise ProviderError("invalid_observation")
                # Bound reads independently of an observed file's reported size.
                with path.open("rb") as source:
                    data = source.read(4097)
                if len(data) > 4096:
                    raise ProviderError("invalid_observation")
                objects[path.name] = content_hash(data)
        except OSError:
            raise ProviderError("observation_unavailable") from None
        return StorageSnapshot(objects, "filesystem:directory-list-and-read", datetime.now(UTC).isoformat())

    def apply(self, resource, trial):
        try:
            path = self._path(resource)
            temporary = path / ".proofbeak-write"
            with temporary.open("xb") as target:
                target.write(trial.content.encode())
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, path / trial.key)
        except OSError:
            raise ProviderError("write_outcome_unknown") from None

    def cleanup(self, resource):
        try:
            path = self._path(resource)
            if path.exists():
                shutil.rmtree(path)
        except OSError:
            raise ProviderError("cleanup_unavailable") from None
