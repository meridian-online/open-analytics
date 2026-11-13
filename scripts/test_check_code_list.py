#!/usr/bin/env python3
# /// script
# requires-python = "==3.12.*"
# dependencies = ["duckdb==1.5.5"]
# ///
"""Self-test for check_code_list.py: proves the contract can fail, and on which rule.

Every case writes a small Parquet with duckdb, runs the check as a subprocess and
asserts BOTH the exit status and the rule named in the output. A passing three-level
ladder is the control; each other case breaks exactly one rule in it.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import duckdb

SCRIPT = Path(__file__).resolve().parent / "check_code_list.py"
LEVELS = "chapter,heading,subheading"

CORE = [
    "code", "code_display", "level", "depth", "parent_code",
    "description", "description_full", "release", "text_source", "corpus",
]

# code, level, depth, parent_code
LADDER = [
    ("01", "chapter", 0, None),
    ("0101", "heading", 1, "01"),
    ("010121", "subheading", 2, "0101"),
]


def row(code, level, depth, parent, release="2026", description="d"):
    return {
        "code": code, "code_display": code, "level": level, "depth": depth,
        "parent_code": parent, "description": description,
        "description_full": description, "release": release,
        "text_source": "t", "corpus": "c",
    }


def ladder():
    return [row(*r) for r in LADDER]


class Case(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, rows, columns=CORE, name="list.parquet") -> Path:
        path = self.dir / name
        con = duckdb.connect()
        con.execute("CREATE TABLE t (%s)" % ", ".join(f'"{c}" VARCHAR' for c in columns))
        for r in rows:
            con.execute(
                "INSERT INTO t VALUES (%s)" % ", ".join("?" for _ in columns),
                [None if r.get(c) is None else str(r.get(c)) for c in columns],
            )
        select = "* REPLACE (CAST(depth AS INTEGER) AS depth)" if "depth" in columns else "*"
        con.execute(f"COPY (SELECT {select} FROM t) TO '{path}' (FORMAT parquet)")
        con.close()
        return path

    def run_check(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args],
                              capture_output=True, text=True)

    def expect_rule(self, rows, rule, columns=CORE):
        path = self.write(rows, columns)
        result = self.run_check("--parquet", str(path), "--levels", LEVELS)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(f"code-list: {rule}:", result.stdout)
        rules = {line.split(":")[1].strip() for line in result.stdout.splitlines()}
        self.assertEqual(rules, {rule}, result.stdout)

    def test_ladder_passes(self):
        path = self.write(ladder())
        result = self.run_check("--parquet", str(path), "--levels", LEVELS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_json_written(self):
        path = self.write(ladder())
        out = self.dir / "out.json"
        result = self.run_check("--parquet", str(path), "--levels", LEVELS, "--json", str(out))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(out.read_text())["findings"], [])

    def test_missing_column(self):
        cols = [c for c in CORE if c != "corpus"]
        self.expect_rule(ladder(), "core-columns", cols)

    def test_swapped_columns(self):
        cols = list(CORE)
        cols[0], cols[1] = cols[1], cols[0]
        self.expect_rule(ladder(), "core-columns", cols)

    def test_code_unique(self):
        self.expect_rule(ladder() + [row("0101", "heading", 1, "01")], "code-unique")

    def test_code_not_null(self):
        self.expect_rule(ladder() + [row(None, "heading", 1, "01")], "code-not-null")

    def test_parent_present(self):
        self.expect_rule(ladder() + [row("0202", "heading", 1, "02")], "parent-present")

    def test_parent_null_below_top(self):
        self.expect_rule(ladder() + [row("0202", "heading", 1, None)], "parent-null-below-top")

    def test_depth_step(self):
        self.expect_rule(ladder() + [row("0199", "subheading", 2, "01")], "depth-step")

    def test_level_set(self):
        self.expect_rule(ladder() + [row("01012100", "tariff_line", 3, "010121")], "level-set")

    def test_release_not_null(self):
        self.expect_rule(ladder() + [row("0102", "heading", 1, "01", release=None)], "release-not-null")

    def test_release_single(self):
        self.expect_rule(ladder() + [row("0102", "heading", 1, "01", release="2025")], "release-single")

    def test_description_not_null(self):
        self.expect_rule(ladder() + [row("0102", "heading", 1, "01", description=None)], "description-not-null")

    def test_empty_file(self):
        self.expect_rule([], "non-empty")

    def test_null_level(self):
        self.expect_rule(ladder() + [row("0102", None, 1, "01")], "level-set")

    def test_level_depth(self):
        # depth 2 under a depth-1 parent is a clean step, but a heading sits at 1
        self.expect_rule(ladder() + [row("010299", "heading", 2, "0101")], "level-depth")

    def test_extra_trailing_columns_allowed(self):
        cols = CORE + ["extra"]
        path = self.write([dict(r, extra="x") for r in ladder()], cols)
        result = self.run_check("--parquet", str(path), "--levels", LEVELS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def descriptor(self, block, resource_path="hts.parquet", resources=True):
        pkg = self.dir / "datasets" / "hts"
        pkg.mkdir(parents=True, exist_ok=True)
        doc = {"x-meridian-code-list": block}
        if resources:
            doc["resources"] = [{"name": "hts", "path": resource_path}]
        (pkg / "datapackage.json").write_text(json.dumps(doc))
        return pkg

    def test_bad_levels_exit_2(self):
        for bad in ({"levels": []}, {"levels": "chapter"}, {}):
            self.descriptor(bad)
            result = self.run_check("--datasets-dir", str(self.dir / "datasets"))
            self.assertEqual(result.returncode, 2, result.stdout)

    def test_missing_resource_path_exit_2(self):
        self.descriptor({"levels": ["chapter"]}, resources=False)
        result = self.run_check("--datasets-dir", str(self.dir / "datasets"))
        self.assertEqual(result.returncode, 2, result.stdout)

    def test_url_path_resolved_to_local_root(self):
        served = self.dir / "served"
        (served / "usitc").mkdir(parents=True)
        self.write(ladder(), name="x.parquet").rename(served / "usitc" / "hts.parquet")
        self.descriptor({"levels": LEVELS.split(",")},
                        resource_path="https://openlake.meridian.online/usitc/hts.parquet")
        result = self.run_check(
            "--datasets-dir", str(self.dir / "datasets"),
            "--resolve-root", f"https://openlake.meridian.online/={served}/")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_parquet_url_resolved_to_local_root(self):
        served = self.dir / "served"
        (served / "x").mkdir(parents=True)
        self.write(ladder(), name="x.parquet").rename(served / "x" / "y.parquet")
        result = self.run_check(
            "--parquet", "https://openlake.meridian.online/x/y.parquet", "--levels", LEVELS,
            "--resolve-root", f"https://openlake.meridian.online/={served}/")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_resolve_root_prefix_without_trailing_slash(self):
        served = self.dir / "served"
        (served / "x").mkdir(parents=True)
        self.write(ladder(), name="x.parquet").rename(served / "x" / "y.parquet")
        result = self.run_check(
            "--parquet", "https://openlake.meridian.online/x/y.parquet", "--levels", LEVELS,
            "--resolve-root", f"https://openlake.meridian.online={served}")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_resolve_root_prefix_matches_whole_path_segments(self):
        # A prefix without a trailing slash names a directory, not a string start:
        # .../usitc must not capture .../usitc-archive/..., which the next root maps.
        served, archive = self.dir / "served", self.dir / "archive"
        served.mkdir()
        archive.mkdir()
        self.write(ladder(), name="x.parquet").rename(archive / "hts.parquet")
        result = self.run_check(
            "--parquet", "https://openlake.meridian.online/usitc-archive/hts.parquet", "--levels", LEVELS,
            "--resolve-root", f"https://openlake.meridian.online/usitc={served}",
            "--resolve-root", f"https://openlake.meridian.online/usitc-archive={archive}")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_url_path_mapped_but_absent_exit_2(self):
        self.descriptor({"levels": LEVELS.split(",")},
                        resource_path="https://openlake.meridian.online/usitc/hts.parquet")
        result = self.run_check(
            "--datasets-dir", str(self.dir / "datasets"),
            "--resolve-root", f"https://openlake.meridian.online/={self.dir}/nowhere/")
        self.assertEqual(result.returncode, 2, result.stdout)

    def test_no_descriptors(self):
        (self.dir / "datasets").mkdir()
        result = self.run_check("--datasets-dir", str(self.dir / "datasets"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("no code-list descriptors", result.stdout + result.stderr)

    def test_unreadable_path(self):
        result = self.run_check("--parquet", str(self.dir / "absent.parquet"), "--levels", LEVELS)
        self.assertEqual(result.returncode, 2)

    def test_descriptor_mode(self):
        pkg = self.dir / "datasets" / "hts"
        pkg.mkdir(parents=True)
        self.write(ladder(), name="x.parquet").rename(pkg / "hts.parquet")
        (pkg / "datapackage.json").write_text(json.dumps({
            "x-meridian-code-list": {"levels": LEVELS.split(",")},
            "resources": [{"name": "hts", "path": "hts.parquet"}],
        }))
        result = self.run_check("--datasets-dir", str(self.dir / "datasets"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_descriptor_mode_finds_break(self):
        pkg = self.dir / "datasets" / "hts"
        pkg.mkdir(parents=True)
        self.write(ladder() + [row("0101", "heading", 1, "01")], name="x.parquet").rename(pkg / "hts.parquet")
        (pkg / "datapackage.json").write_text(json.dumps({
            "x-meridian-code-list": {"levels": LEVELS.split(",")},
            "resources": [{"name": "hts", "path": "hts.parquet"}],
        }))
        result = self.run_check("--datasets-dir", str(self.dir / "datasets"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("code-list: code-unique:", result.stdout)


if __name__ == "__main__":
    unittest.main()
