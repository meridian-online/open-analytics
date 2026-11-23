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
APR = datetime(2026, 4, 1, tzinfo=timezone.utc)


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

    def test_inputs_unchanged_since_the_publish_are_main_disagreeing(self) -> None:
        self.assertEqual(lc.classify("widgets", FEB, JAN), lc.MAIN)

    def test_a_crash_is_drift_and_a_repository_only_finding_is_main_disagreeing(self) -> None:
        """A crash leaves the lake unchecked, so it alarms; a finding about the repository alone does not."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = scratch_repo(root)
            commit(repo, JAN, {"README.md": "x\n"})
            tree = lc.Tree(write_tree(root, FEB, {"path": "x.parquet"}) / "datasets")
            self.assertEqual(lc.sort_finding(repo, tree, lc.Finding("labels", "exit 2", crash=True)), lc.DRIFT)
            self.assertEqual(lc.sort_finding(repo, tree, lc.Finding("descriptors", "examples/demo: licence")), lc.MAIN)

    def test_a_check_that_fails_without_a_finding_is_a_crash(self) -> None:
        found = lc.crashed(lc.Ran("labels", 2, "Traceback"), [])
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0].crash)
        self.assertTrue(lc.each("labels", [{"unexpected": "shape"}], lambda item: lc.Finding("labels", item["key"]))[0].crash)


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
        self.assertEqual(self.sort(lc.Finding("descriptors", "catalogue.rows", [self.subject], ["README.md"])), lc.MAIN)

    def test_a_label_finding_reads_the_reasons_file_alone(self) -> None:
        reasons = ["datasets/label-agreement.json"]
        commit(self.repo, JAN, {"datasets/label-agreement.json": "{}\n"})
        commit(self.repo, MAR, {"datasets/widgets/README.md": "edited\n"})
        self.assertEqual(self.sort(lc.Finding("labels", "no stated reason", [self.subject], reasons)), lc.MAIN)
        commit(self.repo, MAR, {"datasets/label-agreement.json": '{"reasons": []}\n'})
        self.assertEqual(self.sort(lc.Finding("labels", "no stated reason", [self.subject], reasons)), lc.AWAITING)

    def test_a_ratchet_finding_reads_its_sidecar_alone(self) -> None:
        sidecar = ["datasets/widgets/descriptor.overrides.json"]
        commit(self.repo, JAN, {sidecar[0]: "{}\n"})
        commit(self.repo, MAR, {"datasets/widgets/README.md": "edited\n"})
        self.assertEqual(self.sort(lc.Finding("ratchet", "ignored", [self.subject], sidecar)), lc.MAIN)
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
                [
                    sys.executable, str(SCRIPT), "--tree", str(tree), "--repo", str(self.repo), "--json", str(out), "--check", "verify",
                    "--alert", str(self.root / "alert.md"), "--report", str(self.root / "report.md"),
                ],
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
        self.assertIn("1 drift, 0 main disagrees, 0 awaiting publish", done.stdout)
        alert = (self.root / "alert.md").read_text(encoding="utf-8")
        self.assertIn("Drift (1)", alert)
        self.assertIn(f"declares {len(self.BODY) + 1:,} bytes", alert)
        self.assertIn("Main disagrees with the lake (0)", (self.root / "report.md").read_text(encoding="utf-8"))

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

    def package(self, slug: str, fields: list[dict], select_sql: str, table: str | None = None, **extra: object) -> Path:
        """A descriptor and its Parquet: `<slug>/datapackage.json`, or `<slug>/<table>.datapackage.json` given `table`."""
        import duckdb

        name = table or slug
        folder = self.datasets / slug
        folder.mkdir(exist_ok=True)
        parquet = folder / f"{name}.parquet"
        con = duckdb.connect()
        con.execute(f"COPY ({select_sql}) TO '{parquet}' (FORMAT parquet)")
        con.close()
        document = {
            "name": name,
            "title": name,
            "description": f"The {name} table.",
            "created": "2026-02-01T00:00:00Z",
            "licenses": LICENCE,
            "resources": [{"name": name, "format": "parquet", "path": parquet.name, "schema": {"fields": fields}}],
            **extra,
        }
        filename = f"{table}.datapackage.json" if table else "datapackage.json"
        (folder / filename).write_text(json.dumps(document, indent=2), encoding="utf-8")
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

    RECORD = "https://openlake.meridian.online/runs/run-1.json"

    def published_by(self, pin: str | None, *, record: object = "runs/run-1.json") -> str:
        """A local manifest whose entry names `record`; the record's stamp step pinned `pin`.

        Run records are fetched, never read from disk, so `read_source` is replaced for the
        case: it serves the one record URL and refuses any other address.
        """
        steps = [{"name": "stamp", "resolved_with": {"script": "../../scripts/stamp_descriptor.py", "deps": [f"duckdb=={pin}"]}}]
        served = {self.RECORD: {"steps": steps}}
        self.requested: list[str] = []
        original = lc.read_source

        def read_source(where: str) -> object:
            if where.startswith(("http://", "https://")):
                self.requested.append(where)
                if where not in served:
                    raise OSError(f"HTTP Error 404: {where}")
                return served[where]
            return original(where)

        lc.read_source = read_source
        self.addCleanup(setattr, lc, "read_source", original)
        entry: dict = {"url": "widgets.parquet", "published": "2026-02-01T00:00:00Z"}
        if record is not None:
            entry["run"] = {"id": "run-1", "record": record}
        manifest = self.root / "manifest.json"
        manifest.write_text(json.dumps({"datasets": {"t/widgets": entry}}), encoding="utf-8")
        return str(manifest)

    STAMP = "steps:\n  - name: stamp\n    op: uv@1\n    with:\n      script: ../../scripts/stamp_descriptor.py\n      deps:\n        - duckdb=={}\n"

    def head_pin(self, pin: str) -> None:
        (self.datasets / "widgets" / "arcform.yaml").write_text(self.STAMP.format(pin))

    def test_engine(self) -> None:
        """Held to the pin of the Run that published it, which the lake's run record states."""
        import duckdb

        self.package("widgets", [{"name": "n", "type": "integer"}], "SELECT 1 AS n")
        self.head_pin(duckdb.__version__)
        ran, found, notes = lc.check_engine(self.tree(), self.work, self.published_by("1.0.0"))
        self.assertEqual(ran.status, 1, ran.log)
        self.assertEqual(len(found), 1, [f.detail for f in found])
        self.assertIn("pinned 1.0.0", found[0].detail)
        self.assertIn("in run run-1", found[0].detail)
        self.assertEqual(notes, [])
        self.assertEqual(self.requested, [self.RECORD], "a relative record resolves against the lake's origin")
        commit(self.repo, MAR, {"datasets/widgets/arcform.yaml": "a commit after the publish\n"})
        self.assertEqual(self.sorted_kinds(found), [lc.DRIFT])

    def test_engine_a_pin_moved_on_main_after_the_publish_is_not_a_finding(self) -> None:
        import duckdb

        self.package("widgets", [{"name": "n", "type": "integer"}], "SELECT 1 AS n")
        self.head_pin("9.9.9")
        ran, found, _ = lc.check_engine(self.tree(), self.work, self.published_by(duckdb.__version__))
        self.assertEqual((ran.status, found), (0, []), ran.log)

    def test_engine_with_no_run_record_is_sorted_by_the_protocol_pin(self) -> None:
        """Held to HEAD's pin, which is a repository input: awaiting once it moved after the publish."""
        self.package("widgets", [{"name": "n", "type": "integer"}], "SELECT 1 AS n")
        self.head_pin("9.9.9")
        ran, found, notes = lc.check_engine(self.tree(), self.work, self.published_by(None, record=None))
        self.assertEqual(len(found), 1, ran.log)
        self.assertIn("at HEAD, because the manifest names no run record for it", found[0].detail)
        self.assertFalse(found[0].lake_only)
        self.assertEqual(found[0].inputs, ["datasets/widgets/arcform.yaml"])
        self.assertEqual(notes, ["engine: widgets/datapackage.json is held to its Protocol's pin at HEAD, because the manifest names no run record for it"])
        commit(self.repo, JAN, {"datasets/widgets/arcform.yaml": self.STAMP.format("1.0.0")})
        self.assertEqual(self.sorted_kinds(found), [lc.MAIN])
        commit(self.repo, MAR, {"datasets/widgets/arcform.yaml": self.STAMP.format("9.9.9")})
        self.assertEqual(self.sorted_kinds(found), [lc.AWAITING])

    def test_engine_with_an_unreadable_run_record_says_so(self) -> None:
        self.package("widgets", [{"name": "n", "type": "integer"}], "SELECT 1 AS n")
        self.head_pin("9.9.9")
        ran, found, notes = lc.check_engine(self.tree(), self.work, self.published_by("9.9.9", record="runs/missing.json"))
        self.assertEqual(self.requested, ["https://openlake.meridian.online/runs/missing.json"])
        self.assertEqual(len(notes), 1)
        self.assertIn("widgets/datapackage.json", notes[0])
        self.assertIn("could not be read", notes[0])
        self.assertIn("could not be read", found[0].detail)
        self.assertEqual(found[0].inputs, ["datasets/widgets/arcform.yaml"])
        text = lc.summary([], [], {"engine": 1}, notes)
        self.assertIn("widgets/datapackage.json is held to its Protocol's pin at HEAD", text)

    def test_a_run_that_is_not_an_object_takes_the_head_pin_path(self) -> None:
        self.package("widgets", [{"name": "n", "type": "integer"}], "SELECT 1 AS n")
        self.head_pin("9.9.9")
        manifest = Path(self.published_by("9.9.9"))
        document = json.loads(manifest.read_text())
        document["datasets"]["t/widgets"]["run"] = "x"
        manifest.write_text(json.dumps(document))
        ran, found, notes = lc.check_engine(self.tree(), self.work, str(manifest))
        self.assertEqual(len(notes), 1, ran.log)
        self.assertIn("names no run record", notes[0])
        self.assertEqual(found[0].inputs, ["datasets/widgets/arcform.yaml"])

    def test_a_run_record_naming_no_stamp_pin_takes_the_head_pin_path(self) -> None:
        self.package("widgets", [{"name": "n", "type": "integer"}], "SELECT 1 AS n")
        self.head_pin("9.9.9")
        manifest = self.published_by("9.9.9")
        original = lc.read_source
        lc.read_source = lambda where: {"steps": []} if where == self.RECORD else original(where)
        ran, found, notes = lc.check_engine(self.tree(), self.work, manifest)
        self.assertEqual(len(notes), 1, ran.log)
        self.assertIn("names no stamp step's duckdb pin", notes[0])
        self.assertFalse(found[0].lake_only)
        self.assertEqual(found[0].inputs, ["datasets/widgets/arcform.yaml"])

    def test_a_relative_record_resolves_against_the_manifest_url_never_a_local_path(self) -> None:
        self.assertEqual(lc.record_url("runs/a.json", "https://lake.example/manifest.json?v=1"), "https://lake.example/runs/a.json")
        self.assertEqual(lc.record_url("runs/a.json", "/some/local/manifest.json"), "https://openlake.meridian.online/runs/a.json")
        self.assertEqual(lc.record_url("https://x.example/r.json", "/some/local/manifest.json"), "https://x.example/r.json")

    def labelled(self, slug: str, label: str) -> None:
        self.package(slug, [{"name": "code", "type": "string", "x-finetype-label": label}], "SELECT '01' AS code")

    def test_labels(self) -> None:
        self.labelled("alpha", "representation.text.word")
        self.labelled("beta", "representation.identifier.numeric_code")
        commit(self.repo, JAN, {"datasets/label-agreement.json": "{}\n"})
        ran, found = lc.check_labels(self.tree(), self.work, self.repo)
        self.assertEqual(ran.status, 1, ran.log)
        self.assertEqual(len(found), 1, [f.detail for f in found])
        self.assertEqual(sorted(s.slug for s in found[0].subjects), ["alpha", "beta"])
        self.assertEqual(self.sorted_kinds(found), [lc.MAIN])
        # An edit to the reasons file that names nothing of this difference leaves main disagreeing.
        commit(self.repo, MAR, {"datasets/label-agreement.json": '{"reasons": []}\n'})
        ran, found = lc.check_labels(self.tree(), self.work, self.repo)
        self.assertEqual(self.sorted_kinds(found), [lc.MAIN])
        # Removing a reason for this field after the publish is a change on main awaiting it.
        entry = {"field": "code", "property": "x-finetype-label", "values": {"alpha.code": "a", "beta.code": "b"}, "reason": "r"}
        commit(self.repo, MAR, {"datasets/label-agreement.json": json.dumps({"reasons": [entry]}, indent=2) + "\n"})
        commit(self.repo, APR, {"datasets/label-agreement.json": '{"reasons": []}\n'})
        ran, found = lc.check_labels(self.tree(), self.work, self.repo)
        self.assertEqual(self.sorted_kinds(found), [lc.AWAITING])

    def reasons_file(self, entries: list[dict]) -> str:
        text = json.dumps({"reasons": entries}, indent=2) + "\n"
        (self.datasets / "label-agreement.json").write_text(text, encoding="utf-8")
        return text

    def test_a_label_finding_is_dated_by_its_own_reason_entry(self) -> None:
        """An unrelated reason edited after the publish leaves a stale reason, and its open difference, main disagreeing."""
        self.labelled("alpha", "representation.text.word")
        self.labelled("beta", "representation.identifier.numeric_code")
        stale = {"field": "code", "property": "x-finetype-label", "values": {"alpha.code": "representation.text.word", "beta.code": "x"}, "reason": "Pinned to a value beta no longer declares."}
        other = {"concept": "some.other.label", "property": "constraints.maxLength", "values": {"p.f": 1, "q.f": 2}, "reason": "Unrelated."}
        commit(self.repo, JAN, {"datasets/label-agreement.json": self.reasons_file([stale, other])})
        ran, found = lc.check_labels(self.tree(), self.work, self.repo)
        self.assertEqual(ran.status, 1, ran.log)
        self.assertEqual(sorted(f.detail.rsplit(": ", 1)[1] for f in found), ["a stated reason matches no difference", "a stated reason matches no difference", "no stated reason"])
        code = [f for f in found if "'code'" in f.detail]
        self.assertEqual(len(code), 2)
        self.assertEqual(set(self.sorted_kinds(code)), {lc.MAIN})

        commit(self.repo, MAR, {"datasets/label-agreement.json": self.reasons_file([stale, {**other, "reason": "Reworded."}])})
        ran, found = lc.check_labels(self.tree(), self.work, self.repo)
        kinds = {f.detail: lc.sort_finding(self.repo, self.tree(), f) for f in found}
        self.assertEqual(kinds["field 'code', x-finetype-label: a stated reason matches no difference"], lc.MAIN)
        self.assertEqual(kinds["field 'code', x-finetype-label: no stated reason"], lc.MAIN)

        moved = {**stale, "values": {"alpha.code": "representation.text.word", "beta.code": "y"}}
        commit(self.repo, APR, {"datasets/label-agreement.json": self.reasons_file([moved, {**other, "reason": "Reworded."}])})
        ran, found = lc.check_labels(self.tree(), self.work, self.repo)
        kinds = {f.detail: lc.sort_finding(self.repo, self.tree(), f) for f in found}
        self.assertEqual(kinds["field 'code', x-finetype-label: a stated reason matches no difference"], lc.AWAITING)
        self.assertEqual(kinds["field 'code', x-finetype-label: no stated reason"], lc.AWAITING)

    def test_a_removed_reason_whose_key_has_regex_characters_is_found(self) -> None:
        """`git log -G` reads an extended regex, so `+` and `(` in a key must match literally."""
        finding = lc.Finding("labels", "d", [lc.Subject("widgets", None)], ["r.json"])
        commit(self.repo, JAN, {"r.json": '{"reasons": [{"field": "a+b(c)"}]}\n'})
        commit(self.repo, MAR, {"r.json": '{"reasons": []}\n'})
        finding.mentions = [("r.json", lc.literal_regex('"field": "a+b(c)"'))]
        self.assertEqual(lc.dated(self.repo, finding), MAR)

    def test_a_readme_differing_from_head_is_dated_by_the_whole_file(self) -> None:
        commit(self.repo, JAN, {"README.md": "row\n"})
        commit(self.repo, MAR, {"other.md": "x\n", "README.md": "row\nmore\n"})
        finding = lc.Finding("descriptors", "d", [], ["README.md"], lines=[("README.md", 1, 1)])
        self.assertEqual(lc.dated(self.repo, finding), JAN)
        (self.repo / "README.md").write_text("inserted\nrow\nmore\n", encoding="utf-8")
        self.assertEqual(lc.dated(self.repo, finding), MAR)

    def catalogue_findings(self) -> list[lc.Finding]:
        ran, found = lc.check_descriptors(self.tree(), self.repo, self.work, {})
        rows = [f for f in found if "catalogue.rows" in f.detail]
        self.assertEqual(len(rows), 1, ran.log)
        return rows

    def test_an_unrelated_readme_edit_after_the_publish_leaves_a_row_mismatch_main_disagreeing(self) -> None:
        parquet = self.package("widgets", [{"name": "n", "type": "integer"}], "SELECT 1 AS n")
        # The descriptor names the object by the address the row links to, as a published one does.
        descriptor = self.datasets / "widgets" / "datapackage.json"
        document = json.loads(descriptor.read_text(encoding="utf-8"))
        document["resources"][0]["path"] = str(parquet)
        descriptor.write_text(json.dumps(document), encoding="utf-8")
        shutil.copy(REPO_DATASETS / "licence-notices.json", self.datasets / "licence-notices.json")
        example = self.repo / "examples" / "demo" / "datapackage.json"
        example.parent.mkdir(parents=True)
        example.write_text(json.dumps({"name": "demo", "licenses": LICENCE, "resources": []}), encoding="utf-8")
        table = "| Dataset | Rows | Readable copy |\n|---|---|---|\n"
        row = f"| widgets | 2 | [w]({parquet}) |\n"  # the object holds 1 row
        commit(self.repo, JAN, {"README.md": "# Catalogue\n\n" + table + row})
        self.assertEqual(self.sorted_kinds(self.catalogue_findings()), [lc.MAIN])

        # Prose added above the table after the publish: the row moves down, and is untouched.
        commit(self.repo, MAR, {"README.md": "# Catalogue\n\nA new paragraph.\n\nAnother.\n\n" + table + row})
        found = self.catalogue_findings()
        self.assertEqual(found[0].lines, [("README.md", 9, 9)])
        self.assertEqual(self.sorted_kinds(found), [lc.MAIN])

        # The row itself edited after the publish is main ahead of the lake.
        commit(self.repo, APR, {"README.md": "# Catalogue\n\nA new paragraph.\n\nAnother.\n\n" + table + row.replace("| 2 |", "| 3 |")})
        self.assertEqual(self.sorted_kinds(self.catalogue_findings()), [lc.AWAITING])

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
        self.assertEqual(set(self.sorted_kinds(found)), {lc.MAIN})

    def test_code_list(self) -> None:
        self.package("widgets", [{"name": "n", "type": "integer"}], "SELECT 1 AS n", **{"x-meridian-code-list": {"levels": ["one"]}})
        commit(self.repo, MAR, {"datasets/widgets/README.md": "edited after the publish\n"})
        ran, found = lc.check_code_list(self.tree(), self.work)
        self.assertEqual(ran.status, 1, ran.log)
        self.assertTrue(found)
        self.assertIn("core-columns", found[0].detail)
        self.assertEqual({s.slug for f in found for s in f.subjects}, {"widgets"})
        self.assertEqual(set(self.sorted_kinds(found)), {lc.DRIFT})


    # A Protocol that publishes several tables writes `<slug>/<table>.datapackage.json` and
    # no `<slug>/datapackage.json`; each check reads it as a package of its own.

    def test_descriptors_reads_a_per_table_descriptor(self) -> None:
        widgets = self.package("widgets", [{"name": "n", "type": "integer"}], "SELECT 1 AS n")
        parts = self.package("gadgets", [{"name": "n", "type": "integer"}, {"name": "ghost", "type": "string"}], "SELECT 1 AS n", table="gadget_parts")
        shutil.copy(REPO_DATASETS / "licence-notices.json", self.datasets / "licence-notices.json")
        example = self.repo / "examples" / "demo" / "datapackage.json"
        example.parent.mkdir(parents=True)
        example.write_text(json.dumps({"name": "demo", "licenses": LICENCE, "resources": []}), encoding="utf-8")
        readme = "| Dataset | Rows | Readable copy |\n|---|---|---|\n" f"| widgets | 1 | [w]({widgets}) |\n| parts | 1 | [p]({parts}) |\n"
        commit(self.repo, MAR, {"README.md": readme, "datasets/gadgets/README.md": "edited after the publish\n"})
        ran, found = lc.check_descriptors(self.tree(), self.repo, self.work, {})
        self.assertEqual(ran.status, 1, ran.log)
        rules = [f for f in found if "schema.fields" in f.detail]
        self.assertTrue(rules, ran.log)
        self.assertEqual({(s.slug, s.descriptor) for f in rules for s in f.subjects}, {("gadgets", "gadgets/gadget_parts.datapackage.json")})
        self.assertTrue(all(f.detail.startswith("gadgets/gadget_parts.datapackage.json :: ") for f in rules))
        self.assertEqual({lc.sort_finding(self.repo, self.tree(), f) for f in rules}, {lc.DRIFT})

    def per_table_run_reporting(self, status: int, items: list[dict]) -> tuple[lc.Ran, list[lc.Finding]]:
        """`check_per_table_descriptors` with the descriptor check replaced by one that reports `items`."""
        self.package("gadgets", [{"name": "n", "type": "integer"}], "SELECT 1 AS n", table="gadget_parts")
        tree = self.tree()
        original = lc.run

        def run(check: str, argv: list[str]) -> lc.Ran:
            out = Path(argv[argv.index("--json") + 1])
            out.write_text(json.dumps({"violations": items, "pending": []}), encoding="utf-8")
            return lc.Ran(check, status, "")

        lc.run = run
        self.addCleanup(setattr, lc, "run", original)
        return lc.check_per_table_descriptors(tree, self.work, tree.per_table())

    def item(self, rule: str) -> dict:
        package = str(self.work / "packages" / "gadget_parts" / "datapackage.json")
        return {"package": package, "resource": "(package)", "field": "", "rule": rule, "detail": "d"}

    def test_a_per_table_run_failing_on_a_licence_notice_alone_is_no_finding(self) -> None:
        # The first run reads the licence notice rule over per-table descriptors already,
        # and reports it against licence-notices.json; the second must neither repeat it
        # nor call its exit 1 a crash.
        self.package("widgets", [{"name": "n", "type": "integer"}], "SELECT 1 AS n")
        ran, found = self.per_table_run_reporting(1, [self.item("licence.notice")])
        self.assertEqual(ran.status, 1)
        self.assertEqual(found, [])

    def test_a_per_table_run_keeps_every_other_finding_beside_a_notice(self) -> None:
        ran, found = self.per_table_run_reporting(1, [self.item("licence.notice"), self.item("schema.fields")])
        self.assertEqual([f.detail for f in found], ["gadgets/gadget_parts.datapackage.json :: (package): schema.fields: d"])
        self.assertEqual([(s.slug, s.descriptor) for s in found[0].subjects], [("gadgets", "gadgets/gadget_parts.datapackage.json")])

    def test_a_per_table_run_that_exits_1_reporting_nothing_is_a_crash(self) -> None:
        ran, found = self.per_table_run_reporting(1, [])
        self.assertEqual(len(found), 1)
        self.assertIn("no finding this script can attribute", found[0].detail)

    def test_frictionless_reads_a_per_table_descriptor(self) -> None:
        import frictionless

        self.package("widgets", [{"name": "n", "type": "integer"}], "SELECT 1 AS n")
        self.package("gadgets", [{"name": "n", "type": "integer", "constraints": {"pattern": "^[0-9]+$"}}], "SELECT 1 AS n", table="gadget_parts")
        (self.datasets / "frictionless-rejections.json").write_text(
            json.dumps({"frictionless_version": frictionless.__version__, "rejections": []}), encoding="utf-8"
        )
        commit(self.repo, JAN, {"datasets/frictionless-rejections.json": "{}\n"})
        ran, found = lc.check_frictionless(self.tree(), self.work)
        self.assertEqual(ran.status, 1, ran.log)
        self.assertTrue(found)
        self.assertEqual({(s.slug, s.descriptor) for f in found for s in f.subjects}, {("gadgets", "gadgets/gadget_parts.datapackage.json")})
        self.assertEqual(set(self.sorted_kinds(found)), {lc.MAIN})

    def test_code_list_reads_a_per_table_descriptor(self) -> None:
        self.package("gadgets", [{"name": "n", "type": "integer"}], "SELECT 1 AS n", table="gadget_parts", **{"x-meridian-code-list": {"levels": ["one"]}})
        commit(self.repo, MAR, {"datasets/gadgets/README.md": "edited after the publish\n"})
        ran, found = lc.check_code_list(self.tree(), self.work)
        self.assertEqual(ran.status, 1, ran.log)
        self.assertTrue(found)
        self.assertTrue(all(f.detail.startswith("gadgets/gadget_parts.datapackage.json: code-list: core-columns") for f in found), found)
        self.assertEqual({(s.slug, s.descriptor) for f in found for s in f.subjects}, {("gadgets", "gadgets/gadget_parts.datapackage.json")})
        self.assertEqual(set(self.sorted_kinds(found)), {lc.DRIFT})

    def test_a_per_table_name_that_is_another_package_cannot_run(self) -> None:
        self.package("widgets", [{"name": "n", "type": "integer"}], "SELECT 1 AS n")
        self.package("gadgets", [{"name": "n", "type": "integer"}], "SELECT 1 AS n", table="widgets")
        with self.assertRaisesRegex(lc.Unrunnable, "already that package"):
            lc.check_code_list(self.tree(), self.work)


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


class RatchetSortTest(unittest.TestCase):
    def test_an_unreadable_stored_descriptor_is_drift_and_an_unreadable_sidecar_is_not(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = scratch_repo(root)
            sidecar = "datasets/widgets/descriptor.overrides.json"
            commit(repo, JAN, {sidecar: '{"fields": {}}\n'})
            tree_dir = write_tree(root, FEB, {"path": "x.parquet", "schema": {"fields": [{"type": "integer"}]}})
            tree = lc.Tree(tree_dir / "datasets")
            found = lc.check_ratchet(tree, repo)
            self.assertEqual(len(found), 1)
            self.assertIn("declares no name", found[0].detail)
            self.assertEqual(lc.sort_finding(repo, tree, found[0]), lc.DRIFT)
            commit(repo, MAR, {sidecar: '{"fields": ["code"]}\n'})
            found = lc.check_ratchet(tree, repo)
            self.assertEqual(len(found), 1)
            self.assertIn("not an object", found[0].detail)
            self.assertEqual(lc.sort_finding(repo, tree, found[0]), lc.MAIN)


class IssueTextTest(unittest.TestCase):
    """The bodies of the alert issue and the weekly report issue."""

    def catalogue(self) -> lc.Finding:
        finding = lc.Finding("descriptors", "README.md line 12: catalogue.rows", [lc.Subject("naics", "naics/datapackage.json")], ["README.md"])
        finding.kind = lc.MAIN
        return finding

    def test_the_report_names_the_catalogue_fix_only_when_a_row_count_disagrees(self) -> None:
        with_row = lc.report_text([self.catalogue()], [])
        self.assertIn("--write-catalogue", with_row)
        self.assertIn("Main disagrees with the lake (1)", with_row)
        self.assertIn("catalogue.rows", with_row)
        label = lc.Finding("labels", "no stated reason", [lc.Subject("a", None)], ["datasets/label-agreement.json"])
        self.assertNotIn("--write-catalogue", lc.report_text([label], []))

    def test_the_alert_lists_drift_and_not_the_report(self) -> None:
        drift = lc.Finding("verify", "widgets :: widgets: declares 1 bytes", [lc.Subject("widgets", "widgets/datapackage.json")], lake_only=True)
        alert = lc.alert_text([drift])
        self.assertIn("Drift (1)", alert)
        self.assertIn("declares 1 bytes", alert)
        self.assertNotIn("Main disagrees", alert)

    def test_a_body_too_long_for_an_issue_is_cut_and_says_so(self) -> None:
        many = [lc.Finding("labels", "x" * 1000, [lc.Subject("a", None)], ["r"]) for _ in range(100)]
        body = lc.report_text(many, [])
        self.assertLessEqual(len(body), lc.ISSUE_LIMIT + 200)
        self.assertIn("Cut to fit an issue", body)
        self.assertEqual(body.count("```") % 2, 0, "a cut inside a code block closes it")
        self.assertEqual(lc.capped("short\n"), "short\n")


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
