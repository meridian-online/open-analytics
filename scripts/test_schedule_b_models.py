#!/usr/bin/env python3
"""Run the Schedule B Protocol's SQL models over reduced Census and USITC files.

The models are `datasets/hts/models/outline.sql` (shared, run unchanged), then
`datasets/schedule_b/models/load.sql` and `package.sql`. Each case lays out a scratch
Protocol directory, runs the three files in order on one DuckDB connection with that
directory as its file search path, as the Protocol's `sql:` steps run them, and sets the
year the way arc passes a `params:` value to SQL: as the environment variable
`ARC_PARAM_YEAR`, which load.sql reads with the DuckDB CLI's `getenv` (stood up here as a
macro, since only the CLI has it).

The scratch `build/` holds:

- `sbsdf/econcord.txt`: `scripts/testdata/schedule_b/econcord.txt`, eight lines cut
  from Census's 2026 file in its CRLF fixed width (0101210000, 0101290000, 0409000025,
  8424890000, 8800000000, 9801100000, 9802100000, 9802200000), plus the padding below.
- `schedule_b_index.html`: `scripts/testdata/schedule_b/index.html`, cells cut from
  Census's 2026 index: sections 1, XIII, XIV, XVI, XVII and XXII and chapters 1, 4, 70,
  71, 84, 88 and 98, with the "All Chapters 1 Through 98" cell above them.
- `hts.json`, `release_before.json`, `release_after.json`: the HTS fixture in
  `scripts/testdata/hts/`, plus the padding below.
- `econcord_date.json`: the release date of econcord.txt, `{"date": "2026-07-28"}`.

Padding. The edition guard needs USITC to name 99% of the headings, and of the
subheadings, outside chapter 98; the fixture has one heading (8800) and three subheadings
(010129, 842489, 880000) USITC does not name, so a few lines alone would trip it. Each run
therefore adds 198 made-up headings in chapters 70 and 71 (7001 to 7199), each printed by
USITC as a heading row over two subheading rows (`7001`, `7001.10`, `7001.20`) and by
Census as two lines (7001100000, 7001200000). 7199 differs (see ODD): USITC prints only
its two subheadings, side by side, and Census only 7199100000. That leaves 200 of 202
headings and 397 of 400 subheadings named, just over 99%. The refusal cases drop the
fixture's and padding's four-digit or six-digit USITC rows, or one or two padding rows to
put a level just under 99%. One more made-up USITC row, `9801.00.10`, gives heading 9801
USITC text, which Schedule B's chapter 98 must not take; two more made-up Census lines,
9803100010 and 9803100020, give a subheading with several lines under it and no USITC
text.

Needs duckdb==1.5.5 in the running Python:

    uv run --quiet --with duckdb==1.5.5 python3 scripts/test_schedule_b_models.py
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import duckdb

REPO = Path(__file__).resolve().parent.parent
MODELS = [
    REPO / "datasets" / "hts" / "models" / "outline.sql",
    REPO / "datasets" / "schedule_b" / "models" / "load.sql",
    REPO / "datasets" / "schedule_b" / "models" / "package.sql",
]
TESTDATA = Path(__file__).resolve().parent / "testdata"
FIXTURE = TESTDATA / "schedule_b"
HTS_FIXTURE = TESTDATA / "hts"
CHECK = Path(__file__).resolve().parent / "check_code_list.py"
LEVELS = "chapter,heading,subheading,commodity"
SEP = " › "
DATE = "2026-07-28"


def census_line(code: str, short: str, long: str, agricultural: str = "1") -> bytes:
    """One econcord.txt line in Census's layout (exp-stru.txt), 278 characters."""
    line = [" "] * 278

    def put(start: int, value: str) -> None:
        line[start - 1 : start - 1 + len(value)] = value

    put(1, code)
    put(15, short)
    put(70, long)
    put(225, "NO")
    put(241, "66XXX")
    put(251, "40000")
    put(261, agricultural)
    put(266, "327212")
    put(277, "00")
    return "".join(line).encode("ascii") + b"\r\n"


# The padding heading USITC gives no text and Census one line (7199100000): the heading
# takes that line's text, its subheading USITC's, and the line its own, so the line's
# text reaches 7199100000's description_full twice, two levels apart, unless deduped.
ODD = "7199"


def padding_codes() -> list[str]:
    return [f"{chapter}{n:02d}" for chapter in ("70", "71") for n in range(1, 100)]


def padding_hts_rows(template: dict) -> list[dict]:
    rows = []
    for heading in padding_codes():
        shape = (
            (heading, "0", f"Made-up heading {heading}:"),
            (f"{heading}.10", "1", f"Made-up subheading {heading}.10"),
            (f"{heading}.20", "1", f"Made-up subheading {heading}.20"),
        )
        if heading == ODD:
            # No heading row: the two subheadings sit side by side, so USITC gives the
            # heading no text of its own (hts_levels labels it).
            shape = tuple((htsno, "0", text) for htsno, _, text in shape[1:])
        for htsno, indent, text in shape:
            row = {k: ([] if isinstance(v, list) else "") for k, v in template.items()}
            row.update(htsno=htsno, indent=indent, description=text)
            rows.append(row)
    # An import provision in heading 9801, so chapter 98's rule has USITC text to refuse.
    row = {k: ([] if isinstance(v, list) else "") for k, v in template.items()}
    row.update(htsno="9801.00.10", indent="0", description="Made-up import provision 9801.00.10")
    return rows + [row]


class Run:
    """A scratch Protocol directory, and the year arc would pass to its SQL steps."""

    def __init__(
        self,
        year: str = "2026",
        drop_hts_digits: int | None = None,
        drop_htsno: tuple[str, ...] = (),
        index_title: str | None = None,
        release_after_body: str | None = None,
    ):
        self.year = year
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        build = self.root / "build"
        (build / "sbsdf").mkdir(parents=True)
        census = (FIXTURE / "econcord.txt").read_bytes()
        for heading in padding_codes():
            for sub in ("10",) if heading == ODD else ("10", "20"):
                code = f"{heading}{sub}0000"
                census += census_line(code, f"MADE-UP LINE {code}", f"MADE-UP LINE {code}, LONG")
        for code in ("9803100010", "9803100020"):
            census += census_line(code, f"MADE-UP LINE {code}", f"MADE-UP LINE {code}, LONG")
        (build / "sbsdf" / "econcord.txt").write_bytes(census)
        index = (FIXTURE / "index.html").read_text(encoding="utf-8")
        if index_title is not None:
            # The page as Census would serve another year's folder: only its title changes.
            old = "<title>Schedule B 2026 - Browse Chapters</title>"
            assert old in index
            index = index.replace(old, f"<title>{index_title}</title>")
        (build / "schedule_b_index.html").write_text(index, encoding="utf-8")
        for name in ("release_before.json", "release_after.json"):
            shutil.copy(HTS_FIXTURE / name, build / name)
        if release_after_body is not None:
            (build / "release_after.json").write_text(release_after_body, encoding="utf-8")
        rows = json.loads((HTS_FIXTURE / "hts.json").read_text(encoding="utf-8"))
        rows += padding_hts_rows(rows[0])
        if drop_hts_digits is not None:
            rows = [r for r in rows if len(r["htsno"].replace(".", "")) != drop_hts_digits]
        rows = [r for r in rows if r["htsno"] not in drop_htsno]
        (build / "hts.json").write_text(json.dumps(rows), encoding="utf-8")
        (build / "econcord_date.json").write_text(json.dumps({"date": DATE}), encoding="utf-8")
        self.con = duckdb.connect()
        self.con.execute("SET file_search_path = ?", [str(self.root)])

    def execute(self) -> None:
        # arc passes a `params:` value to a SQL step as the environment variable
        # ARC_PARAM_<NAME>, which the DuckDB CLI it runs reads with `getenv`. `getenv`
        # is a builtin of the CLI and of no other client, so this connection stands it
        # up as a macro answering what arc would set; no model byte changes.
        assert re.fullmatch(r"\d{4}", self.year), self.year
        self.con.execute(
            f"CREATE OR REPLACE MACRO getenv(name) AS CASE name WHEN 'ARC_PARAM_YEAR' THEN '{self.year}' END"
        )
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

    @classmethod
    def tearDownClass(cls):
        cls.scratch.close()

    def row(self, code: str) -> dict:
        cur = self.con.execute("SELECT * FROM schedule_b_out WHERE code = ?", [code])
        names = [d[0] for d in cur.description]
        found = cur.fetchall()
        self.assertEqual(len(found), 1, f"{code}: expected one row, found {len(found)}")
        return dict(zip(names, found[0]))

    def hts_text(self, prefix: str) -> str:
        return self.con.execute("SELECT text FROM hts_levels WHERE prefix = ?", [prefix]).fetchone()[0]

    def test_heading_takes_usitcs_text(self):
        r = self.row("0101")
        self.assertEqual(r["text_source"], "usitc")
        self.assertEqual(r["description"], "Live horses, asses, mules and hinnies:")
        self.assertEqual(r["level"], "heading")
        self.assertEqual(r["parent_code"], "01")
        self.assertEqual(r["code_display"], "0101")
        sub = self.row("010121")
        self.assertEqual(sub["parent_code"], "0101")
        self.assertEqual(sub["text_source"], "usitc")
        self.assertEqual(sub["description"], self.hts_text("010121"))
        self.assertEqual(sub["code_display"], "0101.21")

    def test_chapter_98_takes_census_text(self):
        # USITC names 9801 (the padding's import provision); chapter 98 reads its own
        # lines whatever USITC holds.
        self.assertEqual(self.hts_text("9801"), "Made-up import provision 9801.00.10")
        r = self.row("9801")
        self.assertEqual(r["text_source"], "census_derived")
        line = self.row("9801100000")
        self.assertEqual(r["description"], line["description"])
        self.assertEqual(self.row("980110")["text_source"], "census_derived")
        self.assertEqual(self.row("980220")["description"], self.row("9802200000")["description"])

    def test_a_census_level_with_several_lines_is_labelled(self):
        # 9802 has two lines under it and 980310 two: no one line's text stands for them.
        for code, label in (("9802", "Heading 9802"), ("9803", "Heading 9803"),
                            ("980310", "Subheading 9803.10")):
            r = self.row(code)
            self.assertEqual(r["text_source"], "meridian_label", code)
            self.assertEqual(r["description"], label, code)
        self.assertEqual(self.row("980210")["text_source"], "census_derived")
        r = self.row("9803100010")
        self.assertEqual(
            r["description_full"],
            SEP.join(["Special Classification Provisions", "Heading 9803", "Subheading 9803.10", r["description"]]),
        )

    def test_export_only_prefixes_take_their_first_lines_text(self):
        line = self.row("8800000000")
        for code in ("8800", "880000", "010129"):
            r = self.row(code)
            self.assertEqual(r["text_source"], "census_derived", code)
        self.assertEqual(self.row("8800")["description"], line["description"])
        self.assertEqual(self.row("010129")["description"], self.row("0101290000")["description"])

    def test_a_labelled_prefix_is_not_named(self):
        # hts_levels labels 842489 ("Subheading 8424.89"): its lines sit under
        # "Other appliances:" with 8424.82.00's. Schedule B takes its first line's text.
        label = self.con.execute(
            "SELECT text_source FROM hts_levels WHERE prefix = '842489'").fetchone()[0]
        self.assertEqual(label, "meridian_label")
        r = self.row("842489")
        self.assertEqual(r["text_source"], "census_derived")
        self.assertEqual(r["description"], self.row("8424890000")["description"])
        self.assertEqual(self.row("8424")["text_source"], "usitc")
        labels = self.con.execute(
            "SELECT count(*) FROM schedule_b_out s JOIN hts_levels h ON h.prefix = s.code "
            "WHERE h.text_source = 'meridian_label' AND s.text_source = 'usitc'"
        ).fetchone()[0]
        self.assertEqual(labels, 0)

    def test_chapter_and_section_from_the_index(self):
        r = self.row("01")
        self.assertEqual(r["description"], "Live Animals")
        self.assertEqual(r["text_source"], "census")
        self.assertEqual(r["section"], "I")
        self.assertEqual(r["section_title"], "Live Animals; Animal Products")
        self.assertEqual(r["depth"], 0)
        self.assertIsNone(r["parent_code"])
        self.assertEqual(self.row("70")["section"], "XIII")
        self.assertEqual(self.row("71")["section"], "XIV")
        self.assertEqual(self.row("88")["description"], "Aircraft, Spacecraft, and Parts Thereof")
        self.assertEqual(self.row("88")["section"], "XVII")
        self.assertEqual(self.row("84")["section"], "XVI")
        self.assertEqual(self.row("98")["section"], "XXII")
        self.assertEqual(self.row("98")["section_title"], "Special Classification Provisions")
        missing = self.con.execute(
            "SELECT count(*) FROM schedule_b_out WHERE section IS NULL OR section_title IS NULL"
        ).fetchone()[0]
        self.assertEqual(missing, 0)

    def test_commodity_line(self):
        r = self.row("0101210000")
        self.assertEqual(r["level"], "commodity")
        self.assertEqual(r["depth"], 3)
        self.assertEqual(r["parent_code"], "010121")
        self.assertEqual(r["code_display"], "0101.21.0000")
        self.assertEqual(r["text_source"], "census")
        self.assertEqual(r["description_short"], "HORSES, PUREBRED BREEDING, LIVE")
        self.assertEqual(r["description"], "HORSES, PUREBRED BREEDING, LIVE")
        self.assertEqual(r["hts_release"], "2026HTSRev22")
        self.assertEqual(r["unit_1"], "NO")
        self.assertIsNone(r["unit_2"])
        self.assertEqual(r["sitc"], "00150")
        self.assertEqual(r["end_use"], "10140")
        self.assertIs(r["agricultural"], True)
        self.assertEqual(r["naics"], "112920")
        self.assertEqual(r["hitech"], "00")
        self.assertIs(self.row("9801100000")["agricultural"], False)

    def test_census_text_has_its_spacing_collapsed(self):
        # Census prints "VALUE OF REPAIR/ALTER  ARTICLES" with two spaces.
        r = self.row("9801100000")
        self.assertEqual(r["description_short"], "VALUE OF REPAIR/ALTER ARTICLES PREVIOUS IMPORTED")
        self.assertNotIn("  ", r["description"])

    def test_upper_rows_carry_no_line_columns(self):
        carrying = self.con.execute(
            "SELECT code FROM schedule_b_out WHERE level <> 'commodity' AND (description_short IS NOT NULL "
            "OR unit_1 IS NOT NULL OR unit_2 IS NOT NULL OR sitc IS NOT NULL OR end_use IS NOT NULL "
            "OR agricultural IS NOT NULL OR naics IS NOT NULL OR hitech IS NOT NULL)"
        ).fetchall()
        self.assertEqual(carrying, [])

    def test_description_full_reads_down_the_ladder(self):
        r = self.row("0101210000")
        self.assertEqual(
            r["description_full"],
            SEP.join(["Live Animals", self.row("0101")["description"], self.row("010121")["description"],
                      "HORSES, PUREBRED BREEDING, LIVE"]),
        )
        # 0409 and 040900 are both named by USITC's 0409.00.00, "Natural honey": the
        # text appears once.
        r = self.row("0409000025")
        self.assertEqual(r["description_full"].count("Natural honey"), 1, r["description_full"])
        # 8800, 880000 and 8800000000 all read Census's one line: it appears once.
        r = self.row("8800000000")
        self.assertEqual(
            r["description_full"],
            SEP.join(["Aircraft, Spacecraft, and Parts Thereof", r["description"]]),
        )
        for code, desc, full in self.con.execute(
            "SELECT code, description, description_full FROM schedule_b_out"
        ).fetchall():
            self.assertTrue(full == desc or full.endswith(SEP + desc), f"{code}: {full}")
            parts = full.split(SEP)
            self.assertEqual(len(parts), len(set(parts)), f"{code}: {full}")
        # The odd padding heading: its text is its line's, two levels above the line.
        self.assertEqual(self.row(ODD)["text_source"], "census_derived")
        self.assertEqual(self.row(ODD + "10")["text_source"], "usitc")
        self.assertEqual(
            self.row(ODD + "100000")["description_full"],
            SEP.join(["Natural or Cultured Pearls, Precious or Semiprecious Stones, Precious Metals, Metals Clad "
                      "with Precious Metal, and Articles Thereof; Imitation Jewelry; Coin",
                      f"Made-up subheading {ODD}.10", f"MADE-UP LINE {ODD}100000, LONG"]),
        )

    def test_release(self):
        releases = self.con.execute("SELECT DISTINCT release FROM schedule_b_out").fetchall()
        self.assertEqual(len(releases), 1)
        self.assertRegex(releases[0][0], r"^2026/\d{4}-\d{2}-\d{2}$")
        self.assertEqual(releases[0][0], f"2026/{DATE}")

    def test_counts(self):
        counts = dict(self.con.execute("SELECT level, count(*) FROM schedule_b_out GROUP BY level").fetchall())
        self.assertEqual(counts, {"chapter": 7, "heading": 7 + 198, "subheading": 9 + 395, "commodity": 10 + 395})

    def test_columns_follow_the_core(self):
        cols = [(c[0], c[1]) for c in self.con.execute("DESCRIBE schedule_b_out").fetchall()]
        self.assertEqual(
            cols,
            [
                ("code", "VARCHAR"), ("code_display", "VARCHAR"), ("level", "VARCHAR"), ("depth", "INTEGER"),
                ("parent_code", "VARCHAR"), ("description", "VARCHAR"), ("description_full", "VARCHAR"),
                ("release", "VARCHAR"), ("text_source", "VARCHAR"), ("corpus", "VARCHAR"),
                ("description_short", "VARCHAR"), ("unit_1", "VARCHAR"), ("unit_2", "VARCHAR"),
                ("sitc", "VARCHAR"), ("end_use", "VARCHAR"), ("agricultural", "BOOLEAN"), ("naics", "VARCHAR"),
                ("hitech", "VARCHAR"), ("section", "VARCHAR"), ("section_title", "VARCHAR"),
                ("hts_release", "VARCHAR"),
            ],
        )
        r = self.row("0101210000")
        self.assertEqual(r["corpus"], r["description_full"].lower())

    def test_check_code_list_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "schedule_b.parquet"
            self.con.execute(f"COPY (SELECT * FROM schedule_b_out ORDER BY code) TO '{out}' (FORMAT parquet)")
            done = subprocess.run(
                [sys.executable, str(CHECK), "--parquet", str(out), "--levels", LEVELS],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


class FixtureTest(unittest.TestCase):
    def test_census_fixture_keeps_crlf(self):
        # .gitattributes keeps git from rewriting these line ends.
        data = (FIXTURE / "econcord.txt").read_bytes()
        self.assertIn(b"\r\n", data)
        self.assertEqual(data.count(b"\n"), data.count(b"\r\n"))


class RefusalTest(unittest.TestCase):
    def refused(self, run: Run) -> str:
        try:
            with self.assertRaises(duckdb.Error) as caught:
                run.execute()
            return str(caught.exception)
        finally:
            run.close()

    def test_release_that_names_no_revision(self):
        # error(NULL) raises nothing: a currentRelease with no name must still fail.
        message = self.refused(Run(release_after_body="{}"))
        self.assertIn("USITC currentRelease names no revision: 2026HTSRev22 / null", message)

    def test_edition_mismatch(self):
        message = self.refused(Run(year="2027", index_title="Schedule B 2027 - Browse Chapters"))
        self.assertIn("HS edition mismatch: Schedule B 2027, USITC 2026HTSRev22", message)

    def test_a_year_param_the_index_does_not_name(self):
        # `arc run --param year=2028` with the URLs still on the 2026 folder.
        message = self.refused(Run(year="2028"))
        self.assertIn("Schedule B index is for 2026, not the year param 2028", message)

    def test_an_index_that_names_no_year(self):
        message = self.refused(Run(index_title="Schedule B - Browse Chapters"))
        self.assertIn("Schedule B index is for no year, not the year param 2026", message)

    def test_headings_below_99_percent(self):
        message = self.refused(Run(drop_hts_digits=4))
        self.assertRegex(message, r"USITC names \d+ of \d+ headings outside chapter 98; 99% required")

    def test_headings_just_under_99_percent(self):
        # Outside chapter 98 the main run has 202 headings, 200 named (99.0%), and
        # passes. Dropping one padding heading row leaves 199 (98.5%): refused.
        message = self.refused(Run(drop_htsno=("7001",)))
        self.assertIn("USITC names 199 of 202 headings outside chapter 98; 99% required", message)

    def test_subheadings_just_under_99_percent(self):
        # 400 subheadings, 397 named: one fewer, 396 (99.0%), passes; two fewer, 395
        # (98.75%), is refused.
        run = Run(drop_htsno=("7001.10",))
        try:
            run.execute()
        finally:
            run.close()
        message = self.refused(Run(drop_htsno=("7001.10", "7001.20")))
        self.assertIn("USITC names 395 of 400 subheadings outside chapter 98; 99% required", message)

    def test_subheadings_below_99_percent(self):
        message = self.refused(Run(drop_hts_digits=6))
        self.assertRegex(message, r"USITC names \d+ of \d+ subheadings outside chapter 98; 99% required")


if __name__ == "__main__":
    unittest.main(verbosity=2)
