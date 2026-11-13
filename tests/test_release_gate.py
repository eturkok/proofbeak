import unittest
from release_gate import release_targets


class ReleaseGateTests(unittest.TestCase):
    def test_prs_and_feature_pushes_cannot_publish(self):
        for event, ref in (("pull_request", "refs/pull/24/merge"), ("push", "refs/heads/feat/release")):
            result = release_targets(event, ref, "0.6.0", "0.5.1", pypi=True, image=True)
            self.assertFalse(result["publish_pypi"] or result["publish_image"])

    def test_only_a_main_version_increase_automatically_publishes(self):
        result = release_targets("push", "refs/heads/main", "0.6.0", "0.5.1")
        self.assertTrue(result["publish_pypi"] and result["publish_image"])
        result = release_targets("push", "refs/heads/main", "0.6.0", "0.6.0")
        self.assertFalse(result["publish_pypi"] or result["publish_image"])
        with self.assertRaises(ValueError):
            release_targets("push", "refs/heads/main", "0.5.1", "0.6.0")

    def test_manual_publication_requires_main_and_matching_version(self):
        for ref, requested in (("refs/heads/feat/release", "0.6.0"), ("refs/heads/main", ""), ("refs/heads/main", "0.5.1")):
            with self.assertRaises(ValueError):
                release_targets("workflow_dispatch", ref, "0.6.0", requested=requested, pypi=True)
        result = release_targets("workflow_dispatch", "refs/heads/main", "0.6.0", requested="0.6.0", pypi=True)
        self.assertTrue(result["publish_pypi"])
        self.assertFalse(result["publish_image"])

    def test_bad_versions_cannot_be_publishing_tags(self):
        for version in ("0.6.0\nother", "$(command)", "0.6.0;command", "../0.6.0"):
            with self.assertRaises(ValueError):
                release_targets("workflow_dispatch", "refs/heads/main", version, requested=version, pypi=True)
