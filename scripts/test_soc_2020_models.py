#!/usr/bin/env python3
"""Run the SOC 2020 Protocol's ONS SQL models over reduced ONS workbooks and check the result.

The models are `datasets/soc_2020/models/load.sql` and `code_list.sql`. Each case copies
the fixtures in `scripts/testdata/soc_2020/` into a scratch `build/ons/` under the fixed
names the Protocol fetches to, writes the `.arcmeta` sidecar `http_fetch@1` writes beside
each (its `url:` the real ONS URL), runs `load` and `code_list` on one DuckDB connection
with the scratch directory as its file search path, as the Protocol's `sql:` steps run
them, exports the terminal table to `build/stage/` as the export step does, and runs
`check_code_list.py` over that file.

The fixtures are cut from ONS's SOC 2020 downloads of 2026-11-03 (see the README beside
them): major groups 1 and 9 and every group beneath them from Volume 1, with ONS's own
headers; index rows for their unit groups from the coding index; and the relationship
blocks whose every 2020 group is one of them.

Needs duckdb==1.5.5 and openpyxl in the running Python:

    uv run --quiet --with duckdb==1.5.5 --with openpyxl python3 scripts/test_soc_2020_models.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import duckdb
import openpyxl

REPO = Path(__file__).resolve().parent.parent
MODELS = [REPO / "datasets" / "soc_2020" / "models" / name for name in ("load.sql", "code_list.sql")]
DATA = Path(__file__).resolve().parent / "testdata" / "soc_2020"
CHECK = Path(__file__).resolve().parent / "check_code_list.py"
LEVELS = "major,sub_major,minor,unit"

ONS = "https://www.ons.gov.uk/file?uri=/methodology/classificationsandstandards/standardoccupationalclassificationsoc/soc2020/"
VOLUME1_URL = ONS + "soc2020volume1structureanddescriptionsofunitgroups/soc2020volume1structureanddescriptionofunitgroupsexcel20260827.xlsx"
INDEX_URL = ONS + "soc2020volume2codingrulesandconventions/soc2020volume2thecodingindexexcel20260827v2.xlsx"
RELATIONSHIP_URL = ONS + "therelationshipbetweenstandardoccupationalclassification2010andstandardoccupationalclassification2020/soc2010soc2020relationshiptablesjuly2021.xlsx"

# fixture file -> (fixed path the Protocol fetches to, the URL it came from)
FILES = {
    "soc2020volume1structureanddescriptionofunitgroupsexcel20260827.xlsx": ("volume1.xlsx", VOLUME1_URL),
    "soc2020volume2thecodingindexexcel20260827v2.xlsx": ("coding_index.xlsx", INDEX_URL),
    "soc2010soc2020relationshiptablesjuly2021.xlsx": ("relationship_2010_2020.xlsx", RELATIONSHIP_URL),
}
VOLUME1, INDEX, RELATIONSHIP = "volume1.xlsx", "coding_index.xlsx", "relationship_2010_2020.xlsx"


def sidecar(url: str) -> str:
    return ("url: " + url + "\nrequest_headers:\n"
            "  User-Agent: Meridian Protocol (open-analytics; research@meridian.online)\n"
            "etag: null\nlast_modified: null\nsha256: 0\nfetched_unix: 0\n")


class Run:
    """A scratch Protocol directory whose `build/ons/` holds the three ONS workbooks."""

    def __init__(self, edit=None, file: str = VOLUME1, urls: dict | None = None, skip_sidecar: str | None = None):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.build = self.root / "build"
        (self.build / "ons").mkdir(parents=True)
        (self.build / "stage").mkdir()
        for source, (name, url) in FILES.items():
            shutil.copy(DATA / source, self.build / "ons" / name)
            if name != skip_sidecar:
                (self.build / "ons" / (name + ".arcmeta")).write_text(sidecar((urls or {}).get(name, url)), encoding="utf-8")
        if edit:
            edit(self.build / "ons" / file)
        self.con = duckdb.connect()
        self.con.execute("SET file_search_path = ?", [str(self.root)])

    def load(self) -> None:
        for model in MODELS:
            self.con.execute(model.read_text(encoding="utf-8"))

    def export(self) -> Path:
        out = self.build / "stage" / "soc_2020.parquet"
        self.con.execute(f"COPY (FROM soc_2020_out ORDER BY code) TO '{out}' (FORMAT parquet)")
        return out

    def close(self) -> None:
        self.con.close()
        self.tmp.cleanup()


def edit_sheet(path: Path, change, sheet: str | None = None) -> None:
    wb = openpyxl.load_workbook(path)
    change(wb[sheet] if sheet else wb.active)
    wb.save(path)


def delete_code(column: int, code: int):
    """Delete the Volume 1 row whose code cell in `column` (2 to 5) is `code`."""
    def edit(path: Path) -> None:
        def change(ws):
            for row in range(ws.max_row, 0, -1):
                if ws.cell(row, column).value == code:
                    ws.delete_rows(row)
        edit_sheet(path, change)
    return edit


class BuildTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = Run()
        cls.scratch.load()
        cls.parquet = cls.scratch.export()
        cls.con = cls.scratch.con

    @classmethod
    def tearDownClass(cls):
        cls.scratch.close()

    def row(self, code: str) -> dict:
        cur = self.con.execute("SELECT * FROM soc_2020_out WHERE code = ?", [code])
        names = [d[0] for d in cur.description]
        found = cur.fetchall()
        self.assertEqual(len(found), 1, f"{code}: expected one row, found {len(found)}")
        return dict(zip(names, found[0]))

    def test_code_list_passes_contract(self):
        proc = subprocess.run([sys.executable, str(CHECK), "--parquet", str(self.parquet),
                               "--levels", LEVELS], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_columns_lead_with_the_ten_core_columns(self):
        names = [d[0] for d in self.con.execute("SELECT * FROM soc_2020_out").description]
        self.assertEqual(names, [
            "code", "code_display", "level", "depth", "parent_code", "description",
            "description_full", "release", "text_source", "corpus",
            "group_description", "entry_routes", "tasks", "related_job_titles",
            "major_code", "sub_major_code", "minor_code"])

    def test_codes_are_text(self):
        self.assertIn("1111", [r[0] for r in self.con.execute("SELECT code FROM soc_2020_out").fetchall()])
        self.assertEqual(self.con.execute(
            "SELECT count(*) FROM soc_2020_out WHERE NOT regexp_matches(code, '^[0-9]{1,4}$')").fetchone()[0], 0)
        for table, columns in (("soc_2020_out", "code, parent_code, major_code, sub_major_code, minor_code, "
                                                "description, group_description, entry_routes"),
                               ("ons_groups", "code, title, group_description, entry_routes")):
            for column in columns.split(", "):
                self.assertEqual(self.con.execute(
                    f"SELECT count(*) FROM {table} WHERE {column} LIKE '%<blank>%' OR {column} LIKE '%.0'").fetchone()[0],
                    0, f"{table}.{column}")
        self.assertEqual(self.con.execute(
            "SELECT count(*) FROM soc_2020_out WHERE list_contains(tasks, '<blank>') "
            "OR list_contains(related_job_titles, '<blank>')").fetchone()[0], 0)

    def test_parents_by_prefix(self):
        for code, parent in [("1111", "111"), ("111", "11"), ("11", "1"), ("1", None),
                             ("9223", "922"), ("92", "9")]:
            self.assertEqual(self.row(code)["parent_code"], parent, code)

    def test_levels_and_depths(self):
        for code, level, depth in [("1", "major", 0), ("11", "sub_major", 1),
                                   ("111", "minor", 2), ("1111", "unit", 3)]:
            r = self.row(code)
            self.assertEqual((r["level"], r["depth"]), (level, depth), code)

    def test_group_counts(self):
        self.assertEqual(dict(self.con.execute("SELECT level, count(*) FROM soc_2020_out GROUP BY level").fetchall()),
                         {"major": 2, "sub_major": 4, "minor": 21, "unit": 76})

    def test_ancestors(self):
        r = self.row("1111")
        self.assertEqual((r["major_code"], r["sub_major_code"], r["minor_code"]), ("1", "11", "111"))
        r = self.row("111")
        self.assertEqual((r["major_code"], r["sub_major_code"], r["minor_code"]), ("1", "11", None))
        r = self.row("11")
        self.assertEqual((r["major_code"], r["sub_major_code"], r["minor_code"]), ("1", None, None))
        r = self.row("1")
        self.assertEqual((r["major_code"], r["sub_major_code"], r["minor_code"]), (None, None, None))

    def test_tasks_split(self):
        tasks = self.row("1111")["tasks"]
        self.assertTrue(tasks[0].startswith("analyses economic"), tasks[0])
        self.assertTrue(all(not t.startswith("~") and t == t.strip() and t for t in tasks))
        self.assertEqual(len(tasks), 9)
        titles = self.row("1111")["related_job_titles"]
        self.assertEqual(titles, ["Chairman", "Chief executive", "Civil servant (grade 5 & above)",
                                  "Diplomat", "Vice President"])

    def test_lists_are_null_above_unit_groups(self):
        for code in ("1", "11", "111"):
            r = self.row(code)
            self.assertEqual((r["entry_routes"], r["tasks"], r["related_job_titles"]), (None, None, None), code)
            self.assertTrue(r["group_description"], code)
        self.assertTrue(self.row("1111")["entry_routes"].startswith("Entry may be by appointment"))

    def test_description_not_capitalised(self):
        self.assertEqual(self.row("11")["description"], "Corporate managers and directors")
        self.assertEqual(self.row("1")["description"], "Managers, directors and senior officials")

    def test_text_and_description_full(self):
        r = self.row("1111")
        self.assertEqual(r["description_full"], " › ".join([
            "Managers, directors and senior officials", "Corporate managers and directors",
            "Chief executives and senior officials", "Chief executives and senior officials"]))
        self.assertEqual(self.row("1")["description_full"], "Managers, directors and senior officials")
        self.assertEqual(r["corpus"], r["description_full"].lower())
        self.assertEqual(self.con.execute("SELECT DISTINCT text_source FROM soc_2020_out").fetchall(), [("ons",)])
        self.assertEqual(self.con.execute(
            "SELECT count(*) FROM soc_2020_out WHERE code_display IS DISTINCT FROM code").fetchone()[0], 0)

    def test_release(self):
        self.assertEqual(self.con.execute("SELECT DISTINCT release FROM soc_2020_out").fetchall(),
                         [("2020/2026-08-27",)])

    def test_staging_releases(self):
        self.assertEqual(self.con.execute(
            "SELECT code_list_release, index_release, crosswalk_release FROM ons_release").fetchall(),
            [("2020/2026-08-27", "v14/2026-08-27", "2021-07")])

    def test_staging_tables(self):
        counts = dict(self.con.execute(
            "SELECT 'groups', count(*) FROM ons_groups UNION ALL SELECT 'index', count(*) FROM ons_index "
            "UNION ALL SELECT 'relationship', count(*) FROM ons_relationship").fetchall())
        self.assertEqual(counts, {"groups": 103, "index": 188, "relationship": 21})

    def test_index_staging(self):
        names = [d[0] for d in self.con.execute("SELECT * FROM ons_index").description]
        self.assertEqual(names, [
            "entry_id", "title", "additional_qualifier", "industry_qualifier", "title_natural", "code",
            "default_flag", "soc_2020_ext", "soc_2020_ext_title", "soc_2010", "soc_2000", "soc_1990",
            "isco_08", "version_added_or_amended", "change"])
        # Cross-references are not entries; the two titles that repeat are told apart by id.
        self.assertEqual(self.con.execute(
            "SELECT count(*) FROM ons_index WHERE entry_id IN ('a1pc', 'a2cj', 'e9se', 'd3ak')").fetchone()[0], 0)
        self.assertEqual(self.con.execute(
            "SELECT count(*) FROM ons_index WHERE title = 'President, vice'").fetchone()[0], 1)
        a = self.con.execute("SELECT * FROM ons_index WHERE entry_id = 'd5zz'").fetchone()
        self.assertEqual(a[5], "1111")
        self.assertEqual(self.con.execute("SELECT count(*) FROM ons_index WHERE code NOT IN "
                                          "(SELECT code FROM ons_groups WHERE level = 'unit')").fetchone()[0], 0)
        self.assertEqual(self.con.execute(
            "SELECT count(*) FROM ons_index WHERE soc_2010 LIKE '%}%' OR soc_2000 LIKE '%}%' OR soc_1990 LIKE '%}%' "
            "OR soc_2020_ext LIKE '%}%' OR isco_08 LIKE '%}%'").fetchone()[0], 0)

    def test_relationship_staging(self):
        names = [d[0] for d in self.con.execute("SELECT * FROM ons_relationship").description]
        self.assertEqual(names, [
            "code_2010", "title_2010", "code_2020", "title_2020",
            "men_lfs_base", "men_lfs_share", "men_census_base", "men_census_share",
            "women_lfs_base", "women_lfs_share", "women_census_base", "women_census_share"])
        r = self.con.execute("SELECT * FROM ons_relationship WHERE code_2010 = '1116' AND code_2020 = '1111'").fetchone()
        self.assertEqual(r[:5], ("1116", "Elected officers and representatives", "1111",
                                 "Chief executives and senior officials", 48))
        self.assertEqual((r[5], r[6], r[8], r[9]), (0.0, 98, 25, 0.0))
        self.assertAlmostEqual(r[7], 0.01020408163265306)
        self.assertEqual(self.con.execute(
            "SELECT count(*) FROM ons_relationship WHERE code_2010 = '1116'").fetchone()[0], 2)
        self.assertEqual(self.con.execute(
            "SELECT count(*) FROM ons_relationship WHERE code_2020 NOT IN "
            "(SELECT code FROM ons_groups WHERE level = 'unit')").fetchone()[0], 0)
        self.assertEqual(self.con.execute(
            "SELECT count(*) FROM ons_relationship WHERE men_lfs_base IS NULL OR women_census_base IS NULL "
            "OR men_lfs_share IS NULL OR women_census_share IS NULL").fetchone()[0], 0)


class ReleaseTest(unittest.TestCase):
    def build(self, **kwargs) -> Run:
        scratch = Run(**kwargs)
        self.addCleanup(scratch.close)
        return scratch

    def test_release_from_sidecar_url(self):
        scratch = self.build(urls={VOLUME1: VOLUME1_URL.replace("20260827", "20270115")})
        scratch.load()
        self.assertEqual(scratch.con.execute("SELECT DISTINCT release FROM soc_2020_out").fetchall(),
                         [("2020/2027-01-15",)])
        self.assertEqual(scratch.con.execute("SELECT code_list_release FROM ons_release").fetchone()[0],
                         "2020/2027-01-15")

    def test_index_release_from_sidecar_url(self):
        scratch = self.build(urls={INDEX: INDEX_URL.replace("20260827v2", "20270115v3")})
        scratch.load()
        self.assertEqual(scratch.con.execute("SELECT index_release FROM ons_release").fetchone()[0],
                         "v14/2027-01-15")

    def test_relationship_release_from_url(self):
        scratch = self.build(urls={RELATIONSHIP: RELATIONSHIP_URL.replace("july2021", "december2022")})
        scratch.load()
        self.assertEqual(scratch.con.execute("SELECT crosswalk_release FROM ons_release").fetchone()[0], "2022-12")
        scratch = self.build()
        scratch.load()
        self.assertEqual(scratch.con.execute("SELECT crosswalk_release FROM ons_release").fetchone()[0], "2021-07")

    def refuses(self, message: str, **kwargs) -> None:
        scratch = self.build(**kwargs)
        with self.assertRaises(duckdb.Error) as caught:
            scratch.load()
        self.assertIn(message, str(caught.exception))

    def test_sidecar_url_without_date_fails(self):
        self.refuses("release", urls={VOLUME1: "https://www.ons.gov.uk/file?uri=/volume1.xlsx"})

    def test_index_url_without_date_fails(self):
        self.refuses("release", urls={INDEX: "https://www.ons.gov.uk/file?uri=/index.xlsx"})

    def test_relationship_url_without_month_fails(self):
        self.refuses("release", urls={RELATIONSHIP: "https://www.ons.gov.uk/file?uri=/relationship2021.xlsx"})

    def test_missing_sidecar_fails(self):
        scratch = self.build(skip_sidecar=VOLUME1)
        with self.assertRaises(duckdb.Error):
            scratch.load()

    def test_index_missing_sidecar_fails(self):
        scratch = self.build(skip_sidecar=INDEX)
        with self.assertRaises(duckdb.Error):
            scratch.load()

    def test_relationship_missing_sidecar_fails(self):
        scratch = self.build(skip_sidecar=RELATIONSHIP)
        with self.assertRaises(duckdb.Error):
            scratch.load()

    def test_index_version_unparseable_fails(self):
        scratch = self.build(file=INDEX, edit=lambda p: edit_sheet(
            p, lambda ws: ws.cell(2, 2).__setattr__("value", "Some index"), "INFO"))
        with self.assertRaises(duckdb.Error) as caught:
            scratch.load()
        self.assertIn("version", str(caught.exception))


class RefusalTest(unittest.TestCase):
    def refuses(self, edit, message: str, file: str = VOLUME1, sheet: str | None = None) -> None:
        scratch = Run(None)
        try:
            edit_sheet(scratch.build / "ons" / file, edit, sheet)
            with self.assertRaises(duckdb.Error) as caught:
                scratch.load()
            self.assertIn(message, str(caught.exception))
        finally:
            scratch.close()

    def refuses_deleting(self, column: int, code: int, message: str) -> None:
        scratch = Run(delete_code(column, code))
        try:
            with self.assertRaises(duckdb.Error) as caught:
                scratch.load()
            self.assertIn(message, str(caught.exception))
        finally:
            scratch.close()

    def test_moved_header_fails(self):
        self.refuses(lambda ws: ws.cell(1, 6).__setattr__("value", "Title"), "header")

    def test_index_moved_header_fails(self):
        self.refuses(lambda ws: ws.cell(1, 9).__setattr__("value", "TITLE"), "header", INDEX, "SOC2020 coding index")

    def test_relationship_moved_header_fails(self):
        self.refuses(lambda ws: ws.cell(1, 4).__setattr__("value", "pct"), "header", RELATIONSHIP, "soc2010-soc2020-map_Unit")

    def test_row_with_two_codes_fails(self):
        self.refuses(lambda ws: ws.cell(3, 4).__setattr__("value", 111), "one code")

    def test_row_with_no_code_fails(self):
        self.refuses(lambda ws: ws.cell(3, 3).__setattr__("value", "<blank>"), "one code")

    def test_a_repeated_code_fails(self):
        self.refuses(lambda ws: ws.cell(3, 3).__setattr__("value", 12), "repeated")

    def test_unit_without_minor_fails(self):
        self.refuses_deleting(4, 111, "no parent")

    def test_minor_without_sub_major_fails(self):
        self.refuses_deleting(3, 11, "no parent")

    def test_sub_major_without_major_fails(self):
        self.refuses_deleting(2, 9, "no parent")

    def test_an_unlisted_index_row_without_a_code_fails(self):
        # Only the cross-reference rows may be left out of the index.
        def change(ws):
            for row in range(2, ws.max_row + 1):
                if ws.cell(row, 1).value == "a1pc":
                    ws.cell(row, 12).value = None
        self.refuses(change, "cross-reference", INDEX, "SOC2020 coding index")

    def test_a_blank_base_on_a_total_sample_row_fails(self):
        # Row 4 is 1116's "Total sample"; a blank base must not take 1115's value.
        self.refuses(lambda ws: ws.cell(4, 5).__setattr__("value", None), "base count missing",
                     RELATIONSHIP, "soc2010-soc2020-map_Unit")

    def test_a_total_sample_row_without_a_group_fails(self):
        self.refuses(lambda ws: ws.cell(4, 1).__setattr__("value", None), "Total sample",
                     RELATIONSHIP, "soc2010-soc2020-map_Unit")

    def test_a_pairing_row_with_a_group_fails(self):
        self.refuses(lambda ws: ws.cell(3, 1).__setattr__("value", "1115: Chief executives and senior officials"),
                     "has a 2010 group", RELATIONSHIP, "soc2010-soc2020-map_Unit")

    def test_a_cleared_index_version_line_fails(self):
        self.refuses(lambda ws: ws.cell(2, 2).__setattr__("value", None), "version", INDEX, "INFO")

    def test_a_relationship_pairing_before_any_2010_group_fails(self):
        self.refuses(lambda ws: ws.delete_rows(2), "2010", RELATIONSHIP, "soc2010-soc2020-map_Unit")


if __name__ == "__main__":
    unittest.main()
