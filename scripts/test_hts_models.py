#!/usr/bin/env python3
"""Run the HTS Protocol's SQL models over a reduced USITC export and check the ladder.

The models are `datasets/hts/models/outline.sql`, `load.sql` and `package.sql`. Each
case copies the fixture in `scripts/testdata/hts/` into a scratch `build/`, beside a
copy of `datasets/hts/chapters.csv`, and runs the three files in order on one DuckDB
connection with that directory as its file search path, as the Protocol's `sql:` steps run them.

The fixture is cut from USITC's `exportList` for 2026HTSRev22, in file order: heading
0101 down to `0101.21.00.20`, `0104.10.00.00`, heading 0105 down to `0105.11.00.40`,
the chain above `0201.20.50.91` (an "Other" line under an "Other:" heading under an
"Other" line), `0409.00.00` and its statistical lines, heading 0802 with the unnumbered
"Kola nuts (Cola spp.):" and its two lines, two chapter 28 subchapter rows ("I. CHEMICAL
ELEMENTS", "II. INORGANIC ACIDS ...") each with the heading and line after it, heading
3407 and its two lines (its only subheading), heading 8424 with "Other appliances:"
over 8424.82.00 and the two 8424.89 lines, the
chains above `9823.01.01` and `9823.52.07`/`.08` (one with indent "03"), and two chapter
99 rows that sit side by side at indent 0. Five rows are made up, for shapes the real
export does not have where a small fixture can reach them: an unnumbered row "Of fish:"
at indent 0, directly under chapter 03, with heading 0301 moved to indent 1 beneath it;
and a provision 9824.01.01 with two 9824.02 lines indented beneath it, a layout chapter
98 uses (9823.03.02 and onward sit under 9823.03.01).

Needs duckdb==1.5.5 in the running Python:

    uv run --quiet --with duckdb==1.5.5 python3 scripts/test_hts_models.py
"""

from __future__ import annotations

import csv
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import duckdb

REPO = Path(__file__).resolve().parent.parent
HTS = REPO / "datasets" / "hts"
MODELS = [HTS / "models" / name for name in ("outline.sql", "load.sql", "package.sql")]
FIXTURE = Path(__file__).resolve().parent / "testdata" / "hts"
CHECK = Path(__file__).resolve().parent / "check_code_list.py"
LEVELS = "chapter,heading,subheading,tariff_line,statistical_line"
SEP = " › "


def chapter_titles() -> dict[str, str]:
    with open(HTS / "chapters.csv", encoding="utf-8", newline="") as f:
        return {row["chapter"]: row["chapter_title"] for row in csv.DictReader(f)}


class Run:
    """A scratch Protocol directory: `build/` holding the fixture, and chapters.csv."""

    def __init__(
        self,
        release_after: str | None = None,
        drop_chapter: str | None = None,
        release_after_body: str | None = None,
    ):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        build = self.root / "build"
        build.mkdir()
        for name in ("hts.json", "release_before.json", "release_after.json"):
            shutil.copy(FIXTURE / name, build / name)
        if release_after is not None:
            (build / "release_after.json").write_text(json.dumps({"name": release_after}), encoding="utf-8")
        if release_after_body is not None:
            (build / "release_after.json").write_text(release_after_body, encoding="utf-8")
        lines = (HTS / "chapters.csv").read_text(encoding="utf-8").splitlines(keepends=True)
        if drop_chapter is not None:
            lines = [ln for ln in lines if not ln.startswith(drop_chapter + ",")]
        (self.root / "chapters.csv").write_text("".join(lines), encoding="utf-8")
        self.con = duckdb.connect()
        # The models read `build/...` and `chapters.csv` relative to the Protocol
        # directory; resolve those against the scratch one.
        self.con.execute("SET file_search_path = ?", [str(self.root)])

    def execute(self) -> None:
        for model in MODELS:
            self.con.execute(model.read_text(encoding="utf-8"))

    def close(self) -> None:
        self.con.close()
        self.tmp.cleanup()


class LadderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = Run()
        cls.scratch.execute()
        cls.con = cls.scratch.con
        cls.titles = chapter_titles()

    @classmethod
    def tearDownClass(cls):
        cls.scratch.close()

    def row(self, code: str) -> dict:
        cur = self.con.execute("SELECT * FROM hts_out WHERE code = ?", [code])
        names = [d[0] for d in cur.description]
        found = cur.fetchall()
        self.assertEqual(len(found), 1, f"{code}: expected one row, found {len(found)}")
        return dict(zip(names, found[0]))

    def test_single_line_heading_is_made(self):
        r = self.row("0409")
        self.assertEqual(r["level"], "heading")
        self.assertEqual(r["text_source"], "usitc_derived")
        self.assertEqual(r["description"], "Natural honey")
        self.assertEqual(r["parent_code"], "04")
        self.assertEqual(r["code_display"], "0409")

    def test_made_subheading_carries_its_unnumbered_heading(self):
        r = self.row("010511")
        self.assertEqual(r["description"], "Weighing not more than 185 g: Chickens")
        self.assertEqual(r["code_display"], "0105.11")
        self.assertEqual(r["level"], "subheading")

    def test_statistical_line_reads_in_context(self):
        r = self.row("0101210010")
        self.assertEqual(r["parent_code"], "01012100")
        self.assertEqual(r["code_display"], "0101.21.00.10")
        heading = self.row("0101")["description"]
        self.assertTrue(
            r["description_full"].startswith(self.titles["01"] + SEP + heading + SEP),
            r["description_full"],
        )
        self.assertEqual(r["description_full"].count("Horses:"), 1, r["description_full"])
        self.assertEqual(r["text_source"], "usitc")

    def test_each_outline_row_appears_once_in_description_full(self):
        # A printed row's path is its chapter title, its subchapter if it has one, each
        # row on its indent chain, and itself: one element per source, none dropped.
        rows = self.con.execute(
            # Every chapter 28 row in the fixture follows a subchapter row.
            """SELECT h.code, h.description_full, len(o.context), h.code LIKE '28%'
               FROM hts_out h JOIN hts_outline o ON o.code = h.code
               WHERE h.text_source = 'usitc'"""
        ).fetchall()
        self.assertGreater(len(rows), 30)
        for code, full, chain, subchapter in rows:
            self.assertEqual(len(full.split(SEP)), 1 + int(subchapter) + chain + 1, f"{code}: {full}")

    def test_description_full_ends_with_the_defining_rows_text(self):
        # Two different rows with the same text are two levels of the outline, and both
        # stay: 0409.00.00.35 sits under "Other:" under "Other:".
        r = self.row("0409000035")
        self.assertEqual(
            r["description_full"],
            SEP.join([self.titles["04"], "Natural honey", "Other:", "Other:", "White or lighter"]),
        )
        r = self.row("0201205091")
        self.assertTrue(r["description_full"].endswith(SEP.join(["", "Other", "Other:", "Other"])), r["description_full"])
        for code, desc, full in self.con.execute(
            "SELECT code, description, description_full FROM hts_out WHERE text_source = 'usitc'"
        ).fetchall():
            self.assertTrue(full.endswith(SEP + desc), f"{code}: {full}")

    def test_unprinted_tariff_line_is_made(self):
        r = self.row("0104100000")
        self.assertEqual(r["parent_code"], "01041000")
        made = self.row("01041000")
        self.assertEqual(made["level"], "tariff_line")
        self.assertEqual(made["text_source"], "usitc_derived")
        self.assertEqual(made["parent_code"], "010410")
        self.assertEqual(made["code_display"], "0104.10.00")

    def test_unnumbered_row_under_a_chapter(self):
        r = self.row("0301")
        self.assertEqual(r["parent_code"], "03")
        self.assertEqual(r["description_full"], self.titles["03"] + SEP + "Of fish:" + SEP + "Live fish:")

    def test_statistical_line_takes_its_tariff_lines_rate(self):
        line = self.row("04090000")
        self.assertEqual(line["general_rate"], "1.9¢/kg")
        self.assertEqual(line["rate_from_code"], "04090000")
        for code in ("0409000005", "0409000010", "0409000065"):
            r = self.row(code)
            self.assertEqual(r["general_rate"], line["general_rate"], code)
            self.assertEqual(r["special_rate"], line["special_rate"], code)
            self.assertEqual(r["column2_rate"], line["column2_rate"], code)
            self.assertEqual(r["rate_from_code"], "04090000", code)
        own = self.row("0104100000")
        self.assertEqual(own["general_rate"], "Free")
        self.assertEqual(own["rate_from_code"], "0104100000")

    def test_only_printed_rows_carry_rates(self):
        carrying = self.con.execute(
            "SELECT code FROM hts_out WHERE text_source <> 'usitc' AND (general_rate IS NOT NULL "
            "OR special_rate IS NOT NULL OR column2_rate IS NOT NULL OR rate_from_code IS NOT NULL)"
        ).fetchall()
        self.assertEqual(carrying, [])

    def test_a_line_with_no_rate_above_it_has_none(self):
        r = self.row("0101")
        self.assertIsNone(r["general_rate"])
        self.assertIsNone(r["rate_from_code"])

    def test_chapters_come_from_the_table_of_contents(self):
        chapters = self.con.execute(
            "SELECT code, text_source, depth, parent_code, description, description_full "
            "FROM hts_out WHERE level = 'chapter' ORDER BY code"
        ).fetchall()
        self.assertEqual([c[0] for c in chapters], ["01", "02", "03", "04", "08", "28", "34", "84", "98", "99"])
        for code, source, depth, parent, desc, full in chapters:
            self.assertEqual(source, "usitc_toc", code)
            self.assertEqual(depth, 0, code)
            self.assertIsNone(parent, code)
            self.assertEqual(desc, self.titles[code], code)
            self.assertEqual(full, self.titles[code], code)

    def test_every_row_has_a_section(self):
        missing = self.con.execute(
            "SELECT count(*) FROM hts_out WHERE section IS NULL OR section_title IS NULL"
        ).fetchone()[0]
        self.assertEqual(missing, 0)
        self.assertEqual(self.row("0409000010")["section"], "I")
        self.assertEqual(self.row("99010050")["section"], "XXII")

    def test_indent_is_an_integer(self):
        self.assertEqual(self.row("98235207")["indent"], 3)

    def test_release_and_corpus(self):
        releases = self.con.execute("SELECT DISTINCT release FROM hts_out").fetchall()
        self.assertEqual(releases, [("2026HTSRev22",)])
        r = self.row("0409000010")
        self.assertEqual(r["corpus"], r["description_full"].lower())

    def test_outline_interface(self):
        cols = self.con.execute("DESCRIBE hts_outline").fetchall()
        self.assertEqual(
            [(c[0], c[1]) for c in cols],
            [
                ("seq", "BIGINT"),
                ("code", "VARCHAR"),
                ("code_display", "VARCHAR"),
                ("indent", "INTEGER"),
                ("description", "VARCHAR"),
                ("context", "VARCHAR[]"),
            ],
        )
        cols = self.con.execute("DESCRIBE hts_levels").fetchall()
        self.assertEqual(
            [(c[0], c[1]) for c in cols],
            [
                ("prefix", "VARCHAR"),
                ("def_seq", "BIGINT"),
                ("def_code", "VARCHAR"),
                ("text", "VARCHAR"),
                ("text_source", "VARCHAR"),
            ],
        )
        level = self.con.execute(
            "SELECT def_code, text FROM hts_levels WHERE prefix = '010511'"
        ).fetchone()
        self.assertEqual(level, ("01051100", "Weighing not more than 185 g: Chickens"))

    def test_level_text_starts_below_its_parent_levels_row(self):
        # Subheading 010121 is made from 0101.21.00 and carries "Horses:". Tariff line
        # 01012100 is defined by the same row, so nothing lies between it and its parent
        # level, and "Horses:" is not carried down a second time.
        texts = dict(self.con.execute(
            "SELECT prefix, text FROM hts_levels WHERE prefix IN ('010121', '01012100', '0101210010')"
        ).fetchall())
        self.assertEqual(texts["010121"], "Horses: Purebred breeding animals")
        self.assertEqual(texts["01012100"], "Purebred breeding animals")
        self.assertEqual(texts["0101210010"], "Males")

    def test_a_level_with_no_node_of_its_own_is_labelled(self):
        # 9901.00.50 and 9901.00.52 sit side by side at indent 0: no outline row is
        # above both, so neither line's text may stand for heading 9901.
        for code, label in (("9901", "Heading 9901"), ("990100", "Subheading 9901.00"), ("9823", "Heading 9823")):
            r = self.row(code)
            self.assertEqual(r["text_source"], "meridian_label", code)
            self.assertEqual(r["description"], label, code)
            self.assertEqual(r["description_full"], self.titles[code[:2]] + SEP + label, code)
        levels = dict(self.con.execute(
            "SELECT prefix, text_source FROM hts_levels WHERE prefix IN ('9901', '990100', '9823', '010511', '0101')"
        ).fetchall())
        self.assertEqual(
            levels,
            {"9901": "meridian_label", "990100": "meridian_label", "9823": "meridian_label",
             "010511": "usitc_derived", "0101": "usitc"},
        )

    def test_a_level_whose_rows_share_one_outline_row_takes_its_text(self):
        # Subheading 9823.52's two lines sit under one unnumbered heading, which no other
        # prefix's rows sit under; heading 9823 has none (see the label test).
        r = self.row("982352")
        self.assertEqual(r["text_source"], "usitc_derived")
        self.assertTrue(r["description"].endswith("Non-originating cotton or man-made fiber spun yard goods as provided for in note 11(a)((iii):"), r["description"])
        self.assertTrue(r["description"].startswith("Goods entered under the provisions"), r["description"])

    def test_an_unnumbered_node(self):
        # 0802.70.10.00 and 0802.70.20.00 sit under "Kola nuts (Cola spp.):" alone.
        r = self.row("080270")
        self.assertEqual(r["text_source"], "usitc_derived")
        self.assertEqual(r["description"], "Kola nuts (Cola spp.):")
        heading = self.row("0802")["description"]
        self.assertEqual(r["description_full"], SEP.join([self.titles["08"], heading, "Kola nuts (Cola spp.):"]))
        self.assertEqual(self.row("08027010")["description"], "In shell")

    def test_a_headings_only_subheading_takes_the_headings_text(self):
        # 3407.00.20.00 and 3407.00.40.00 sit under heading 3407 and nothing else: the
        # heading row is their node, and no other subheading's rows sit under it.
        heading = self.row("3407")["description"]
        r = self.row("340700")
        self.assertEqual(r["text_source"], "usitc_derived")
        self.assertEqual(r["description"], heading)
        level = self.con.execute("SELECT text, text_source FROM hts_levels WHERE prefix = '340700'").fetchone()
        self.assertEqual(level, (heading, "usitc_derived"))

    def test_rows_under_another_prefixs_line_are_labelled(self):
        # 9824.02.01 and .02 sit under the line 9824.01.01, which is no prefix of
        # 982402: that line's text must not stand for subheading 9824.02.
        r = self.row("982402")
        self.assertEqual(r["text_source"], "meridian_label")
        self.assertEqual(r["description"], "Subheading 9824.02")
        self.assertEqual(self.row("9824")["description"], "Made-up provision A")

    def test_below_a_label_the_text_reads_from_the_chapter_top(self):
        # 8424.89's two lines sit under "Other appliances:", which 8424.82.00 sits under
        # too, so 842489 is a label; its lines' made tariff lines read from the chapter
        # top and keep "Other appliances:".
        r = self.row("842489")
        self.assertEqual(r["text_source"], "meridian_label")
        self.assertEqual(r["description"], "Subheading 8424.89")
        r = self.row("84248990")
        self.assertEqual(r["text_source"], "usitc_derived")
        self.assertEqual(r["description"], "Other appliances: Other")

    def test_subchapter_rows_follow_the_chapter_title(self):
        title = self.titles["28"]
        first, second = "I. CHEMICAL ELEMENTS", "II. INORGANIC ACIDS AND INORGANIC OXYGEN COMPOUNDS OF NONMETALS"
        for code, sub in (("2801", first), ("280110", first), ("2801100000", first), ("2806", second), ("2806100000", second)):
            full = self.row(code)["description_full"]
            self.assertTrue(full.startswith(title + SEP + sub + SEP), f"{code}: {full}")
            self.assertEqual(full.count(" › I"), 1, f"{code}: {full}")
        self.assertEqual(self.row("28")["description_full"], title)
        self.assertEqual(self.row("2806")["description_full"], SEP.join([title, second, self.row("2806")["description"]]))

    def test_hts_columns_follow_the_core(self):
        cols = [c[0] for c in self.con.execute("DESCRIBE hts_out").fetchall()]
        self.assertEqual(
            cols,
            [
                "code", "code_display", "level", "depth", "parent_code", "description",
                "description_full", "release", "text_source", "corpus",
                "indent", "section", "section_title", "units", "general_rate", "special_rate",
                "column2_rate", "rate_from_code", "footnotes", "quota_quantity", "additional_duties",
            ],
        )

    def test_check_code_list_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "hts.parquet"
            self.con.execute(f"COPY (SELECT * FROM hts_out ORDER BY code) TO '{out}' (FORMAT parquet)")
            done = subprocess.run(
                [sys.executable, str(CHECK), "--parquet", str(out), "--levels", LEVELS],
                capture_output=True,
                text=True,
            )
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


class RefusalTest(unittest.TestCase):
    def test_release_changed_during_the_run(self):
        run = Run(release_after="2026HTSRev23")
        try:
            with self.assertRaises(duckdb.Error) as caught:
                run.execute()
            self.assertIn("2026HTSRev22 then 2026HTSRev23", str(caught.exception))
        finally:
            run.close()

    def test_release_that_names_no_revision(self):
        # error(NULL) raises nothing: a currentRelease with no name must still fail.
        run = Run(release_after_body="{}")
        try:
            with self.assertRaises(duckdb.Error) as caught:
                run.execute()
            self.assertIn("USITC currentRelease names no revision: 2026HTSRev22 / null", str(caught.exception))
        finally:
            run.close()

    def test_chapter_without_a_title(self):
        run = Run(drop_chapter="98")
        try:
            with self.assertRaises(duckdb.Error) as caught:
                run.execute()
            self.assertIn("chapter 98 has no title in chapters.csv", str(caught.exception))
        finally:
            run.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
