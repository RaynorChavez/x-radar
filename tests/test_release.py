from __future__ import annotations

import importlib.util
import json
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("build_release", ROOT / "tools/build_release.py")
assert SPEC and SPEC.loader
build_release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_release)


class ReleaseArtifactTests(unittest.TestCase):
    def test_two_builds_of_same_commit_are_identical(self):
        commit = "a" * 40
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            one = build_release.build("RaynorChavez/x-radar", commit, Path(first))
            two = build_release.build("RaynorChavez/x-radar", commit, Path(second))
            self.assertEqual(one["artifact"]["sha256"], two["artifact"]["sha256"])
            self.assertEqual(
                (Path(first) / one["artifact"]["name"]).read_bytes(),
                (Path(second) / two["artifact"]["name"]).read_bytes(),
            )

    def test_artifact_contains_only_allowlisted_runtime_files(self):
        commit = "b" * 40
        with tempfile.TemporaryDirectory() as directory:
            metadata = build_release.build("RaynorChavez/x-radar", commit, Path(directory))
            artifact = Path(directory) / metadata["artifact"]["name"]
            with tarfile.open(artifact, "r:gz") as archive:
                names = archive.getnames()
                self.assertIn("release.json", names)
                self.assertIn("collector/firefox_collect.py", names)
                self.assertIn("bin/collect-pipeline", names)
                self.assertIn("bin/semantic-server", names)
                self.assertIn("src/xradar/luna.py", names)
                self.assertIn("src/xradar/pipeline.py", names)
                self.assertIn("src/xradar/semantic.py", names)
                self.assertFalse(any(name.startswith("site/") for name in names))
                self.assertFalse(any(name.startswith("var/") for name in names))
                embedded = json.load(archive.extractfile("release.json"))
            self.assertEqual(embedded["commit"], commit)
            self.assertEqual(embedded["application"], "x-radar")

    def test_invalid_identity_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                build_release.build("not-a-repo", "a" * 40, Path(directory))
            with self.assertRaises(ValueError):
                build_release.build("owner/repo", "short", Path(directory))


if __name__ == "__main__":
    unittest.main()
