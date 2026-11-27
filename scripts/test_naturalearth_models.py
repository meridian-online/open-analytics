#!/usr/bin/env python3
"""Run the Natural Earth Protocol's SQL models over a small shapefile and check the result.

The models are `datasets/naturalearth/models/load.sql`, `countries.sql` and `names.sql`.
Each case writes a scratch Protocol directory: a shapefile of a few countries under
`build/naturalearth/countries/`, written with DuckDB's spatial extension from rows given
here, the archive's `VERSION.txt`, and an `aliases.csv`. It runs the three models on one
DuckDB connection from that directory, as the Protocol's `sql:` steps run them.

The rows are chosen for what the models decide: a country whose ISO_A3 is -99 but whose
ISO_A3_EH is not (France), two with no ISO code at all (Kosovo, Northern Cyprus), a
territory that shares its sovereign's name in SOVEREIGNT (New Caledonia), a code that is
also another country's name in Vietnamese (`ao`, Angola's code and Austria's name), an
accented name (Côte d'Ivoire), and a name two countries share at the same rank, which
must be dropped. The refusal cases change one input each.

Needs duckdb==2.0.0 in the running Python, and the spatial extension, which the models
install:

    uv run --quiet --with duckdb==2.0.0 python3 scripts/test_naturalearth_models.py
"""

from __future__ import annotations

import csv
import json
import os
import tempfile
import unittest
from pathlib import Path

import duckdb

REPO = Path(__file__).resolve().parent.parent
PROTOCOL = REPO / "datasets" / "naturalearth"
MODELS = [PROTOCOL / "models" / name for name in ("load.sql", "countries.sql", "names.sql")]
LANGS = ["AR", "BN", "DE", "EN", "ES", "FA", "FR", "EL", "HE", "HI", "HU", "ID", "IT", "JA",
         "KO", "NL", "PL", "PT", "RU", "SV", "TR", "UK", "UR", "VI", "ZH", "ZHT"]
COLUMNS = ["ADMIN", "NAME", "NAME_LONG", "FORMAL_EN", "NAME_ALT", "SOVEREIGNT", "ADM0_A3",
           "ISO_A3_EH", "ISO_A2_EH", "CONTINENT"] + [f"NAME_{lang}" for lang in LANGS]


def country(iso_a3_eh, adm0_a3, iso_a2_eh, name, sovereignt=None, continent="Europe", x=0, **names):
    row: dict[str, str | None] = {c: None for c in COLUMNS}
    row.update(ADMIN=name, NAME=name, NAME_LONG=name, SOVEREIGNT=sovereignt or name, ADM0_A3=adm0_a3,
               ISO_A3_EH=iso_a3_eh, ISO_A2_EH=iso_a2_eh, CONTINENT=continent, NAME_EN=name)
    row.update(names)
    row["wkt"] = f"POLYGON(({x} 0, {x + 1} 0, {x + 1} 1, {x} 1, {x} 0))"
    return row


COUNTRIES = [
    country("FRA", "FRA", "FR", "France", NAME_DE="Frankreich"),
    country("NCL", "NCL", "NC", "New Caledonia", sovereignt="France", continent="Oceania", x=2),
    country("AGO", "AGO", "AO", "Angola", continent="Africa", x=4, NAME_ES="Gemelo"),
    country("AUT", "AUT", "AT", "Austria", x=6, NAME_VI="Áo", NAME_DE="Österreich"),
    country("CIV", "CIV", "CI", "Côte d'Ivoire", continent="Africa", x=8, NAME_ES="Gemelo"),
    country("GBR", "GBR", "GB", "United Kingdom", x=10),
    country("-99", "KOS", "XK", "Kosovo", x=12),
    country("-99", "CYN", "-99", "N. Cyprus", sovereignt="Northern Cyprus", continent="Asia", x=14),
]
ALIASES = [("uk", "GBR", "The common abbreviation for the United Kingdom.")]


class Run:
    """A scratch Protocol directory holding a shapefile, VERSION.txt and aliases.csv."""

    def __init__(self, countries=COUNTRIES, aliases=ALIASES, version="5.1.1"):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        out = self.root / "build" / "naturalearth" / "countries"
        out.mkdir(parents=True)
        (out / "ne_110m_admin_0_countries.VERSION.txt").write_text(version + "\n")
        with open(self.root / "aliases.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["alias", "iso_a3", "reason"])
            w.writerows(aliases)
        con = duckdb.connect()
        con.execute("INSTALL spatial; LOAD spatial;")
        cols = ", ".join(f"{c} VARCHAR" for c in COLUMNS)
        con.execute(f"CREATE TABLE rows ({cols}, wkt VARCHAR)")
        con.executemany(f"INSERT INTO rows VALUES ({', '.join('?' * (len(COLUMNS) + 1))})",
                        [[r[c] for c in COLUMNS] + [r["wkt"]] for r in countries])
        con.execute(
            f"COPY (SELECT * EXCLUDE (wkt), ST_GeomFromText(wkt) AS geom FROM rows) "
            f"TO '{out / 'ne_110m_admin_0_countries.shp'}' "
            f"WITH (FORMAT GDAL, DRIVER 'ESRI Shapefile', LAYER_CREATION_OPTIONS 'ENCODING=UTF-8')"
        )
        con.close()

    def build(self):
        con = duckdb.connect()
        here = os.getcwd()
        os.chdir(self.root)
        try:
            for model in MODELS:
                con.execute(model.read_text())
        finally:
            os.chdir(here)
        return con

    def close(self):
        self.tmp.cleanup()


class ModelsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = Run()
        cls.con = cls.scratch.build()

    @classmethod
    def tearDownClass(cls):
        cls.con.close()
        cls.scratch.close()

    def rows(self, sql):
        return self.con.execute(sql).fetchall()

    def test_every_country_has_a_unique_key(self):
        keys = self.rows("SELECT iso_a3, iso_a2 FROM countries_110m_out ORDER BY iso_a3")
        self.assertEqual(len(keys), len(COUNTRIES))
        self.assertEqual(len({k for k, _ in keys}), len(keys))
        self.assertNotIn(None, [k for k, _ in keys])
        found = dict(keys)
        self.assertEqual(found["FRA"], "FR")
        self.assertEqual(found["KOS"], "XK", "Kosovo has no ISO_A3, so it takes ADM0_A3 and keeps XK")
        self.assertIsNone(found["CYN"], "Northern Cyprus has no ISO_A2, so iso_a2 is NULL, not -99")

    def test_geom_is_geojson_text(self):
        for (geom,) in self.rows("SELECT geom FROM countries_110m_out"):
            self.assertEqual(json.loads(geom)["type"], "Polygon")

    def test_a_key_resolves_at_its_strongest_source(self):
        names = dict(self.rows("SELECT name_key, iso_a3 FROM country_names_out"))
        expected = {
            "france": "FRA",          # France's NAME, not New Caledonia's SOVEREIGNT
            "frankreich": "FRA",
            "ao": "AGO",              # Angola's iso_a2, not Austria's name in Vietnamese
            "osterreich": "AUT",
            "cote d'ivoire": "CIV",
            "uk": "GBR",              # the alias
            "united kingdom": "GBR",
            "kos": "KOS",
            "xk": "KOS",
            "new caledonia": "NCL",
        }
        for key, iso in expected.items():
            self.assertEqual(names.get(key), iso, key)

    def test_a_key_two_countries_tie_on_is_dropped(self):
        names = dict(self.rows("SELECT name_key, iso_a3 FROM country_names_out"))
        self.assertNotIn("gemelo", names)
        dropped = self.rows("SELECT name_key, rank, countries FROM country_names_dropped")
        self.assertEqual(len(dropped), 1)
        self.assertEqual(dropped[0][:2], ("gemelo", 2))
        self.assertIn("AGO", dropped[0][2])
        self.assertIn("CIV", dropped[0][2])

    def test_name_key_is_unique_and_every_country_is_known(self):
        (n, distinct, orphans), = self.rows(
            "SELECT count(*), count(DISTINCT name_key), "
            "count(*) FILTER (WHERE iso_a3 NOT IN (SELECT iso_a3 FROM countries_110m_out)) "
            "FROM country_names_out")
        self.assertEqual(n, distinct)
        self.assertEqual(orphans, 0)


class RefusalsTest(unittest.TestCase):
    def refused(self, needle, **kw):
        run = Run(**kw)
        try:
            with self.assertRaises(duckdb.Error) as caught:
                run.build()
            self.assertIn(needle, str(caught.exception))
        finally:
            run.close()

    def test_another_release(self):
        self.refused("release is not 5.1.1", version="5.2.0")

    def test_a_repeated_key(self):
        self.refused("key repeated: FRA", countries=COUNTRIES + [country("FRA", "FR1", "FR", "Francia", x=20)])

    def test_a_country_with_no_key(self):
        self.refused("without a three-letter key", countries=COUNTRIES + [country("-99", "-99", "-99", "Nowhere", x=20)])

    def test_an_alias_for_no_country(self):
        self.refused("which is not a Natural Earth country", aliases=ALIASES + [("usa", "USA", "Not in the fixture.")])

    def test_an_alias_with_no_country(self):
        self.refused("no alias or no iso_a3", aliases=ALIASES + [("usa", "", "Blank code.")])

    def test_an_alias_with_no_reason(self):
        self.refused("has no reason", aliases=ALIASES + [("gb", "GBR", "")])

    def test_an_alias_repeated(self):
        self.refused("is repeated", aliases=ALIASES + [(" UK ", "GBR", "Repeated, padded.")])


class ShippedAliasesTest(unittest.TestCase):
    def test_every_alias_has_a_code_and_a_reason(self):
        with open(PROTOCOL / "aliases.csv", newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertTrue(rows)
        for row in rows:
            self.assertRegex(row["iso_a3"], r"^[A-Z]{3}$", row)
            self.assertTrue(row["alias"].strip(), row)
            self.assertTrue(row["reason"].strip(), row)


if __name__ == "__main__":
    unittest.main()
