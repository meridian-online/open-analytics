#!/usr/bin/env python3
"""Run the title-index and crosswalk contract checks over small in-memory tables.

The checks are `contracts/check_titles.sql` and `contracts/check_crosswalk.sql`, which
the SOC Protocols run as `sql:` steps. Each case builds the views and tables the
contracts read (see `contracts/README.md`) on a fresh DuckDB connection, runs the SQL
file, and asserts either the result table's row count or the `InvalidInputException`
DuckDB raises, with the rule name (and the first offending value) in its message.

The code list is three rows: two detailed occupations (`11-1011`, `11-1021`) and one
broad group (`11-1000`). Nothing here is cut from a real file.

Needs duckdb==1.5.5 in the running Python:

    uv run --quiet --with duckdb==1.5.5 python3 scripts/test_contracts.py
"""

from __future__ import annotations

import unittest
from pathlib import Path

import duckdb

CONTRACTS = Path(__file__).resolve().parent.parent / "contracts"

CODE_LIST = """
CREATE VIEW contract_code_list AS SELECT * FROM (VALUES
    ('11-1000', 'broad', 'Top executives'),
    ('11-1011', 'detailed', 'Chief executives'),
    ('11-1021', 'detailed', 'General and operations managers')
) AS t(code, level, description)
"""

TITLE_COLS = "entry_id, title, code, code_description, release, text_source, corpus"
TITLE_ROW = "('11-1011|ceo', 'CEO', '11-1011', 'Chief executives', '2020-04-15', 'BLS', 'ceo')"
TITLE_ROW2 = "('11-1021|gm', 'GM', '11-1021', 'General and operations managers', '2020-04-15', 'BLS', 'gm')"

XW_COLS = "from_code, from_description, to_code, to_description, from_edition, to_edition, release"
XW_ROW = "('11-1011', 'Chief executives', '11-1011', 'Chief executives', '2010', '2018', '2017-11')"
XW_ROW2 = "('11-1021', 'General managers', '11-1021', 'General and operations managers', '2010', '2018', '2017-11')"


def titles(rows=(TITLE_ROW, TITLE_ROW2), cols=TITLE_COLS):
    con = duckdb.connect()
    con.execute(CODE_LIST)
    con.execute("CREATE TABLE contract_title_level AS SELECT 'detailed' AS level")
    names = [c.strip() for c in cols.split(",")]
    if rows:
        con.execute(f"CREATE VIEW contract_titles AS SELECT * FROM (VALUES {', '.join(rows)}) AS t({cols})")
    else:
        typed = ", ".join(f"NULL::VARCHAR AS {n}" for n in names)
        con.execute(f"CREATE VIEW contract_titles AS SELECT {typed} WHERE false")
    return con


def crosswalk(rows=(XW_ROW, XW_ROW2), cols=XW_COLS):
    con = duckdb.connect()
    con.execute(CODE_LIST)
    con.execute(
        "CREATE TABLE contract_crosswalk_shape AS "
        "SELECT '[0-9]{2}-[0-9]{4}' AS from_pattern, 'detailed' AS level"
    )
    names = [c.strip() for c in cols.split(",")]
    if rows:
        con.execute(f"CREATE VIEW contract_crosswalk AS SELECT * FROM (VALUES {', '.join(rows)}) AS t({cols})")
    else:
        typed = ", ".join(f"NULL::VARCHAR AS {n}" for n in names)
        con.execute(f"CREATE VIEW contract_crosswalk AS SELECT {typed} WHERE false")
    return con


def run(con, name):
    con.execute((CONTRACTS / name).read_text())


class Titles(unittest.TestCase):
    def fails(self, con, *fragments):
        with self.assertRaises(duckdb.InvalidInputException) as cm:
            run(con, "check_titles.sql")
        for f in fragments:
            self.assertIn(f, str(cm.exception))

    def test_titles_pass(self):
        con = titles()
        run(con, "check_titles.sql")
        self.assertEqual(con.execute("SELECT rows_checked FROM contract_titles_result").fetchall(), [(2,)])

    def test_titles_extra_columns_after_core_pass(self):
        con = titles(
            rows=("('a', 'A', '11-1011', 'Chief executives', 'r', 'BLS', 'a', true)",),
            cols=TITLE_COLS + ", illustrative",
        )
        run(con, "check_titles.sql")
        self.assertEqual(con.execute("SELECT rows_checked FROM contract_titles_result").fetchall(), [(1,)])

    def test_titles_empty(self):
        self.fails(titles(rows=()), "titles: no rows")

    def test_titles_column_order(self):
        cols = "title, entry_id, code, code_description, release, text_source, corpus"
        row = "('CEO', '11-1011|ceo', '11-1011', 'Chief executives', 'r', 'BLS', 'ceo')"
        self.fails(titles(rows=(row,), cols=cols), "titles: core columns")

    def test_titles_duplicate_entry_id(self):
        dup = TITLE_ROW2.replace("11-1021|gm", "11-1011|ceo")
        self.fails(titles(rows=(TITLE_ROW, dup)), "titles: repeated entry_id: 11-1011|ceo")

    def test_titles_null_entry_id(self):
        row = TITLE_ROW.replace("'11-1011|ceo'", "NULL")
        self.fails(titles(rows=(row,)), "titles: NULL entry_id")

    def test_titles_null_title(self):
        row = TITLE_ROW.replace("'CEO'", "NULL")
        self.fails(titles(rows=(row,)), "titles: NULL title")

    def test_titles_null_code(self):
        row = TITLE_ROW.replace("'11-1011', 'Chief", "NULL, 'Chief")
        self.fails(titles(rows=(row,)), "titles: NULL code")

    def test_titles_code_not_in_list(self):
        row = TITLE_ROW.replace("'11-1011', 'Chief", "'11-9999', 'Chief")
        self.fails(titles(rows=(row,)), "titles: code not in code list: 11-9999")

    def test_titles_wrong_level(self):
        row = TITLE_ROW.replace("'11-1011', 'Chief", "'11-1000', 'Chief")
        self.fails(titles(rows=(row,)), "titles: code at the wrong level: 11-1000")

    def test_titles_two_releases(self):
        row = TITLE_ROW2.replace("2020-04-15", "2021-01-01")
        self.fails(titles(rows=(TITLE_ROW, row)), "titles: more than one release")


class Crosswalk(unittest.TestCase):
    def fails(self, con, *fragments):
        with self.assertRaises(duckdb.InvalidInputException) as cm:
            run(con, "check_crosswalk.sql")
        for f in fragments:
            self.assertIn(f, str(cm.exception))

    def test_crosswalk_pass(self):
        con = crosswalk()
        run(con, "check_crosswalk.sql")
        self.assertEqual(con.execute("SELECT rows_checked FROM contract_crosswalk_result").fetchall(), [(2,)])

    def test_crosswalk_empty(self):
        self.fails(crosswalk(rows=()), "crosswalk: no rows")

    def test_crosswalk_column_order(self):
        cols = "from_description, from_code, to_code, to_description, from_edition, to_edition, release"
        row = "('d', '11-1011', '11-1011', 'd', '2010', '2018', 'r')"
        self.fails(crosswalk(rows=(row,), cols=cols), "crosswalk: core columns")

    def test_crosswalk_duplicate_pair(self):
        self.fails(crosswalk(rows=(XW_ROW, XW_ROW)), "crosswalk: repeated pair: 11-1011 -> 11-1011")

    def test_crosswalk_null_code(self):
        row = XW_ROW.replace("('11-1011'", "(NULL", 1)
        self.fails(crosswalk(rows=(row,)), "crosswalk: NULL from_code")
        row = "('11-1011', 'd', NULL, 'd', '2010', '2018', 'r')"
        self.fails(crosswalk(rows=(row,)), "crosswalk: NULL to_code")

    def test_crosswalk_to_code_not_in_list(self):
        row = "('11-1011', 'd', '11-9999', 'd', '2010', '2018', 'r')"
        self.fails(crosswalk(rows=(row,)), "crosswalk: to_code not in code list: 11-9999")

    def test_crosswalk_to_code_wrong_level(self):
        row = "('11-1011', 'd', '11-1000', 'd', '2010', '2018', 'r')"
        self.fails(crosswalk(rows=(row,)), "crosswalk: to_code at the wrong level: 11-1000")

    def test_crosswalk_from_code_shape(self):
        row = "('1110', 'd', '11-1011', 'd', '2010', '2018', 'r')"
        self.fails(crosswalk(rows=(row,)), "crosswalk: from_code of the wrong shape: 1110")

    def test_crosswalk_two_releases(self):
        row = XW_ROW2.replace("2017-11", "2018-01")
        self.fails(crosswalk(rows=(XW_ROW, row)), "crosswalk: more than one release")


if __name__ == "__main__":
    unittest.main()
