#!/usr/bin/env python3
"""Self-test for check_before_publish.py — proves the pre-publish gate can fail, and on what.

    uv run --with duckdb==2.0.0 --with pyyaml==6.0.3 --with frictionless==5.19.0 \
        scripts/test_check_before_publish.py

Every case builds a scratch repository: `datasets/` holding two code-list Protocols
(`alpha`, the one being published, and its sibling `beta`) or a three-table one
(`gamma`), each with the `arcform.yaml` publish step that names its tables, plus a lake
bundle of the descriptors the lake already holds. The Protocol's built Parquet is real
and stamped with its own descriptor, as the `stamp_descriptor` step leaves it. The
script runs as a subprocess from inside the Protocol's folder, as `arc` runs it, and
every case asserts BOTH the exit status AND the message.

No case touches the network: the bundle's resources point at an `.invalid` host, and
the only Parquet any check reads is the built one, through `--read-from`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import stamp_descriptor as sd  # noqa: E402 - found beside this file

SCRIPT = Path(__file__).with_name("check_before_publish.py")
REPO_DATASETS = Path(__file__).resolve().parent.parent / "datasets"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_ERROR = 2

LICENCE = [
    {"name": "CC0-1.0", "path": "https://creativecommons.org/publicdomain/zero/1.0/", "title": "CC0 1.0 Universal"}
]
PLAIN = "representation.text.plain_text"
WORD = "representation.text.word"
CODE = "representation.identifier.numeric_code"


def url(table: str) -> str:
    return f"https://openlake.example.invalid/{table.replace('.', '/')}.parquet"


def code_list(name: str, table: str, *, description_label: str = PLAIN, **code_extra: Any) -> dict[str, Any]:
    code: dict[str, Any] = {
        "name": "code",
        "type": "string",
        "description": "The code.",
        "x-finetype-label": CODE,
        "constraints": {"pattern": "^[0-9]{2}$"},
    }
    code.update(code_extra)
    return {
        "name": name,
        "title": name,
        "description": f"The {name} code list.",
        "licenses": LICENCE,
        "resources": [
            {
                "name": name,
                "format": "parquet",
                "mediatype": "application/vnd.apache.parquet",
                "path": url(table),
                "schema": {
                    "fields": [
                        code,
                        {
                            "name": "description",
                            "type": "string",
                            "description": "What the code means.",
                            "x-finetype-label": description_label,
                        },
                    ],
                    "primaryKey": ["code"],
                },
            }
        ],
    }


def arcform(tables: list[tuple[str, str]]) -> str:
    lines = ["steps:", "  - name: publish", "    op: ducklake_write@1", "    with:", "      tables:"]
    for table, descriptor in tables:
        lines += [f"        - table: {table}", f"          descriptor: {descriptor}"]
    return "\n".join(lines) + "\n"


class Scratch(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="before-publish-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.datasets = self.root / "datasets"
        self.datasets.mkdir()
        shutil.copy(REPO_DATASETS / "licence-notices.json", self.datasets / "licence-notices.json")
        import frictionless

        (self.datasets / "frictionless-rejections.json").write_text(
            json.dumps({"frictionless_version": frictionless.__version__, "rejections": []}) + "\n"
        )
        self.bundle: dict[str, Any] = {}
        self.built: list[tuple[Path, Path]] = []

    # ── the repository ────────────────────────────────────────────────────────────
    def protocol(self, slug: str, tables: list[tuple[str, str]]) -> Path:
        folder = self.datasets / slug
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "arcform.yaml").write_text(arcform(tables))
        return folder

    def two_code_lists(self, *, alpha_in_lake: bool = True) -> None:
        self.protocol("alpha", [("t.alpha", "datapackage.json")])
        self.protocol("beta", [("t.beta", "datapackage.json")])
        self.bundle["t.beta"] = code_list("beta", "t.beta")
        if alpha_in_lake:
            self.bundle["t.alpha"] = code_list("alpha", "t.alpha")

    # ── the built output of the Protocol's Run ────────────────────────────────────
    def build(self, slug: str, filename: str, document: dict[str, Any], select_sql: str | None = None) -> None:
        """Write build/<name>.parquet stamped with `document`, and the descriptor beside the Protocol."""
        import duckdb

        folder = self.datasets / slug
        name = document["name"]
        stage = folder / "build" / "stage" / f"{name}.parquet"
        stage.parent.mkdir(parents=True, exist_ok=True)
        parquet = folder / "build" / f"{name}.parquet"
        con = duckdb.connect()
        sql = select_sql or "SELECT * FROM (VALUES ('01', 'one'), ('02', 'two')) v(code, description)"
        con.execute(f"COPY ({sql}) TO '{stage}' (FORMAT parquet)")
        con.close()
        sd.stamp_parquet(sd.connect(), stage, parquet, sd.embedded_text(document))
        size, digest = sd.file_measurement(parquet)
        document = json.loads(json.dumps(document))
        document["created"] = "2026-11-17T00:00:00Z"
        document["resources"][0]["bytes"] = size
        document["resources"][0]["hash"] = f"sha256:{digest}"
        descriptor = folder / filename
        descriptor.write_text(json.dumps(document, indent=2) + "\n")
        self.built.append((descriptor, parquet))

    def commit(self) -> None:
        """Commit the scratch repository as it stands: the overrides check reads HEAD."""
        git = ["git", "-C", str(self.root), "-c", "user.name=t", "-c", "user.email=t@example.test"]
        if not (self.root / ".git").exists():
            subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run([*git, "add", "-A"], check=True, capture_output=True)
        subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "scratch"], check=True, capture_output=True)

    def sidecar(self, slug: str, fields: dict[str, Any], name: str = "descriptor.overrides.json") -> None:
        (self.datasets / slug / name).write_text(json.dumps({"fields": fields}, indent=2) + "\n")

    def run_check(
        self, slug: str = "alpha", *, commit: bool = True
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, Any] | None]:
        if commit:
            self.commit()
        folder = self.datasets / slug
        (folder / "build").mkdir(parents=True, exist_ok=True)
        (folder / "build" / "lake_descriptors.json").write_text(json.dumps(self.bundle, indent=2))
        args = [
            sys.executable,
            str(SCRIPT),
            "--datasets-dir",
            "../../datasets",
            "--slug",
            slug,
            "--lake-bundle",
            "build/lake_descriptors.json",
            "--json",
            "build/check_before_publish.json",
        ]
        for descriptor, parquet in self.built:
            args += ["--built", f"{descriptor.name}=build/{parquet.name}"]
        result = subprocess.run(args, cwd=folder, capture_output=True, text=True, check=False)
        report = folder / "build" / "check_before_publish.json"
        return result, (json.loads(report.read_text()) if report.exists() else None)

    def both(self, result: subprocess.CompletedProcess[str]) -> str:
        return result.stdout + result.stderr

    def assertChecks(self, report: dict[str, Any] | None, **passed: bool) -> None:  # noqa: N802
        self.assertIsNotNone(report)
        got = {check["check"]: check["passed"] for check in report["checks"]}
        passed.setdefault("overrides", True)
        want = {name.replace("_", " "): value for name, value in passed.items()}
        self.assertEqual(got, want)


class CheckBeforePublish(Scratch):
    def test_clean_passes(self) -> None:
        self.two_code_lists()
        self.build("alpha", "datapackage.json", code_list("alpha", "t.alpha"))
        result, report = self.run_check()
        self.assertEqual(result.returncode, EXIT_OK, self.both(result))
        self.assertChecks(report, label_agreement=True, frictionless=True, descriptors=True)
        self.assertTrue(report["passed"])
        self.assertIn("4 of 4 check(s) passed", result.stdout)
        # The built descriptor, not the lake's older copy, is the one in the tree.
        laid = json.loads((self.datasets / "alpha/build/check_tree/datasets/alpha/datapackage.json").read_text())
        self.assertIn("bytes", laid["resources"][0])
        self.assertEqual(laid["created"], "2026-11-17T00:00:00Z")

    def test_label_drift_fails(self) -> None:
        self.two_code_lists()
        self.build("alpha", "datapackage.json", code_list("alpha", "t.alpha", description_label=WORD))
        result, report = self.run_check()
        self.assertEqual(result.returncode, EXIT_FAILED, self.both(result))
        self.assertChecks(report, label_agreement=False, frictionless=True, descriptors=True)
        self.assertIn("FAILED  label agreement", result.stdout)
        self.assertIn("field name 'description'", result.stdout)
        self.assertIn(f'alpha.description: "{WORD}"', result.stdout)

    def test_a_sibling_in_the_lake_labelled_differently_fails_on_that_difference_alone(self) -> None:
        """The arc refusal case in census/fips can match only an exit status, and the label it
        moves is also pinned by reasons, so a stale reason would fail it too. Here no reason
        is shipped at all: the one thing that can fail is the field-name disagreement, and
        the report has to name it as an open difference."""
        self.two_code_lists()
        self.bundle["t.beta"] = code_list("beta", "t.beta", description_label=WORD)
        self.build("alpha", "datapackage.json", code_list("alpha", "t.alpha"))
        self.assertFalse((self.datasets / "label-agreement.json").exists())
        result, report = self.run_check()
        self.assertEqual(result.returncode, EXIT_FAILED, self.both(result))
        self.assertChecks(report, label_agreement=False, frictionless=True, descriptors=True)
        self.assertIn("field name 'description' — x-finetype-label differs [OPEN]", result.stdout)
        self.assertIn(f'beta.description: "{WORD}"', result.stdout)
        self.assertIn(f'alpha.description: "{PLAIN}"', result.stdout)
        self.assertIn("FAILED under --strict", result.stdout)
        self.assertNotIn("no longer describe", self.both(result))

    def test_override_broken_by_data(self) -> None:
        self.two_code_lists()
        # The sibling declares the same, so label agreement has nothing to say: only the data does.
        self.bundle["t.beta"]["resources"][0]["schema"]["fields"][1]["constraints"] = {"required": True}
        document = code_list("alpha", "t.alpha")
        document["resources"][0]["schema"]["fields"][1]["constraints"] = {"required": True}
        self.build(
            "alpha",
            "datapackage.json",
            document,
            "SELECT * FROM (VALUES ('01', 'one'), ('02', NULL)) v(code, description)",
        )
        result, report = self.run_check()
        self.assertEqual(result.returncode, EXIT_FAILED, self.both(result))
        self.assertChecks(report, label_agreement=True, frictionless=True, descriptors=False)
        self.assertIn("FAILED  descriptors", result.stdout)
        self.assertIn("required", result.stdout)
        self.assertIn("description", result.stdout)

    def test_frictionless_refusal(self) -> None:
        self.two_code_lists()
        document = code_list("alpha", "t.alpha")
        document["resources"][0]["schema"]["fields"].append(
            {"name": "rank", "type": "integer", "description": "A rank.", "constraints": {"pattern": "^[0-9]+$"}}
        )
        self.build(
            "alpha",
            "datapackage.json",
            document,
            "SELECT * FROM (VALUES ('01', 'one', 1), ('02', 'two', 2)) v(code, description, rank)",
        )
        result, report = self.run_check()
        self.assertEqual(result.returncode, EXIT_FAILED, self.both(result))
        self.assertFalse(next(c for c in report["checks"] if c["check"] == "frictionless")["passed"])
        self.assertIn("FAILED  frictionless", result.stdout)
        self.assertIn('constraint "pattern" is not supported by type "integer"', result.stdout)

    def test_first_publish(self) -> None:
        self.two_code_lists(alpha_in_lake=False)
        self.build("alpha", "datapackage.json", code_list("alpha", "t.alpha"))
        result, report = self.run_check()
        self.assertEqual(result.returncode, EXIT_OK, self.both(result))
        self.assertChecks(report, label_agreement=True, frictionless=True, descriptors=True)
        label = next(c for c in report["checks"] if c["check"] == "label agreement")
        self.assertEqual(label["findings"]["examined"], 2)

    def test_first_publish_still_compares_against_siblings(self) -> None:
        self.two_code_lists(alpha_in_lake=False)
        self.build("alpha", "datapackage.json", code_list("alpha", "t.alpha", description_label=WORD))
        result, report = self.run_check()
        self.assertEqual(result.returncode, EXIT_FAILED, self.both(result))
        self.assertIn("beta.description", result.stdout)

    def test_multi_table(self) -> None:
        self.two_code_lists()
        names = ["g_one", "g_two", "g_three"]
        self.protocol("gamma", [(f"t.{n}", f"{n}.datapackage.json") for n in names])
        for n in names:
            self.bundle[f"t.{n}"] = code_list(n, f"t.{n}")
            self.build("gamma", f"{n}.datapackage.json", code_list(n, f"t.{n}"))
        result, report = self.run_check("gamma")
        self.assertEqual(result.returncode, EXIT_OK, self.both(result))
        # Label agreement does not read per-table descriptors (see the exclusion test
        # below), so this case claims only the two checks that do.
        checks = {c["check"]: c for c in report["checks"]}
        self.assertTrue(checks["frictionless"]["passed"])
        self.assertTrue(checks["descriptors"]["passed"])
        tree = self.datasets / "gamma/build/check_tree/datasets/gamma"
        for n in names:
            laid = json.loads((tree / f"{n}.datapackage.json").read_text())
            self.assertEqual(laid["created"], "2026-11-17T00:00:00Z", n)
        self.assertEqual(checks["descriptors"]["findings"]["checked"], "g_one, g_three, g_two")
        self.assertEqual(sorted(checks["descriptors"]["findings"]["self_described"]),
                         sorted(f"{n}::{n}" for n in names))
        self.assertEqual(checks["frictionless"]["findings"]["accepted"], sorted(names))

    def test_multi_table_checks_every_table(self) -> None:
        """The third table alone is broken, and the run names it."""
        self.two_code_lists()
        names = ["g_one", "g_two", "g_three"]
        self.protocol("gamma", [(f"t.{n}", f"{n}.datapackage.json") for n in names])
        for n in names:
            document = code_list(n, f"t.{n}")
            sql = None
            if n == "g_three":
                document["resources"][0]["schema"]["fields"][1]["constraints"] = {"required": True}
                sql = "SELECT * FROM (VALUES ('01', NULL)) v(code, description)"
            self.build("gamma", f"{n}.datapackage.json", document, sql)
        result, report = self.run_check("gamma")
        self.assertEqual(result.returncode, EXIT_FAILED, self.both(result))
        checks = {c["check"]: c for c in report["checks"]}
        self.assertTrue(checks["frictionless"]["passed"])
        self.assertFalse(checks["descriptors"]["passed"])
        self.assertIn("g_three", result.stdout)

    def test_label_agreement_does_not_read_per_table_descriptors(self) -> None:
        """The stated exclusion, pinned: label agreement reads `<slug>/datapackage.json` only.

        That is the coverage the lake check has today. A per-table `<name>.datapackage.json`
        labelling `description` differently from its sibling still passes. Widening the
        coverage is a separate change; when it lands, this case flips to expect the failure.
        """
        self.two_code_lists()
        names = ["g_one", "g_two"]
        self.protocol("gamma", [(f"t.{n}", f"{n}.datapackage.json") for n in names])
        self.build("gamma", "g_one.datapackage.json", code_list("g_one", "t.g_one", description_label=WORD))
        self.build("gamma", "g_two.datapackage.json", code_list("g_two", "t.g_two"))
        result, report = self.run_check("gamma")
        self.assertEqual(result.returncode, EXIT_OK, self.both(result))
        self.assertChecks(report, label_agreement=True, frictionless=True, descriptors=True)
        label = next(c for c in report["checks"] if c["check"] == "label agreement")
        # alpha and beta only: neither gamma table was read.
        self.assertEqual(label["findings"]["examined"], 2)
        self.assertEqual(
            sorted(Path(p).parent.name for p in label["findings"]["packages"]), ["alpha", "beta"]
        )

    def test_the_tree_is_rebuilt_fresh(self) -> None:
        self.two_code_lists()
        stale = self.datasets / "alpha/build/check_tree/datasets/stale/datapackage.json"
        stale.parent.mkdir(parents=True)
        stale.write_text(json.dumps(code_list("stale", "t.stale", description_label=WORD)))
        self.build("alpha", "datapackage.json", code_list("alpha", "t.alpha"))
        result, _ = self.run_check()
        self.assertEqual(result.returncode, EXIT_OK, self.both(result))
        self.assertFalse(stale.exists())

    def test_a_built_descriptor_the_slug_does_not_publish_is_refused(self) -> None:
        self.two_code_lists()
        self.build("alpha", "other.datapackage.json", code_list("alpha", "t.alpha"))
        result, _ = self.run_check()
        self.assertEqual(result.returncode, EXIT_ERROR, self.both(result))
        self.assertIn("other.datapackage.json", result.stderr)
        self.assertIn("datapackage.json", result.stderr)


class ACheckThatCannotRun(Scratch):
    """A crash inside one check is that check failing to run: the rest still run and the report is written."""

    def run_in_process(self, broken: str) -> tuple[int, dict[str, Any], str]:
        import contextlib
        import io

        import check_before_publish as cbp

        self.two_code_lists()
        self.build("alpha", "datapackage.json", code_list("alpha", "t.alpha"))
        folder = self.datasets / "alpha"
        (folder / "build" / "lake_descriptors.json").write_text(json.dumps(self.bundle))
        self.commit()
        module = getattr(cbp, broken)
        original = module.main

        def crash(argv: list[str]) -> int:
            print("partial output before the crash")
            raise RuntimeError("the check fell over")

        module.main = crash
        cwd = os.getcwd()
        out = io.StringIO()
        try:
            os.chdir(folder)
            with contextlib.redirect_stdout(out):
                status = cbp.main(
                    [
                        "--datasets-dir", "../../datasets", "--slug", "alpha",
                        "--lake-bundle", "build/lake_descriptors.json",
                        "--built", "datapackage.json=build/alpha.parquet",
                        "--json", "build/check_before_publish.json",
                    ]
                )
        finally:
            os.chdir(cwd)
            module.main = original
        report = json.loads((folder / "build" / "check_before_publish.json").read_text())
        return status, report, out.getvalue()

    def test_a_crash_is_exit_2_with_its_traceback_and_the_rest_still_run(self) -> None:
        status, report, out = self.run_in_process("check_frictionless")
        self.assertEqual(status, EXIT_ERROR, out)
        checks = {c["check"]: c for c in report["checks"]}
        self.assertEqual(checks["frictionless"]["exit"], EXIT_ERROR)
        self.assertIn("partial output before the crash", checks["frictionless"]["output"])
        self.assertIn("RuntimeError: the check fell over", checks["frictionless"]["output"])
        self.assertTrue(checks["label agreement"]["passed"])
        self.assertTrue(checks["descriptors"]["passed"])
        self.assertTrue(checks["overrides"]["passed"])
        self.assertIn("FAILED  frictionless (exit 2)", out)


class OverridesAtHead(Scratch):
    """The ratchet's refusal, moved to where a descriptor is regenerated now.

    The built descriptor's `code` carries `^[0-9]{2}$` (see `code_list`). Each case sets
    what the overrides held at HEAD and in the working tree, and nothing else differs.
    """

    TWO = {"code": {"constraints": {"pattern": "^[0-9]{2}$"}}}
    ANY = {"code": {"constraints": {"pattern": "^[0-9]+$"}}}

    def overrides(self, result: subprocess.CompletedProcess[str], report: dict[str, Any] | None) -> dict[str, Any]:
        self.assertIsNotNone(report, self.both(result))
        return next(c for c in report["checks"] if c["check"] == "overrides")

    def test_a_built_descriptor_carrying_what_head_holds_passes(self) -> None:
        self.two_code_lists()
        self.sidecar("alpha", self.TWO)
        self.build("alpha", "datapackage.json", code_list("alpha", "t.alpha"))
        result, report = self.run_check()
        self.assertEqual(result.returncode, EXIT_OK, self.both(result))
        self.assertChecks(report, label_agreement=True, frictionless=True, descriptors=True)
        self.assertIn("0 hand-held constraint(s) replaced or ignored; 1 held at HEAD", self.overrides(result, report)["output"])

    def test_a_constraint_head_holds_and_the_build_lost_is_refused(self) -> None:
        """Dropped from the working sidecar and regenerated looser: main's `replaced`."""
        self.two_code_lists()
        self.sidecar("alpha", self.TWO)
        self.commit()
        self.sidecar("alpha", {})
        # The sibling declares the same, so label agreement has nothing to say.
        self.bundle["t.beta"] = code_list("beta", "t.beta", constraints={"pattern": "^[0-9]+$"})
        self.build("alpha", "datapackage.json", code_list("alpha", "t.alpha", constraints={"pattern": "^[0-9]+$"}))
        result, report = self.run_check(commit=False)
        self.assertEqual(result.returncode, EXIT_FAILED, self.both(result))
        self.assertChecks(report, label_agreement=True, frictionless=True, descriptors=True, overrides=False)
        self.assertIn("FAILED  overrides", result.stdout)
        self.assertIn("alpha/datapackage.json.code: constraints.pattern", result.stdout)
        self.assertEqual([v["kind"] for v in self.overrides(result, report)["findings"]["violations"]], ["replaced"])

    def test_a_constraint_head_holds_that_the_build_did_not_apply_is_refused(self) -> None:
        self.two_code_lists()
        self.sidecar("alpha", self.ANY)
        self.build("alpha", "datapackage.json", code_list("alpha", "t.alpha"))
        result, report = self.run_check()
        self.assertEqual(result.returncode, EXIT_FAILED, self.both(result))
        self.assertChecks(report, label_agreement=True, frictionless=True, descriptors=True, overrides=False)

    def test_a_constraint_moved_in_the_sidecar_passes(self) -> None:
        """Moving a constraint in the sidecar, where the diff shows it, is not refused."""
        self.two_code_lists()
        self.sidecar("alpha", self.TWO)
        self.commit()
        self.sidecar("alpha", self.ANY)
        # The sibling declares the same, so label agreement has nothing to say.
        self.bundle["t.beta"] = code_list("beta", "t.beta", constraints={"pattern": "^[0-9]+$"})
        self.build("alpha", "datapackage.json", code_list("alpha", "t.alpha", constraints={"pattern": "^[0-9]+$"}))
        result, report = self.run_check(commit=False)
        self.assertEqual(result.returncode, EXIT_OK, self.both(result))

    def test_a_per_table_descriptor_reads_its_own_sidecar(self) -> None:
        self.two_code_lists()
        self.protocol("gamma", [("t.g_one", "g_one.datapackage.json")])
        self.bundle["t.g_one"] = code_list("g_one", "t.g_one")
        self.sidecar("gamma", self.ANY, "g_one.overrides.json")
        self.build("gamma", "g_one.datapackage.json", code_list("g_one", "t.g_one"))
        result, report = self.run_check("gamma")
        self.assertEqual(result.returncode, EXIT_FAILED, self.both(result))
        self.assertIn("gamma/g_one.datapackage.json.code", result.stdout)

    def test_outside_a_git_checkout_the_check_cannot_run(self) -> None:
        self.two_code_lists()
        self.build("alpha", "datapackage.json", code_list("alpha", "t.alpha"))
        result, report = self.run_check(commit=False)
        self.assertEqual(result.returncode, EXIT_ERROR, self.both(result))
        self.assertEqual(self.overrides(result, report)["exit"], EXIT_ERROR)
        self.assertIn("not inside a git work tree", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
