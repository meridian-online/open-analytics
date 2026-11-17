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
import shutil
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
    """Which repository paths a finding's time is read from, and which findings read none."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.repo = scratch_repo(root)
        self.tree = lc.Tree(write_tree(root, FEB, {"path": "x.parquet"}) / "datasets")
        self.subject = lc.Subject("widgets", "widgets/datapackage.json")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def sort(self, finding: lc.Finding) -> str:
        return lc.sort_finding(self.repo, self.tree, finding)

    def test_a_catalogue_finding_uses_the_readme_time(self) -> None:
        commit(self.repo, JAN, {"datasets/widgets/arcform.yaml": "steps: []\n"})
        commit(self.repo, MAR, {"README.md": "| rows |\n"})
        self.assertEqual(self.sort(lc.Finding("descriptors", "catalogue.rows", [self.subject], ["README.md"])), lc.AWAITING)

    def test_a_catalogue_finding_ignores_the_protocol_directory(self) -> None:
        commit(self.repo, JAN, {"README.md": "| rows |\n"})
        commit(self.repo, MAR, {"datasets/widgets/arcform.yaml": "steps: []\n"})
        self.assertEqual(self.sort(lc.Finding("descriptors", "catalogue.rows", [self.subject], ["README.md"])), lc.DRIFT)

    def test_a_label_finding_reads_the_reasons_file_alone(self) -> None:
        reasons = ["datasets/label-agreement.json"]
        commit(self.repo, JAN, {"datasets/label-agreement.json": "{}\n"})
        commit(self.repo, MAR, {"datasets/widgets/README.md": "edited\n"})
        self.assertEqual(self.sort(lc.Finding("labels", "no stated reason", [self.subject], reasons)), lc.DRIFT)
        commit(self.repo, MAR, {"datasets/label-agreement.json": '{"reasons": []}\n'})
        self.assertEqual(self.sort(lc.Finding("labels", "no stated reason", [self.subject], reasons)), lc.AWAITING)

    def test_a_ratchet_finding_reads_its_sidecar_alone(self) -> None:
        sidecar = ["datasets/widgets/descriptor.overrides.json"]
        commit(self.repo, JAN, {sidecar[0]: "{}\n"})
        commit(self.repo, MAR, {"datasets/widgets/README.md": "edited\n"})
        self.assertEqual(self.sort(lc.Finding("ratchet", "ignored", [self.subject], sidecar)), lc.DRIFT)
        commit(self.repo, MAR, {sidecar[0]: '{"fields": {}}\n'})
        self.assertEqual(self.sort(lc.Finding("ratchet", "ignored", [self.subject], sidecar)), lc.AWAITING)

    def test_the_lake_against_itself_is_drift_after_any_commit(self) -> None:
        commit(self.repo, MAR, {"datasets/widgets/arcform.yaml": "steps: []\n", "README.md": "x\n"})
        self.assertEqual(self.sort(lc.Finding("verify", "hash differs", [self.subject], lake_only=True)), lc.DRIFT)

    def test_an_unpublished_table_is_awaiting_publish(self) -> None:
        commit(self.repo, JAN, {"README.md": "x\n"})
        unpublished = lc.Subject("gadgets", None)
        self.assertEqual(self.sort(lc.Finding("descriptors", "catalogue.unpublished", [unpublished], ["README.md"])), lc.AWAITING)


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

    def test_the_same_disagreement_after_a_commit_to_the_protocol_is_still_drift(self) -> None:
        """The served object contradicts the lake's own record; no commit on `main` can close that."""
        commit(self.repo, MAR, {"datasets/widgets/arcform.yaml": "steps: [changed]\n", "datasets/widgets/README.md": "x\n"})
        done, report = self.run_check(len(self.BODY) + 1)
        self.assertEqual(done.returncode, lc.EXIT_DRIFT, done.stdout + done.stderr)
        self.assertEqual(len(report[lc.DRIFT]), 1, report)
        self.assertEqual(report[lc.AWAITING], [])

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


LICENCE = [{"name": "CC0-1.0", "path": "https://creativecommons.org/publicdomain/zero/1.0/", "title": "CC0 1.0 Universal"}]
REPO_DATASETS = Path(__file__).resolve().parent.parent / "datasets"


class AdapterTest(unittest.TestCase):
    """Each JSON adapter over the real script's output, from a scratch tree the script fails on.

    Each case asserts the finding is attributed to its table and sorted by the rule for its
    kind, so an adapter that misread its script's JSON could not pass.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.repo = scratch_repo(self.root)
        self.datasets = self.root / "tree" / "datasets"
        self.datasets.mkdir(parents=True)
        self.work = self.root / "work"
        self.work.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def package(self, slug: str, fields: list[dict], select_sql: str, **extra: object) -> Path:
        import duckdb

        folder = self.datasets / slug
        folder.mkdir()
        parquet = folder / f"{slug}.parquet"
        con = duckdb.connect()
        con.execute(f"COPY ({select_sql}) TO '{parquet}' (FORMAT parquet)")
        con.close()
        document = {
            "name": slug,
            "title": slug,
            "description": f"The {slug} table.",
            "created": "2026-02-01T00:00:00Z",
            "licenses": LICENCE,
            "resources": [{"name": slug, "format": "parquet", "path": parquet.name, "schema": {"fields": fields}}],
            **extra,
        }
        (folder / "datapackage.json").write_text(json.dumps(document, indent=2), encoding="utf-8")
        return parquet

    def tree(self) -> lc.Tree:
        return lc.Tree(self.datasets)

    def sorted_kinds(self, found: list[lc.Finding]) -> list[str]:
        return [lc.sort_finding(self.repo, self.tree(), finding) for finding in found]

    def test_descriptors(self) -> None:
        parquet = self.package("widgets", [{"name": "n", "type": "integer"}, {"name": "ghost", "type": "string"}], "SELECT 1 AS n")
        shutil.copy(REPO_DATASETS / "licence-notices.json", self.datasets / "licence-notices.json")
        example = self.repo / "examples" / "demo" / "datapackage.json"
        example.parent.mkdir(parents=True)
        example.write_text(json.dumps({"name": "demo", "licenses": LICENCE, "resources": []}), encoding="utf-8")
        readme = "| Dataset | Rows | Readable copy |\n|---|---|---|\n" f"| widgets | 1 | [w]({parquet}) |\n"
        commit(self.repo, MAR, {"README.md": readme, "datasets/widgets/README.md": "edited after the publish\n"})
        ran, found = lc.check_descriptors(self.tree(), self.repo, self.work, {})
        self.assertEqual(ran.status, 1, ran.log)
        self.assertIn("schema.fields", "\n".join(f.detail for f in found))
        self.assertTrue(found)
        self.assertEqual({(s.slug, s.descriptor) for f in found for s in f.subjects}, {("widgets", "widgets/datapackage.json")})
        self.assertEqual(set(self.sorted_kinds(found)), {lc.DRIFT})

    def published_by(self, pin: str | None) -> str:
        """A manifest naming a run record whose stamp step pinned `pin`; None names no record."""
        record = self.root / "run.json"
        steps = [{"name": "stamp", "resolved_with": {"script": "../../scripts/stamp_descriptor.py", "deps": [f"duckdb=={pin}"]}}]
        record.write_text(json.dumps({"steps": steps}), encoding="utf-8")
        entry = {"url": "widgets.parquet", "published": "2026-02-01T00:00:00Z"}
        if pin is not None:
            entry["run"] = {"id": "run-1", "record": str(record)}
        manifest = self.root / "manifest.json"
        manifest.write_text(json.dumps({"datasets": {"t/widgets": entry}}), encoding="utf-8")
        return str(manifest)

    STAMP = "steps:\n  - name: stamp\n    op: uv@1\n    with:\n      script: ../../scripts/stamp_descriptor.py\n      deps:\n        - duckdb=={}\n"

    def test_engine(self) -> None:
        """Held to the pin of the Run that published it, which the lake's run record states."""
        import duckdb

        self.package("widgets", [{"name": "n", "type": "integer"}], "SELECT 1 AS n")
        (self.datasets / "widgets" / "arcform.yaml").write_text(self.STAMP.format(duckdb.__version__))
        ran, found = lc.check_engine(self.tree(), self.work, self.published_by("1.0.0"))
        self.assertEqual(ran.status, 1, ran.log)
        self.assertEqual(len(found), 1, [f.detail for f in found])
        self.assertIn("pinned 1.0.0", found[0].detail)
        self.assertIn("in run run-1", found[0].detail)
        self.assertEqual(found[0].subjects[0].descriptor, "widgets/datapackage.json")
        commit(self.repo, MAR, {"datasets/widgets/arcform.yaml": "a commit after the publish\n"})
        self.assertEqual(self.sorted_kinds(found), [lc.DRIFT])

    def test_engine_a_pin_moved_on_main_after_the_publish_is_not_a_finding(self) -> None:
        import duckdb

        self.package("widgets", [{"name": "n", "type": "integer"}], "SELECT 1 AS n")
        (self.datasets / "widgets" / "arcform.yaml").write_text(self.STAMP.format("9.9.9"))
        ran, found = lc.check_engine(self.tree(), self.work, self.published_by(duckdb.__version__))
        self.assertEqual((ran.status, found), (0, []), ran.log)

    def test_engine_with_no_run_record_reads_the_pin_at_head(self) -> None:
        self.package("widgets", [{"name": "n", "type": "integer"}], "SELECT 1 AS n")
        (self.datasets / "widgets" / "arcform.yaml").write_text(self.STAMP.format("9.9.9"))
        ran, found = lc.check_engine(self.tree(), self.work, self.published_by(None))
        self.assertEqual(len(found), 1, ran.log)
        self.assertIn("at HEAD", found[0].detail)

    def labelled(self, slug: str, label: str) -> None:
        self.package(slug, [{"name": "code", "type": "string", "x-finetype-label": label}], "SELECT '01' AS code")

    def test_labels(self) -> None:
        self.labelled("alpha", "representation.text.word")
        self.labelled("beta", "representation.identifier.numeric_code")
        commit(self.repo, JAN, {"datasets/label-agreement.json": "{}\n"})
        ran, found = lc.check_labels(self.tree(), self.work)
        self.assertEqual(ran.status, 1, ran.log)
        self.assertEqual(len(found), 1, [f.detail for f in found])
        self.assertEqual(sorted(s.slug for s in found[0].subjects), ["alpha", "beta"])
        self.assertEqual(self.sorted_kinds(found), [lc.DRIFT])
        commit(self.repo, MAR, {"datasets/label-agreement.json": '{"reasons": []}\n'})
        self.assertEqual(self.sorted_kinds(found), [lc.AWAITING])

    def test_frictionless(self) -> None:
        import frictionless

        self.package("widgets", [{"name": "n", "type": "integer", "constraints": {"pattern": "^[0-9]+$"}}], "SELECT 1 AS n")
        (self.datasets / "frictionless-rejections.json").write_text(
            json.dumps({"frictionless_version": frictionless.__version__, "rejections": []}), encoding="utf-8"
        )
        commit(self.repo, JAN, {"datasets/frictionless-rejections.json": "{}\n"})
        ran, found = lc.check_frictionless(self.tree(), self.work)
        self.assertEqual(ran.status, 1, ran.log)
        self.assertTrue(found)
        self.assertEqual({s.slug for f in found for s in f.subjects}, {"widgets"})
        self.assertEqual(set(self.sorted_kinds(found)), {lc.DRIFT})

    def test_code_list(self) -> None:
        self.package("widgets", [{"name": "n", "type": "integer"}], "SELECT 1 AS n", **{"x-meridian-code-list": {"levels": ["one"]}})
        commit(self.repo, MAR, {"datasets/widgets/README.md": "edited after the publish\n"})
        ran, found = lc.check_code_list(self.tree(), self.work)
        self.assertEqual(ran.status, 1, ran.log)
        self.assertTrue(found)
        self.assertIn("core-columns", found[0].detail)
        self.assertEqual({s.slug for f in found for s in f.subjects}, {"widgets"})
        self.assertEqual(set(self.sorted_kinds(found)), {lc.DRIFT})


class UnreadableItemTest(unittest.TestCase):
    def test_an_item_the_adapter_cannot_read_is_kept_beside_one_it_can(self) -> None:
        def convert(item: dict) -> lc.Finding:
            return lc.Finding("labels", item["key"], [lc.Subject("a", "a/datapackage.json")], ["x"])

        found = lc.each("labels", [{"key": "read"}, {"unexpected": "shape"}], convert)
        self.assertEqual(len(found), 2)
        self.assertEqual(found[1].subjects, [])
        self.assertIn("cannot read", found[1].detail)
        ran = lc.Ran("labels", 1, "")
        self.assertEqual(len(lc.crashed(ran, found)), 2)


class WorkflowPinsTest(unittest.TestCase):
    def test_the_env_lake_yml_shares_with_descriptors_yml_is_equal(self) -> None:
        import yaml

        workflows = Path(__file__).resolve().parent.parent / ".github" / "workflows"
        lake = yaml.safe_load((workflows / "lake.yml").read_text(encoding="utf-8"))["env"]
        pull = yaml.safe_load((workflows / "descriptors.yml").read_text(encoding="utf-8"))["env"]
        shared = sorted(set(lake) & set(pull))
        self.assertEqual(shared, sorted(lake), "lake.yml sets a version descriptors.yml does not pin")
        self.assertEqual({k: lake[k] for k in shared}, {k: pull[k] for k in shared})


if __name__ == "__main__":
    unittest.main(verbosity=2)
