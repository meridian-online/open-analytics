#!/usr/bin/env python3
"""Self-test for lake_check.py: how a finding is sorted, and a run that finds drift.

The classification cases call `classify` and `sort_finding` against a scratch git
repository whose commits carry fixed dates, so "changed after the publish" is a fact
about the fixture and not about the day the test runs. The end-to-end cases run the
script on a scratch tree whose one object is served from loopback.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lake_check as lc  # noqa: E402 - found beside this file
from test_stamp_descriptor import ObjectServer  # noqa: E402 - found beside this file

SCRIPT = Path(__file__).with_name("lake_check.py")

JAN = datetime(2026, 1, 1, tzinfo=timezone.utc)
FEB = datetime(2026, 2, 1, tzinfo=timezone.utc)
MAR = datetime(2026, 3, 1, tzinfo=timezone.utc)


def commit(repo: Path, when: datetime, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    stamp = when.isoformat()
    env = {**os.environ, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.test"]
    subprocess.run([*git, "add", *files], check=True, capture_output=True)
    subprocess.run([*git, "commit", "-q", "-m", "fixture"], check=True, capture_output=True, env=env)


def scratch_repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    return repo


def write_tree(root: Path, created: datetime, resource: dict) -> Path:
    tree = root / "tree"
    descriptor = tree / "datasets" / "widgets" / "datapackage.json"
    descriptor.parent.mkdir(parents=True)
    document = {
        "name": "widgets",
        "created": created.isoformat().replace("+00:00", "Z"),
        "resources": [{"name": "widgets", "format": "parquet", **resource}],
    }
    descriptor.write_text(json.dumps(document), encoding="utf-8")
    return tree


class ClassifyTest(unittest.TestCase):
    def test_an_unpublished_table_is_awaiting_publish(self) -> None:
        self.assertEqual(lc.classify("widgets", None, MAR), lc.AWAITING)
        self.assertEqual(lc.classify(None, None, MAR), lc.AWAITING)

    def test_inputs_changed_after_the_publish_are_awaiting_publish(self) -> None:
        self.assertEqual(lc.classify("widgets", FEB, MAR), lc.AWAITING)

    def test_inputs_unchanged_since_the_publish_are_drift(self) -> None:
        self.assertEqual(lc.classify("widgets", FEB, JAN), lc.DRIFT)

    def test_a_finding_with_no_table_is_drift(self) -> None:
        """A crash, or a repository-only disagreement: no publish can close it."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = scratch_repo(root)
            commit(repo, JAN, {"README.md": "x\n"})
            tree = lc.Tree(write_tree(root, FEB, {"path": "x.parquet"}) / "datasets")
            self.assertEqual(lc.sort_finding(repo, tree, lc.Finding("labels", "exit 2")), lc.DRIFT)


class InputsTest(unittest.TestCase):
    """Which repository paths a finding's time is read from."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.repo = scratch_repo(root)
        self.tree = lc.Tree(write_tree(root, FEB, {"path": "x.parquet"}) / "datasets")
        self.subject = lc.Subject("widgets", "widgets/datapackage.json")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def finding(self, catalogue: bool) -> lc.Finding:
        if catalogue:
            return lc.Finding("descriptors", "catalogue.rows", [self.subject], ["README.md"], own_dir=False)
        return lc.Finding("descriptors", "schema.fields", [self.subject])

    def test_a_catalogue_finding_uses_the_readme_time(self) -> None:
        commit(self.repo, JAN, {"datasets/widgets/arcform.yaml": "steps: []\n"})
        commit(self.repo, MAR, {"README.md": "| rows |\n"})
        self.assertEqual(lc.sort_finding(self.repo, self.tree, self.finding(catalogue=True)), lc.AWAITING)
        self.assertEqual(lc.sort_finding(self.repo, self.tree, self.finding(catalogue=False)), lc.DRIFT)

    def test_a_catalogue_finding_ignores_the_protocol_directory(self) -> None:
        commit(self.repo, JAN, {"README.md": "| rows |\n"})
        commit(self.repo, MAR, {"datasets/widgets/arcform.yaml": "steps: []\n"})
        self.assertEqual(lc.sort_finding(self.repo, self.tree, self.finding(catalogue=True)), lc.DRIFT)
        self.assertEqual(lc.sort_finding(self.repo, self.tree, self.finding(catalogue=False)), lc.AWAITING)

    def test_a_label_finding_also_reads_the_reasons_file(self) -> None:
        commit(self.repo, JAN, {"datasets/widgets/arcform.yaml": "steps: []\n"})
        commit(self.repo, MAR, {"datasets/label-agreement.json": "{}\n"})
        finding = lc.Finding("labels", "no stated reason", [self.subject], ["datasets/label-agreement.json"])
        self.assertEqual(lc.sort_finding(self.repo, self.tree, finding), lc.AWAITING)


class EndToEndTest(unittest.TestCase):
    """The script over a scratch tree whose one object is served from loopback."""

    BODY = b"PAR1 not really a parquet, but bytes to measure PAR1"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.repo = scratch_repo(self.root)
        commit(self.repo, JAN, {"datasets/widgets/arcform.yaml": "steps: []\n", "README.md": "x\n"})

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_check(self, declared_bytes: int) -> tuple[subprocess.CompletedProcess[str], dict]:
        out = self.root / "findings.json"
        with ObjectServer(self.BODY, "/widgets.parquet") as server:
            digest = "sha256:" + hashlib.sha256(self.BODY).hexdigest()
            tree = write_tree(self.root, FEB, {"path": server.url, "bytes": declared_bytes, "hash": digest})
            done = subprocess.run(
                [sys.executable, str(SCRIPT), "--tree", str(tree), "--repo", str(self.repo), "--json", str(out), "--check", "verify"],
                capture_output=True,
                text=True,
                check=False,
                env={k: v for k, v in os.environ.items() if k != "GITHUB_STEP_SUMMARY"},
            )
        return done, json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}

    def test_a_served_object_that_disagrees_with_its_descriptor_is_drift(self) -> None:
        done, report = self.run_check(len(self.BODY) + 1)
        self.assertEqual(done.returncode, lc.EXIT_DRIFT, done.stdout + done.stderr)
        self.assertEqual(len(report[lc.DRIFT]), 1, report)
        self.assertEqual(report[lc.AWAITING], [])
        self.assertIn(f"declares {len(self.BODY) + 1:,} bytes", report[lc.DRIFT][0]["detail"])
        self.assertIn("1 drift, 0 awaiting publish", done.stdout)

    def test_the_same_disagreement_after_a_change_on_main_is_awaiting_publish(self) -> None:
        commit(self.repo, MAR, {"datasets/widgets/arcform.yaml": "steps: [changed]\n"})
        done, report = self.run_check(len(self.BODY) + 1)
        self.assertEqual(done.returncode, lc.EXIT_OK, done.stdout + done.stderr)
        self.assertEqual(report[lc.DRIFT], [])
        self.assertEqual(len(report[lc.AWAITING]), 1, report)

    def test_a_served_object_that_agrees_is_no_finding(self) -> None:
        done, report = self.run_check(len(self.BODY))
        self.assertEqual(done.returncode, lc.EXIT_OK, done.stdout + done.stderr)
        self.assertEqual((report[lc.DRIFT], report[lc.AWAITING]), ([], []))

    def test_a_tree_with_no_descriptor_cannot_pass(self) -> None:
        (self.root / "empty" / "datasets").mkdir(parents=True)
        done = subprocess.run(
            [sys.executable, str(SCRIPT), "--tree", str(self.root / "empty"), "--repo", str(self.repo)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(done.returncode, lc.EXIT_ERROR)
        self.assertIn("holds no descriptor", done.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
