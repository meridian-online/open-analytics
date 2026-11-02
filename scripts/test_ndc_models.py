#!/usr/bin/env python3
"""Run the NDC Protocol's SQL models over a reduced openFDA file and check the result.

The models are `datasets/ndc/models/load.sql` and `package.sql`. Each case writes a copy
of the fixture in `scripts/testdata/ndc/` into a scratch `build/ndc/`, runs `load` and
`package` on one DuckDB connection with the scratch directory as its file search path, as
the Protocol's `sql:` steps run them, exports the terminal table to
`build/stage/ndc.parquet` as `export_ndc` does, and runs `check_code_list.py` over that
file.

The fixture is cut from openFDA's NDC download of 2026-10-30
(drug-ndc-0001-of-0001.json), with each record's `openfda` block removed, and `meta`'s
record count set to the eight records kept:

- 0002-0152, Zepbound: a four-digit labeler code, three packages, one of them a sample.
- 63323-729, Micafungin: one product NDC in two listings, each with its own package.
- 85533-203: one product NDC in two listings that spell the labeler's name two ways
  ("Mint Skin Incorporated", "Mint Skin Incroporated"), with one package each.
- 72043-2500: a package, 72043-2500-1, listed twice in one listing with two start dates.
- 71110-1505: a 5-4-1 package NDC.
- 0006-0001: an unfinished listing (a bulk drug), whose package start date openFDA
  prints as 15-AUG-14, so the load fails if it reads unfinished listings at all.

openFDA's data is released under CC0, so the records are committed as openFDA gives them.

Needs duckdb==1.5.5 in the running Python:

    uv run --quiet --with duckdb==1.5.5 python3 scripts/test_ndc_models.py
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import duckdb

REPO = Path(__file__).resolve().parent.parent
NDC = REPO / "datasets" / "ndc"
MODELS = [NDC / "models" / name for name in ("load.sql", "package.sql")]
FIXTURE = Path(__file__).resolve().parent / "testdata" / "ndc" / "drug-ndc-0001-of-0001.json"
CHECK = Path(__file__).resolve().parent / "check_code_list.py"
LEVELS = "labeler,product,package"


def fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class Run:
    """A scratch Protocol directory whose `build/ndc/` holds one openFDA file."""

    def __init__(self, doc: dict | None = None, extra: bool = False):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.build = self.root / "build"
        (self.build / "ndc").mkdir(parents=True)
        (self.build / "stage").mkdir()
        body = json.dumps(fixture() if doc is None else doc)
        (self.build / "ndc" / "drug-ndc-0001-of-0001.json").write_text(body, encoding="utf-8")
        if extra:
            (self.build / "ndc" / "drug-ndc-0001-of-0002.json").write_text(body, encoding="utf-8")
        self.con = duckdb.connect()
        self.con.execute("SET file_search_path = ?", [str(self.root)])

    def load(self) -> None:
        for model in MODELS:
            self.con.execute(model.read_text(encoding="utf-8"))

    def export(self) -> Path:
        out = self.build / "stage" / "ndc.parquet"
        self.con.execute(f"COPY (FROM ndc_out ORDER BY code) TO '{out}' (FORMAT parquet)")
        return out

    def close(self) -> None:
        self.con.close()
        self.tmp.cleanup()


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
        cur = self.con.execute("SELECT * FROM ndc_out WHERE code = ?", [code])
        names = [d[0] for d in cur.description]
        found = cur.fetchall()
        self.assertEqual(len(found), 1, f"{code}: expected one row, found {len(found)}")
        return dict(zip(names, found[0]))

    def codes(self, level: str) -> list[str]:
        return [r[0] for r in self.con.execute(
            "SELECT code_display FROM ndc_out WHERE level = ? ORDER BY code", [level]).fetchall()]

    def test_finished_drugs_only_one_row_per_code(self):
        self.assertEqual(self.codes("labeler"), ["0002", "63323", "71110", "72043", "85533"])
        self.assertEqual(self.codes("product"),
                         ["0002-0152", "63323-729", "71110-1505", "72043-2500", "85533-203"])
        self.assertEqual(self.codes("package"), [
            "0002-0152-01", "0002-0152-04", "0002-0152-61", "63323-729-10", "63323-729-12",
            "71110-1505-1", "72043-2500-1", "72043-2500-2", "72043-2500-3", "72043-2500-5",
            "85533-203-15", "85533-203-50"])

    def test_codes_are_eleven_digit_padded_with_parents_by_prefix(self):
        lab, prod, pkg = self.row("00002"), self.row("000020152"), self.row("00002015201")
        self.assertEqual((lab["level"], lab["depth"], lab["parent_code"]), ("labeler", 0, None))
        self.assertEqual((prod["level"], prod["depth"], prod["parent_code"]), ("product", 1, "00002"))
        self.assertEqual((pkg["level"], pkg["depth"], pkg["parent_code"]), ("package", 2, "000020152"))
        self.assertEqual(pkg["code_display"], "0002-0152-01")
        # 5-3-2 pads its product segment; 5-4-1 pads its package segment.
        self.assertEqual(self.row("63323072910")["code_display"], "63323-729-10")
        self.assertEqual(self.row("71110150501")["code_display"], "71110-1505-1")

    def test_core_columns_and_text(self):
        lab, prod, pkg = self.row("00002"), self.row("000020152"), self.row("00002015201")
        self.assertEqual(lab["description"], "Eli Lilly and Company")
        self.assertEqual(prod["description"], "Zepbound (tirzepatide), INJECTION, SOLUTION")
        self.assertEqual(pkg["description"],
                         "1 VIAL, SINGLE-DOSE in 1 CARTON (0002-0152-01)  / .5 mL in 1 VIAL, SINGLE-DOSE")
        self.assertEqual(pkg["description_full"], " › ".join(
            [lab["description"], prod["description"], pkg["description"]]))
        self.assertEqual(prod["description_full"], lab["description"] + " › " + prod["description"])
        for r in (lab, prod, pkg):
            self.assertEqual(r["release"], "2026-10-30")
            self.assertEqual(r["text_source"], "fda")
            self.assertEqual(r["corpus"], r["description_full"].lower())

    def test_generic_name_in_brackets_only_where_it_differs(self):
        self.assertEqual(self.row("633230729")["description"],
                         "Micafungin (Micafungin Sodium), INJECTION, POWDER, LYOPHILIZED, FOR SOLUTION")
        doc = fixture()
        for r in doc["results"]:
            if r["product_ndc"] == "63323-729":
                r["generic_name"] = "MICAFUNGIN"
        scratch = Run(doc)
        try:
            scratch.load()
            got = scratch.con.execute("SELECT description FROM ndc_out WHERE code = '633230729'").fetchone()[0]
            self.assertEqual(got, "Micafungin, INJECTION, POWDER, LYOPHILIZED, FOR SOLUTION")
        finally:
            scratch.close()

    def test_package_rows_carry_their_listings_product_fields(self):
        pkg = self.row("00002015261")
        self.assertEqual(pkg["brand_name"], "Zepbound")
        self.assertEqual(pkg["generic_name"], "tirzepatide")
        self.assertEqual(pkg["routes"], ["SUBCUTANEOUS"])
        self.assertEqual(pkg["active_ingredients"], [{"name": "TIRZEPATIDE", "strength": "2.5 mg/.5mL"}])
        self.assertEqual(pkg["application_number"], "NDA217806")
        self.assertIs(pkg["sample"], True)
        self.assertEqual(str(pkg["package_marketing_start_date"]), "2025-05-01")
        self.assertEqual(str(pkg["product_marketing_start_date"]), "2024-03-28")
        self.assertIsNone(pkg["listing_count"])

    def test_labeler_rows_carry_only_their_name(self):
        lab = self.row("00002")
        self.assertEqual(lab["labeler_name"], "Eli Lilly and Company")
        for col in ("product_type", "brand_name", "routes", "product_id", "package_description", "sample"):
            self.assertIsNone(lab[col], col)

    def test_a_product_in_two_listings_is_one_row_with_both_packages(self):
        prod = self.row("633230729")
        self.assertEqual(prod["listing_count"], 2)
        # Same certification and start date: the lower product_id is taken.
        self.assertEqual(prod["product_id"], "63323-729_e8fcf6a2-1a58-4a13-9290-1fc0d4fecfd1")
        self.assertEqual(self.row("63323072910")["product_id"],
                         "63323-729_e9aa7e59-831f-4994-802b-b62221fdfca3")

    def test_labeler_named_by_its_most_common_name_and_rows_keep_their_own(self):
        self.assertEqual(self.row("85533")["description"], "Mint Skin Incorporated")
        self.assertEqual(self.row("85533020315")["labeler_name"], "Mint Skin Incroporated")
        self.assertTrue(self.row("85533020315")["description_full"].startswith("Mint Skin Incorporated › "))

    def test_a_package_listed_twice_keeps_its_earlier_start(self):
        self.assertEqual(str(self.row("72043250001")["package_marketing_start_date"]), "2018-01-10")

    def test_code_list_contract(self):
        proc = subprocess.run([sys.executable, str(CHECK), "--parquet", str(self.parquet),
                               "--levels", LEVELS], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


class RefusalTest(unittest.TestCase):
    def refuses(self, doc: dict | None, message: str, extra: bool = False) -> None:
        scratch = Run(doc, extra=extra)
        try:
            with self.assertRaises(duckdb.Error) as caught:
                scratch.load()
            self.assertIn(message, str(caught.exception))
        finally:
            scratch.close()

    def test_record_count_must_match_meta(self):
        doc = fixture()
        doc["results"].pop()
        self.refuses(doc, "openFDA states 8 records but the file holds 7")

    def test_one_file_only(self):
        self.refuses(None, "expected one openFDA NDC file", extra=True)

    def test_last_updated_must_be_a_date(self):
        doc = fixture()
        doc["meta"]["last_updated"] = "soon"
        self.refuses(doc, "openFDA last_updated is not a date: soon")

    def test_product_ndc_layout(self):
        doc = fixture()
        doc["results"][0]["product_ndc"] = "855-203"
        self.refuses(doc, "openFDA product NDC is not 4-4, 5-3 or 5-4: 855-203")

    def test_package_must_sit_under_its_product(self):
        doc = fixture()
        doc["results"][0]["packaging"][0]["package_ndc"] = "85533-204-50"
        self.refuses(doc, "is not under its product 85533-203")

    def test_two_labeler_codes_that_pad_alike(self):
        doc = fixture()
        twin = copy.deepcopy(next(r for r in doc["results"] if r["product_ndc"] == "0002-0152"))
        twin["product_ndc"] = "00002-153"
        twin["product_id"] = "00002-153_twin"
        twin["packaging"] = [dict(twin["packaging"][0], package_ndc="00002-153-01")]
        doc["results"].append(twin)
        doc["meta"]["results"]["total"] += 1
        self.refuses(doc, "two labeler codes pad to 00002")

    def test_dates_must_parse(self):
        doc = fixture()
        doc["results"][0]["marketing_start_date"] = "15-AUG-14"
        self.refuses(doc, "openFDA marketing start date is not a date")

    def test_unfinished_listings_are_not_read(self):
        # The fixture's unfinished listing has an unparseable package date; the build
        # passes only because it is filtered out. Mark it finished to prove it.
        doc = fixture()
        next(r for r in doc["results"] if not r["finished"])["finished"] = True
        self.refuses(doc, "openFDA package marketing start date is not a date")


if __name__ == "__main__":
    unittest.main()
