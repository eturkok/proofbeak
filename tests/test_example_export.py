"""An installed package can create a starting definition without a repository checkout."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from proofbeak.cli import main
from proofbeak.packs import builtins


class ExampleExportTests(unittest.TestCase):
    def test_export_is_valid_private_and_non_executing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "my-scenario.json"
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(["example", "--output", str(path)]), 0)
            pack, _ = builtins().prepare(json.loads(path.read_text()))
            self.assertEqual(pack.id, "scenario.v1")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_export_never_replaces_existing_files_or_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "existing.json"
            original.write_text("preserve this content")
            link = root / "link.json"
            link.symlink_to(original)
            dangling = root / "dangling.json"
            dangling.symlink_to(root / "absent.json")
            for path in (original, link, dangling):
                with self.subTest(path=path.name), redirect_stderr(io.StringIO()):
                    self.assertEqual(main(["example", "--output", str(path)]), 3)
            self.assertEqual(original.read_text(), "preserve this content")
            self.assertTrue(link.is_symlink())
            self.assertTrue(dangling.is_symlink())
            self.assertFalse((root / "absent.json").exists())
            self.assertEqual(list(root.glob(".proofbeak-report-*")), [])
