#!/usr/bin/env python3
"""Self-test for check_lake_shape.py: the shape rule, and the CLI against a real local DuckLake."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import duckdb

CHECKER = Path(__file__).with_name("check_lake_shape.py")
sys.path.insert(0, str(CHECKER.parent))

from check_lake_shape import shape_difference  # noqa: E402


class ShapeRule(unittest.TestCase):
    def test_identical_columns_pass(self) -> None:
        self.assertEqual(
            shape_difference([("code", "VARCHAR")], [("code", "VARCHAR")]), []
        )

    def test_trailing_new_column_passes(self) -> None:
        self.assertEqual(
            shape_difference(
                [("code", "VARCHAR")], [("code", "VARCHAR"), ("x", "INTEGER")]
            ),
            [],
        )

    def test_type_change_is_named(self) -> None:
        out = shape_difference([("code", "VARCHAR")], [("code", "INTEGER")])
        self.assertEqual(len(out), 1)
        for word in ("code", "VARCHAR", "INTEGER"):
            self.assertIn(word, out[0])

    def test_dropped_column_is_named(self) -> None:
        out = shape_difference([("a", "VARCHAR"), ("b", "VARCHAR")], [("a", "VARCHAR")])
        self.assertEqual(len(out), 1)
        self.assertIn("b", out[0])
        self.assertIn("dropped", out[0])

    def test_reorder_is_named(self) -> None:
        out = shape_difference(
            [("a", "VARCHAR"), ("b", "VARCHAR")], [("b", "VARCHAR"), ("a", "VARCHAR")]
        )
        self.assertEqual(len(out), 1)
        self.assertIn("reordered", out[0])

    def test_column_added_before_last_is_named(self) -> None:
        out = shape_difference(
            [("a", "VARCHAR"), ("b", "VARCHAR")],
            [("a", "VARCHAR"), ("n", "INTEGER"), ("b", "VARCHAR")],
        )
        self.assertEqual(len(out), 1)
        self.assertIn("n", out[0])
        self.assertIn("before", out[0])


class Cli(unittest.TestCase):
    def run_cli(
        self, lake: Path, parquet: Path, table: str = "s.t"
    ) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                sys.executable,
                str(CHECKER),
                "--catalog",
                str(lake),
                "--table",
                table,
                "--parquet",
                str(parquet),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_cli_against_local_lake(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lake = root / "t.ducklake"
            con = duckdb.connect()
            con.execute("INSTALL ducklake; LOAD ducklake")
            con.execute(f"ATTACH 'ducklake:{lake}' AS l (DATA_PATH '{root / 'data'}/')")
            con.execute("CREATE SCHEMA l.s")
            con.execute(
                "CREATE TABLE l.s.t AS SELECT 'c' || i AS code, i::INTEGER AS n FROM range(10) r(i)"
            )
            con.execute("DETACH l")

            def pq(name: str, sql: str) -> Path:
                p = root / name
                con.execute(f"COPY ({sql}) TO '{p}' (FORMAT parquet)")
                return p

            same = pq(
                "same.parquet",
                "SELECT 'c' || i AS code, i::INTEGER AS n FROM range(10) r(i)",
            )
            r = self.run_cli(lake, same)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("shape ok: s.t 2 columns, rows 10 -> 10", r.stdout)

            wide = pq(
                "wide.parquet",
                "SELECT 'c' || i AS code, i::BIGINT AS n FROM range(10) r(i)",
            )
            r = self.run_cli(lake, wide)
            self.assertEqual(r.returncode, 1)
            self.assertIn("n", r.stdout + r.stderr)
            self.assertIn("BIGINT", r.stdout + r.stderr)

            more = pq(
                "more.parquet",
                "SELECT 'c' || i AS code, i::INTEGER AS n FROM range(20) r(i)",
            )
            r = self.run_cli(lake, more)
            self.assertEqual(r.returncode, 1)
            self.assertIn("rows 10 -> 20", r.stdout + r.stderr)

            r = self.run_cli(lake, same, table="s.absent")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("new table", r.stdout)


if __name__ == "__main__":
    unittest.main()
