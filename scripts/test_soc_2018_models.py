#!/usr/bin/env python3
"""Run the SOC 2018 Protocol's BLS SQL models over reduced BLS workbooks and check the result.

The models are `datasets/soc_2018/models/load.sql`, `code_list.sql`, `titles.sql` and
`crosswalk.sql`; the title index and crosswalk are also run through the shared contract
checks in `contracts/`, behind the `check_inputs_*.sql` models. Each case copies
the fixtures in `scripts/testdata/soc_2018/` into a scratch `build/bls/`, runs `load` and
`code_list` on one DuckDB connection with the scratch directory as its file search path, as
the Protocol's `sql:` steps run them, exports the terminal table to `build/stage/` as the
export step does, and runs `check_code_list.py` over that file. `DescriptorTest` holds the
three committed `<table>.datapackage.json` files to the tables built from the fixtures and
to their overrides, since CI's descriptor sweeps do not read them before they are published.

The fixtures are cut from BLS's SOC 2018 downloads of 2026-11-03 (see the README beside
them): major groups 11 and 15, the minor groups 11-3000, 11-9000 and 15-1200, and every
broad group and detailed occupation under them, with BLS's own title block and headers.

Needs duckdb==1.5.5 and openpyxl in the running Python:

    uv run --quiet --with duckdb==1.5.5 --with openpyxl python3 scripts/test_soc_2018_models.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import duckdb
import openpyxl

REPO = Path(__file__).resolve().parent.parent
MODELS = [REPO / "datasets" / "soc_2018" / "models" / name for name in ("load.sql", "code_list.sql", "titles.sql", "crosswalk.sql")]
MODELS_DIR = REPO / "datasets" / "soc_2018" / "models"
CONTRACTS = REPO / "contracts"
DATA = Path(__file__).resolve().parent / "testdata" / "soc_2018"
CHECK = Path(__file__).resolve().parent / "check_code_list.py"
LEVELS = "major,minor,broad,detailed"
DEFINITIONS = "soc_2018_definitions.xlsx"
TITLES = "soc_2018_direct_match_title_file.xlsx"
CROSSWALK = "soc_2010_to_2018_crosswalk.xlsx"
FILES = (DEFINITIONS, TITLES, CROSSWALK)


class Run:
    """A scratch Protocol directory whose `build/bls/` holds the three BLS workbooks."""

    def __init__(self, edit=None, file: str = DEFINITIONS):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.build = self.root / "build"
        (self.build / "bls").mkdir(parents=True)
        (self.build / "stage").mkdir()
        for name in FILES:
            shutil.copy(DATA / name, self.build / "bls" / name)
        if edit:
            edit(self.build / "bls" / file)
        self.con = duckdb.connect()
        self.con.execute("SET file_search_path = ?", [str(self.root)])

    def load(self) -> None:
        for model in MODELS:
            self.con.execute(model.read_text(encoding="utf-8"))

    def export(self) -> Path:
        out = self.build / "stage" / "soc_2018.parquet"
        self.con.execute(f"COPY (FROM soc_2018_out ORDER BY code) TO '{out}' (FORMAT parquet)")
        return out

    def export_all(self) -> None:
        """Export the three out tables to the paths the check_inputs models read."""
        for table, name in [("soc_2018_out", "soc_2018"), ("soc_2018_titles_out", "soc_2018_titles"),
                            ("soc_2010_to_2018_out", "soc_2010_to_2018")]:
            self.con.execute(f"COPY (FROM {table}) TO '{self.build / 'stage' / (name + '.parquet')}' (FORMAT parquet)")

    def check(self, inputs: str, contract: str) -> None:
        """Run the code-list and table check_inputs models, as the Protocol's steps do, then a
        shared contract check; raises duckdb.Error on a broken rule."""
        self.con.execute((MODELS_DIR / "check_inputs_code_list.sql").read_text(encoding="utf-8"))
        self.con.execute((MODELS_DIR / inputs).read_text(encoding="utf-8"))
        self.con.execute((CONTRACTS / contract).read_text(encoding="utf-8"))

    def close(self) -> None:
        self.con.close()
        self.tmp.cleanup()


def edit_sheet(path: Path, change) -> None:
    wb = openpyxl.load_workbook(path)
    change(wb.active)
    wb.save(path)


def delete_code(code: str):
    def edit(path: Path) -> None:
        def change(ws):
            for row in range(ws.max_row, 0, -1):
                if ws.cell(row, 2).value == code:
                    ws.delete_rows(row)
        edit_sheet(path, change)
    return edit


class BuildTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = Run()
        cls.scratch.load()
        cls.parquet = cls.scratch.export()
        cls.scratch.export_all()
        cls.con = cls.scratch.con

    @classmethod
    def tearDownClass(cls):
        cls.scratch.close()

    def row(self, code: str) -> dict:
        cur = self.con.execute("SELECT * FROM soc_2018_out WHERE code = ?", [code])
        names = [d[0] for d in cur.description]
        found = cur.fetchall()
        self.assertEqual(len(found), 1, f"{code}: expected one row, found {len(found)}")
        return dict(zip(names, found[0]))

    def test_code_list_passes_contract(self):
        proc = subprocess.run([sys.executable, str(CHECK), "--parquet", str(self.parquet),
                               "--levels", LEVELS], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_columns_lead_with_the_ten_core_columns(self):
        names = [d[0] for d in self.con.execute("SELECT * FROM soc_2018_out").description]
        self.assertEqual(names, [
            "code", "code_display", "level", "depth", "parent_code", "description",
            "description_full", "release", "text_source", "corpus",
            "definition", "major_code", "minor_code", "broad_code"])

    def test_parents(self):
        for code, parent in [("15-1211", "15-1210"), ("15-1210", "15-1200"), ("11-3010", "11-3000"),
                             ("11-3000", "11-0000"), ("11-0000", None)]:
            self.assertEqual(self.row(code)["parent_code"], parent, code)

    def test_a_detailed_code_under_a_broad_group_with_a_different_last_digit(self):
        # BLS's 29-1210 holds 29-1211 to 29-1229; zeroing the last digit would name 29-1220.
        def add(path):
            def change(ws):
                for row in [("Major", "29-0000", "Healthcare Practitioners", ""),
                            ("Minor", "29-1000", "Diagnosing Practitioners", ""),
                            ("Broad", "29-1210", "Physicians", ""),
                            ("Detailed", "29-1211", "Anesthesiologists", "x"),
                            ("Detailed", "29-1221", "Pediatricians, General", "y"),
                            ("Broad", "29-1240", "Surgeons", ""),
                            ("Detailed", "29-1241", "Ophthalmologists", "z")]:
                    ws.append(list(row))
            edit_sheet(path, change)
        scratch = Run(add)
        try:
            scratch.load()
            got = dict(scratch.con.execute(
                "SELECT code, parent_code FROM soc_2018_out WHERE code LIKE '29-%'").fetchall())
            self.assertEqual(got["29-1221"], "29-1210")
            self.assertEqual(got["29-1241"], "29-1240")
        finally:
            scratch.close()

    def test_levels_and_depths(self):
        for code, level, depth in [("11-0000", "major", 0), ("11-3000", "minor", 1),
                                   ("11-3010", "broad", 2), ("11-3012", "detailed", 3)]:
            r = self.row(code)
            self.assertEqual((r["level"], r["depth"]), (level, depth), code)

    def test_ancestors(self):
        r = self.row("15-1211")
        self.assertEqual((r["major_code"], r["minor_code"], r["broad_code"]),
                         ("15-0000", "15-1200", "15-1210"))
        r = self.row("15-1210")
        self.assertEqual((r["major_code"], r["minor_code"], r["broad_code"]), ("15-0000", "15-1200", None))
        r = self.row("15-1200")
        self.assertEqual((r["major_code"], r["minor_code"], r["broad_code"]), ("15-0000", None, None))
        r = self.row("15-0000")
        self.assertEqual((r["major_code"], r["minor_code"], r["broad_code"]), (None, None, None))

    def test_release(self):
        self.assertEqual(self.con.execute("SELECT DISTINCT release FROM soc_2018_out").fetchall(),
                         [("2018/2020-04-15",)])

    def test_staging_releases(self):
        self.assertEqual(self.con.execute("SELECT release, crosswalk_release FROM bls_release").fetchall(),
                         [("2018/2020-04-15", "2017-11")])

    def test_definition_only_on_detailed(self):
        rows = self.con.execute(
            "SELECT level, count(*) FILTER (definition IS NOT NULL), count(*) FROM soc_2018_out GROUP BY level"
        ).fetchall()
        for level, with_definition, total in rows:
            self.assertEqual(with_definition, total if level == "detailed" else 0, level)
        self.assertIn("detailed", [r[0] for r in rows])
        self.assertTrue(self.row("11-3012")["definition"].startswith("Plan, direct, or coordinate"))

    def test_code_display_equals_code(self):
        self.assertEqual(self.con.execute(
            "SELECT count(*) FROM soc_2018_out WHERE code_display IS DISTINCT FROM code").fetchone()[0], 0)

    def test_text_and_description_full(self):
        r = self.row("11-3012")
        self.assertEqual(r["description"], "Administrative Services Managers")
        self.assertEqual(r["description_full"], " › ".join([
            "Management Occupations", "Operations Specialties Managers",
            "Administrative Services and Facilities Managers", "Administrative Services Managers"]))
        self.assertEqual(self.row("11-0000")["description_full"], "Management Occupations")
        self.assertEqual(r["corpus"], r["description_full"].lower())
        self.assertEqual({x[0] for x in self.con.execute("SELECT text_source FROM soc_2018_out").fetchall()},
                         {"bls"})

    def test_repeated_titles_are_kept(self):
        # A broad group and its only detailed occupation share a title (ruling R2).
        r = self.row("15-1211")
        self.assertEqual(r["description_full"].split(" › ")[-2:],
                         [self.row("15-1210")["description"], r["description"]])
        self.assertEqual(len(r["description_full"].split(" › ")), 4)

    def test_sheet_row_is_the_workbook_row(self):
        # 15-1210 is cell B68 of the fixture; a read that loses sheet order changes this.
        self.assertEqual(self.con.execute(
            "SELECT sheet_row FROM bls_definitions WHERE code = '15-1210'").fetchone()[0], 68)
        self.assertEqual(self.con.execute(
            "SELECT sheet_row FROM bls_definitions WHERE code = '11-0000'").fetchone()[0], 9)

    def test_staging_tables(self):
        counts = dict(self.con.execute(
            "SELECT 'defs', count(*) FROM bls_definitions UNION ALL SELECT 'titles', count(*) FROM bls_titles "
            "UNION ALL SELECT 'xwalk', count(*) FROM bls_crosswalk").fetchall())
        self.assertEqual(counts, {"defs": 80, "titles": 308, "xwalk": 51})
        self.assertEqual(self.con.execute(
            "SELECT code, direct_match_title, illustrative FROM bls_titles "
            "WHERE direct_match_title IN ('Records Management Director', 'Business Office Manager') "
            "AND code = '11-3012' ORDER BY direct_match_title").fetchall(),
            [("11-3012", "Business Office Manager", None), ("11-3012", "Records Management Director", "x")])
        self.assertEqual(self.con.execute(
            "SELECT count(*) FILTER (illustrative = 'x') FROM bls_titles").fetchone()[0], 145)

    def titles(self, where: str = "true") -> list[dict]:
        cur = self.con.execute(f"SELECT * FROM soc_2018_titles_out WHERE {where}")
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]

    def test_titles_pass_contract(self):
        self.scratch.check("check_inputs_titles.sql", "check_titles.sql")
        n = self.con.execute("SELECT rows_checked FROM contract_titles_result").fetchone()[0]
        self.assertEqual(n, 308)

    def test_crosswalk_pass_contract(self):
        self.scratch.check("check_inputs_crosswalk.sql", "check_crosswalk.sql")
        n = self.con.execute("SELECT rows_checked FROM contract_crosswalk_result").fetchone()[0]
        self.assertEqual(n, 51)

    def test_titles_columns(self):
        names = [d[0] for d in self.con.execute("SELECT * FROM soc_2018_titles_out").description]
        self.assertEqual(names, ["entry_id", "title", "code", "code_description", "release",
                                 "text_source", "corpus", "illustrative"])

    def test_entry_id_form(self):
        # The fixture holds no 11-1011; the form is `<code>|<title>`.
        self.assertEqual(len(self.titles("entry_id = '11-3012|Business Office Manager'")), 1)
        self.assertEqual(self.con.execute(
            "SELECT count(*) FROM soc_2018_titles_out WHERE entry_id != code || '|' || title").fetchone()[0], 0)

    def test_illustrative(self):
        self.assertEqual(self.con.execute(
            "SELECT count(*) FILTER (illustrative), count(*) FILTER (NOT illustrative), count(*) FILTER (illustrative IS NULL) "
            "FROM soc_2018_titles_out").fetchone(), (145, 163, 0))
        by_title = {r["title"]: r["illustrative"] for r in self.titles("code = '11-3012'")}
        self.assertIs(by_title["Records Management Director"], True)
        self.assertIs(by_title["Business Office Manager"], False)

    def test_code_description(self):
        self.assertEqual(self.con.execute(
            "SELECT count(*) FROM soc_2018_titles_out t JOIN soc_2018_out c USING (code) "
            "WHERE t.code_description IS DISTINCT FROM c.description").fetchone()[0], 0)
        self.assertEqual(self.titles("code = '11-3012'")[0]["code_description"], "Administrative Services Managers")

    def test_titles_text_and_release(self):
        self.assertEqual(self.con.execute(
            "SELECT DISTINCT release, text_source FROM soc_2018_titles_out").fetchall(),
            [("2018/2020-04-15", "bls")])
        self.assertEqual(self.con.execute(
            "SELECT count(*) FROM soc_2018_titles_out WHERE corpus != lower(strip_accents(regexp_replace(title, '\\s+', ' ', 'g')))").fetchone()[0], 0)

    def test_corpus_collapses_whitespace_but_title_keeps_it(self):
        # The fixture holds no double-spaced title, so make one.
        def edit(path):
            def change(ws):
                for row in range(9, ws.max_row + 1):
                    if ws.cell(row, 3).value == "Business Office Manager":
                        ws.cell(row, 3).value = "Business  Office Manager"
            edit_sheet(path, change)
        scratch = Run(edit, TITLES)
        try:
            scratch.load()
            self.assertEqual(scratch.con.execute(
                "SELECT entry_id, title, corpus FROM soc_2018_titles_out WHERE code = '11-3012' AND title LIKE 'Business%Office Manager'"
            ).fetchall(), [("11-3012|Business  Office Manager", "Business  Office Manager", "business office manager")])
        finally:
            scratch.close()

    def test_crosswalk_pairs(self):
        self.assertEqual(self.con.execute("SELECT count(*) FROM soc_2010_to_2018_out").fetchone()[0],
                         self.con.execute("SELECT count(*) FROM (SELECT DISTINCT code_2010, code_2018 FROM bls_crosswalk)").fetchone()[0])
        self.assertEqual(self.con.execute(
            "SELECT DISTINCT release, from_edition, to_edition FROM soc_2010_to_2018_out").fetchall(),
            [("2017-11", "2010", "2018")])
        names = [d[0] for d in self.con.execute("SELECT * FROM soc_2010_to_2018_out").description]
        self.assertEqual(names, ["from_code", "from_description", "to_code", "to_description",
                                 "from_edition", "to_edition", "release"])

    def test_crosswalk_descriptions_are_bls_titles(self):
        self.assertEqual(self.con.execute(
            "SELECT count(*) FROM soc_2010_to_2018_out x JOIN bls_crosswalk b "
            "ON b.code_2010 = x.from_code AND b.code_2018 = x.to_code "
            "WHERE x.from_description IS DISTINCT FROM b.title_2010 OR x.to_description IS DISTINCT FROM b.title_2018"
        ).fetchone()[0], 0)


class RefusalTest(unittest.TestCase):
    def refuses(self, edit, message: str, file: str = DEFINITIONS) -> None:
        scratch = Run(edit, file)
        try:
            with self.assertRaises(duckdb.Error) as caught:
                scratch.load()
            self.assertIn(message, str(caught.exception))
        finally:
            scratch.close()

    def test_release_unparseable_fails(self):
        self.refuses(lambda p: edit_sheet(p, lambda ws: ws.cell(4, 1).__setattr__("value", "Some day")),
                     "release")

    def test_release_without_update_date_fails(self):
        self.refuses(lambda p: edit_sheet(p, lambda ws: ws.cell(4, 1).__setattr__(
            "value", "November 2017 (for reference year January 2018)")), "release")

    def test_moved_header_fails(self):
        self.refuses(lambda p: edit_sheet(p, lambda ws: ws.cell(8, 2).__setattr__("value", "Code")),
                     "header")

    def test_titles_update_date_mismatch_fails(self):
        self.refuses(lambda p: edit_sheet(p, lambda ws: ws.cell(4, 1).__setattr__(
            "value", "November 2017 (Updated April 16, 2020)")), "update date", TITLES)

    def test_titles_release_unparseable_fails(self):
        self.refuses(lambda p: edit_sheet(p, lambda ws: ws.cell(4, 1).__setattr__("value", "Some day")),
                     "update date", TITLES)

    def test_crosswalk_release_unparseable_fails(self):
        self.refuses(lambda p: edit_sheet(p, lambda ws: ws.cell(4, 1).__setattr__("value", "Autumn 2017")),
                     "crosswalk release", CROSSWALK)

    def test_titles_moved_header_fails(self):
        self.refuses(lambda p: edit_sheet(p, lambda ws: ws.cell(8, 3).__setattr__("value", "Title")),
                     "direct match title header", TITLES)

    def test_crosswalk_moved_header_fails(self):
        # The crosswalk's headers are row 9; a row inserted above moves them to row 10.
        self.refuses(lambda p: edit_sheet(p, lambda ws: ws.insert_rows(5)),
                     "crosswalk header", CROSSWALK)

    def test_non_first_broad_group_miscoded_fails(self):
        # With 15-1220 recoded 15-1330, 15-1221 falls to 15-1210 by code, but the last broad
        # group above it in BLS's sheet is the miscoded one.
        def recode(path):
            def change(ws):
                for row in range(1, ws.max_row + 1):
                    if ws.cell(row, 2).value == "15-1220":
                        ws.cell(row, 2).value = "15-1330"
            edit_sheet(path, change)
        self.refuses(recode, "not under it in BLS's sheet order")

    def test_a_broad_group_out_of_sheet_order_fails(self):
        # 11-3010 and its occupations moved to the end of the sheet: it falls to 11-3000 by
        # code, but the last minor group above it in the sheet is 15-1200.
        def move(path):
            def change(ws):
                rows = [r for r in range(1, ws.max_row + 1)
                        if str(ws.cell(r, 2).value).startswith("11-301")]
                moved = [[ws.cell(r, c).value for c in range(1, 5)] for r in rows]
                for r in reversed(rows):
                    ws.delete_rows(r)
                for values in moved:
                    ws.append(values)
            edit_sheet(path, change)
        self.refuses(move, "broad 11-3010 has parent 11-3000 by code, but is not under it")

    def test_dropped_non_first_minor_group_fails_for_want_of_a_parent(self):
        self.refuses(delete_code("11-9000"), "no parent")

    def test_broad_without_minor_fails(self):
        self.refuses(delete_code("11-3000"), "no parent")

    def test_detailed_without_broad_fails(self):
        self.refuses(delete_code("15-1210"), "no parent")


PROTOCOL = REPO / "datasets" / "soc_2018"
# Each committed descriptor, the out table the models build for it, its primary key and
# the declaration that names its contract.
DESCRIPTORS = (
    ("soc_2018", "soc_2018_out", ["code"],
     "x-meridian-code-list", {"levels": ["major", "minor", "broad", "detailed"]}),
    ("soc_2018_titles", "soc_2018_titles_out", ["entry_id"],
     "x-meridian-title-index", {"codeList": "bls/soc_2018"}),
    ("soc_2010_to_2018", "soc_2010_to_2018_out", ["from_code", "to_code"],
     "x-meridian-crosswalk", {"from": "2010", "to": "2018", "toCodeList": "bls/soc_2018"}),
)


class DescriptorTest(unittest.TestCase):
    """The committed `<table>.datapackage.json` files, which CI's descriptor sweeps do not
    read until they are published, held to the tables the models build and to their
    overrides: field order, primary key, contract declaration, licence and descriptions."""

    @classmethod
    def setUpClass(cls):
        cls.scratch = Run()
        cls.scratch.load()
        cls.con = cls.scratch.con

    @classmethod
    def tearDownClass(cls):
        cls.scratch.close()

    def documents(self, table: str) -> tuple[dict, dict]:
        descriptor = json.loads((PROTOCOL / f"{table}.datapackage.json").read_text(encoding="utf-8"))
        overrides = json.loads((PROTOCOL / f"{table}.overrides.json").read_text(encoding="utf-8"))
        return descriptor, overrides

    def test_fields_are_the_out_tables_columns_in_order(self):
        for table, out, *_ in DESCRIPTORS:
            with self.subTest(table=table):
                descriptor, _ = self.documents(table)
                fields = [f["name"] for f in descriptor["resources"][0]["schema"]["fields"]]
                columns = [d[0] for d in self.con.execute(f"SELECT * FROM {out} LIMIT 0").description]
                self.assertEqual(fields, columns)

    def test_primary_key(self):
        for table, _, key, *_ in DESCRIPTORS:
            with self.subTest(table=table):
                descriptor, overrides = self.documents(table)
                self.assertEqual(descriptor["resources"][0]["schema"]["primaryKey"], key)
                self.assertEqual(overrides["primaryKey"], key)

    def test_contract_declaration(self):
        for table, _, _, name, value in DESCRIPTORS:
            with self.subTest(table=table):
                descriptor, overrides = self.documents(table)
                self.assertEqual(descriptor.get(name), value)
                self.assertEqual(overrides.get(name), value)
                others = {d[3] for d in DESCRIPTORS} - {name}
                self.assertFalse(others & descriptor.keys(), f"{table} declares another table's contract")

    def test_licence_is_public_domain_and_matches_the_overrides(self):
        for table, *_ in DESCRIPTORS:
            with self.subTest(table=table):
                descriptor, overrides = self.documents(table)
                self.assertEqual([lic["name"] for lic in descriptor["licenses"]], ["other-pd"])
                self.assertEqual(descriptor["licenses"], overrides["licenses"])

    def test_every_field_has_the_overrides_description(self):
        for table, *_ in DESCRIPTORS:
            with self.subTest(table=table):
                descriptor, overrides = self.documents(table)
                fields = descriptor["resources"][0]["schema"]["fields"]
                self.assertEqual([f["name"] for f in fields], list(overrides["fields"]))
                for field in fields:
                    self.assertTrue(field.get("description", "").strip(), f"{table}.{field['name']} has no description")
                    self.assertEqual(field["description"], overrides["fields"][field["name"]]["description"])


if __name__ == "__main__":
    unittest.main()
