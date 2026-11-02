#!/usr/bin/env python3
"""Run the HCPCS Protocol's SQL models over a reduced CMS file and check the result.

The models are `datasets/hcpcs/models/load.sql`, `package.sql` and `check_licence.sql`.
Each case copies the fixture in `scripts/testdata/hcpcs/` into a scratch
`build/hcpcs/`, runs `load` and `package` on one DuckDB connection with the scratch
directory as its file search path, as the Protocol's `sql:` steps run them, exports the
terminal table to `build/stage/hcpcs.parquet` as `export_hcpcs` does, and runs
`check_licence` over that file.

The fixture is cut from CMS's October 2026 alpha-numeric file
(HCPC2026_OCT_ANWEB_09232026.txt), in file order: the first modifier record (A1), A0021
(a cross-reference to A0030), A0080 (a long description on two lines), A4397
(terminated 2021-12-31), C8904, G0279 (a description that cites
CPT codes), and J0120. Three things are made up so that no CPT code is committed here:
C8904's cross-reference, a CPT code in CMS's file, is `00000`; G0279's two CPT codes are
`00000` and `0000T`; and a dental record `D9999`, which CMS's file does not carry today,
is inserted before J0120 to prove the D series is dropped.

Needs duckdb==1.5.5 in the running Python:

    uv run --quiet --with duckdb==1.5.5 python3 scripts/test_hcpcs_models.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import duckdb

REPO = Path(__file__).resolve().parent.parent
HCPCS = REPO / "datasets" / "hcpcs"
MODELS = [HCPCS / "models" / name for name in ("load.sql", "package.sql")]
LICENCE = HCPCS / "models" / "check_licence.sql"
FIXTURE = Path(__file__).resolve().parent / "testdata" / "hcpcs"
NAME = "HCPC2026_OCT_ANWEB_09232026.txt"
CHECK = Path(__file__).resolve().parent / "check_code_list.py"


class Run:
    """A scratch Protocol directory whose `build/hcpcs/` holds the fixture."""

    def __init__(self, name: str = NAME, body: str | None = None, extra: str | None = None):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.build = self.root / "build"
        (self.build / "hcpcs").mkdir(parents=True)
        (self.build / "stage").mkdir()
        text = (FIXTURE / NAME).read_bytes() if body is None else body.encode()
        (self.build / "hcpcs" / name).write_bytes(text)
        if extra is not None:
            shutil.copy(FIXTURE / NAME, self.build / "hcpcs" / extra)
        self.con = duckdb.connect()
        self.con.execute("SET file_search_path = ?", [str(self.root)])

    def load(self) -> None:
        for model in MODELS:
            self.con.execute(model.read_text(encoding="utf-8"))

    def export(self) -> Path:
        out = self.build / "stage" / "hcpcs.parquet"
        self.con.execute(f"COPY (FROM hcpcs_out ORDER BY code) TO '{out}' (FORMAT parquet)")
        return out

    def licence(self) -> None:
        self.con.execute(LICENCE.read_text(encoding="utf-8"))

    def close(self) -> None:
        self.con.close()
        self.tmp.cleanup()


def fixture_lines() -> list[str]:
    return (FIXTURE / NAME).read_bytes().decode().split("\r\n")


class BuildTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = Run()
        cls.scratch.load()
        cls.parquet = cls.scratch.export()
        cls.scratch.licence()
        cls.con = cls.scratch.con

    @classmethod
    def tearDownClass(cls):
        cls.scratch.close()

    def row(self, code: str) -> dict:
        cur = self.con.execute("SELECT * FROM hcpcs_out WHERE code = ?", [code])
        names = [d[0] for d in cur.description]
        found = cur.fetchall()
        self.assertEqual(len(found), 1, f"{code}: expected one row, found {len(found)}")
        return dict(zip(names, found[0]))

    def test_one_row_per_code_without_modifiers_or_dental(self):
        codes = [r[0] for r in self.con.execute("SELECT code FROM hcpcs_out ORDER BY code").fetchall()]
        self.assertEqual(codes, ["A0021", "A0080", "A4397", "C8904", "G0279", "J0120"])

    def test_core_columns(self):
        r = self.row("A0021")
        self.assertEqual(r["code_display"], "A0021")
        self.assertEqual(r["level"], "code")
        self.assertEqual(r["depth"], 0)
        self.assertIsNone(r["parent_code"])
        self.assertEqual(r["description"], "Ambulance service, outside state per mile, transport (medicaid only)")
        self.assertEqual(r["description_full"], r["description"])
        self.assertEqual(r["release"], "2026-10/2026-09-23")
        self.assertEqual(r["text_source"], "cms")
        self.assertEqual(r["corpus"], r["description"].lower())
        self.assertEqual(r["short_description"], "Outside state ambulance serv")

    def test_continuation_lines_join_with_a_space(self):
        self.assertEqual(
            self.row("A0080")["description"],
            "Non-emergency transportation, per mile - vehicle provided by volunteer "
            "(individual or organization), with no vested interest",
        )

    def test_cpt_numbers_in_a_description_are_redacted(self):
        r = self.row("G0279")
        self.assertEqual(r["text_source"], "cms_cpt_redacted")
        self.assertTrue(r["description"].endswith("(list separately in addition to [CPT code] or [CPT code])"))
        self.assertNotIn("00000", r["corpus"])

    def test_cross_references_keep_level_ii_codes_only(self):
        self.assertEqual(self.row("A0021")["cross_reference_codes"], ["A0030"])
        self.assertIsNone(self.row("C8904")["cross_reference_codes"])

    def test_dates_and_fields(self):
        r = self.row("A4397")
        self.assertEqual(str(r["termination_date"]), "2021-12-31")
        line = next(l for l in fixture_lines() if l.startswith("A4397") and l[10] == "3")
        self.assertEqual(r["pricing_indicators"], [line[119:121]])
        self.assertEqual(r["betos"], line[256:259].strip())
        self.assertIsNone(self.row("A0021")["termination_date"])
        self.assertEqual(str(self.row("J0120")["added_date"]), "1986-01-01")

    def test_licence_check_passes_and_counts(self):
        r = self.con.execute("SELECT result, codes, redacted FROM licence_check").fetchone()
        self.assertEqual(r, ("pass", 6, 1))

    def test_code_list_contract(self):
        done = subprocess.run(
            [sys.executable, str(CHECK), "--parquet", str(self.parquet), "--levels", "code"],
            capture_output=True, text=True,
        )
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


class RefusalTest(unittest.TestCase):
    def refuse(self, run: Run, step, message: str) -> None:
        try:
            with self.assertRaises(duckdb.Error) as caught:
                step(run)
            self.assertIn(message, str(caught.exception))
        finally:
            run.close()

    def test_two_files(self):
        self.refuse(Run(extra="HCPC2026_JUL_ANWEB_06242026.txt"), Run.load, "expected one CMS ANWEB file")

    def test_unparsed_name(self):
        self.refuse(Run(name="HCPC2026_XYZ_ANWEB_09232026.txt"), Run.load, "does not parse")

    def test_unparsed_date_in_name(self):
        self.refuse(Run(name="HCPC2026_OCT_ANWEB_092326.txt"), Run.load, "does not parse")

    def test_missing_first_record(self):
        lines = [l for l in fixture_lines() if not l.startswith("A0080001003")]
        self.refuse(Run(body="\r\n".join(lines)), Run.load, "0 first records: A0080")

    def test_duplicate_first_record(self):
        lines = fixture_lines()
        first = next(l for l in lines if l.startswith("J0120"))
        lines.insert(lines.index(first), first)
        self.refuse(Run(body="\r\n".join(lines)), Run.load, "2 first records: J0120")

    def test_malformed_code(self):
        lines = fixture_lines()
        lines[1] = "A002X" + lines[1][5:]
        self.refuse(Run(body="\r\n".join(lines)), Run.load, "not a letter and four digits")

    def test_licence_refuses_cpt_text(self):
        run = Run()
        try:
            run.load()
            run.con.execute("UPDATE hcpcs_out SET short_description = 'see 12345' WHERE code = 'J0120'")
            run.export()
        except Exception:
            run.close()
            raise
        self.refuse(run, Run.licence, "1 rows with CPT-shaped text")

    def test_licence_refuses_a_dental_code(self):
        run = Run()
        try:
            run.load()
            run.con.execute("UPDATE hcpcs_out SET code = 'D0120' WHERE code = 'J0120'")
            run.export()
        except Exception:
            run.close()
            raise
        self.refuse(run, Run.licence, "1 D codes")

    def test_licence_refuses_a_cpt_cross_reference(self):
        run = Run()
        try:
            run.load()
            run.con.execute("UPDATE hcpcs_out SET cross_reference_codes = ['0000T'] WHERE code = 'J0120'")
            run.export()
        except Exception:
            run.close()
            raise
        self.refuse(run, Run.licence, "1 rows with a CPT or D cross-reference")


if __name__ == "__main__":
    unittest.main(verbosity=2)
