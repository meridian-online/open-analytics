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

    def run_check(self, slug: str = "alpha") -> tuple[subprocess.CompletedProcess[str], dict[str, Any] | None]:
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
        self.assertIn("3 of 3 check(s) passed", result.stdout)
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
        self.assertChecks(report, label_agreement=True, frictionless=True, descriptors=True)
        tree = self.datasets / "gamma/build/check_tree/datasets/gamma"
        for n in names:
            laid = json.loads((tree / f"{n}.datapackage.json").read_text())
            self.assertEqual(laid["created"], "2026-11-17T00:00:00Z", n)
        checks = {c["check"]: c for c in report["checks"]}
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
        self.assertChecks(report, label_agreement=True, frictionless=True, descriptors=False)
        self.assertIn("g_three", result.stdout)

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
