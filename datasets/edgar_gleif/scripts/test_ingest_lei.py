#!/usr/bin/env python3
"""Self-test for the identifier gate in the crosswalk ingest — proves it can fail.

Cases here run the SQL models THIS REPO SHIPS, read off disk, against scratch fixtures
standing in for the fetch and export operators. Most run all four models in the order
`arcform.yaml` declares them; `test_the_arithmetic_agrees_with_an_independent_one` and
`test_a_malformed_value_is_not_accepted_as_valid` execute only the macro at the head of
`models/load.sql`, because the arithmetic needs no pipeline around it to be wrong.
Either way the bytes are the shipped ones and nothing about the check is re-implemented
here: if a model file stops filtering, or the gate is deleted from
`models/package.sql`, these cases go red, and they cannot go green against a copy of
the logic that agrees with itself.

Two mechanisms are under test, and they are not the same mechanism:

  the filter, in `models/load.sql`   N-CEN reports its LEI as filer-typed free text, so
                                     a value that fails ISO 7064 MOD 97-10 and that
                                     GLEIF does not publish never becomes an edge.
  the gate, in `models/package.sql`  every other route into the edge set — the RA000665
                                     register, the resolver — has no such filter, so a
                                     value failing both tests STOPS THE RUN.

They cross-check: delete the filter and the gate catches what it lets through, which is
why `test_deleting_the_ncen_filter_is_caught_by_the_gate` mutates the shipped model file
rather than trusting that the two are independent.

The arithmetic is pinned against an independent implementation — expand to digits, one
big integer, mod 97 — because a checksum that agrees only with itself is not a checksum.

Not covered, named rather than implied: `arc run` itself. The fetch, export, resolve,
validate and describe steps are arcform operators whose code lives in another repo, and
the resolve step is a multi-hour job; the fixtures stand in for their outputs. What is
covered is every `sql:` model the manifest names, byte for byte as shipped.

No case touches the network.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path

DATASET = Path(__file__).resolve().parent.parent
MANIFEST = DATASET / "arcform.yaml"

# Identifiers whose check digits are correct. Every one is asserted against the
# independent implementation in `test_the_arithmetic_agrees_with_an_independent_one`,
# so a typo here cannot quietly turn a fixture into the thing under test.
LEI_TRUST = "5493001KJTIIGC8Y1R12"  # the registrant of the scratch fund family
LEI_SERIES = "213800WAVVOPS85N2205"  # one of its series
LEI_OPCO = "529900T8BM49AURSDO55"  # an operating company, reached by the resolver
LEI_RA = "254900XU4138OHZ4BG72"  # an entity carried by the RA000665 register

# Identifiers whose check digits are wrong.
LEI_PLACEHOLDER = "00000000000000000000"  # a null written where a null belongs
LEI_TRANSCRIBED = "2549OOXU4138OHZ4BG72"  # LEI_RA with letter O typed for zero
LEI_INVENTED = "6GG1425B5X0SPG36P158"  # a value matching no issued identifier
LEI_MALFORMED = "52990-T8BM49AURSDO55"  # a character the LEI alphabet does not contain

# Wrong check digits AND a real GLEIF record: the register publishes identifiers whose
# digits do not satisfy the standard, and an entity GLEIF publishes is a real entity.
LEI_LEGACY = "579100JDDBCKJJEJC031"

AS_OF = "2026-08-12"


def mod97(lei: str) -> int:
    """ISO 7064 MOD 97-10, written the textbook way: expand, one integer, one modulo.

    Deliberately NOT the shipped fold. The model folds character by character to stay
    inside a fixed-width integer; if the two ever disagree, one of them is wrong and
    the point of having both is to be told.
    """
    return int("".join(str(int(character, 36)) for character in lei)) % 97


def sql_models() -> list[Path]:
    """The `sql:` models the manifest declares, in the order it declares them."""
    steps = re.findall(r"^\s+sql:\s*(\S+)\s*$", MANIFEST.read_text(encoding="utf-8"), re.MULTILINE)
    if not steps:
        raise AssertionError(f"{MANIFEST} declares no `sql:` steps; there is nothing to test")
    return [DATASET / step for step in steps]


class Fixture:
    """A scratch `build/` tree holding what the fetch and export operators would leave."""

    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="edgar-gleif-ingest-"))
        self.build = self.root / "build"
        self.build.mkdir()
        self.models = self.root / "models"
        self.models.mkdir()
        for model in sql_models():
            shutil.copy(model, self.models / model.name)
        self.ncen_registrant: list[tuple[str, str]] = [("1000001", LEI_TRUST)]
        self.ncen_series: list[tuple[str, str]] = [("S000000001", LEI_SERIES)]
        self.ra_sec: list[tuple[str, str, str]] = [("1000002", LEI_RA, "FUND")]
        self.resolved: list[tuple[str, str, str, str, float]] = [
            ("1000003", LEI_OPCO, "exact_name", "confirmed", 0.99)
        ]
        self.gleif: list[str] = [LEI_TRUST, LEI_SERIES, LEI_OPCO, LEI_RA, LEI_LEGACY]

    def model(self, name: str) -> Path:
        return self.models / name

    def write(self) -> None:
        import duckdb

        def literal(value: str) -> str:
            return "'" + str(value).replace("'", "''") + "'"

        con = duckdb.connect()
        rows = ", ".join(
            f"({literal(lei)}, {literal('SCRATCH ENTITY ' + lei[:6])}, 'US', 'NEW YORK', 'US-DE', 'ISSUED')"
            for lei in self.gleif
        )
        con.execute(
            "COPY (SELECT * FROM (VALUES " + rows + ") "
            "t(lei, legal_name, country, city, jurisdiction, registration_status)) "
            f"TO '{self.build / 'gleif.parquet'}' (FORMAT parquet)"
        )
        con.execute(
            "COPY (SELECT * FROM (VALUES (1000003, 'SCRATCH', 'NYSE'), (1000001, 'TRUST', 'NASDAQ')) "
            f"t(cik, ticker, exchange)) TO '{self.build / 'edgar.parquet'}' (FORMAT parquet)"
        )
        resolved = ", ".join(
            f"({literal(cik)}, {literal(lei)}, {literal(method)}, {literal(status)}, {probability})"
            for cik, lei, method, status, probability in self.resolved
        )
        con.execute(
            "COPY (SELECT * FROM (VALUES " + resolved + ") "
            "t(cik, lei, method, status, match_probability)) "
            f"TO '{self.build / 'resolved.parquet'}' (FORMAT parquet)"
        )
        con.close()

        (self.build / "cik_lookup.txt").write_text(
            "".join(
                f"SCRATCH COMPANY {cik}:{int(cik):010d}:\n"
                for cik in ("1000001", "1000002", "1000003", "1000004", "1000005")
            ),
            encoding="utf-8",
        )
        (self.build / "gleif_ra_sec.csv").write_text(
            "lei,registered_as,category\n"
            + "".join(f"{lei},{key},{category}\n" for key, lei, category in self.ra_sec),
            encoding="utf-8",
        )
        for quarter, table, rows_ in (
            ("2026q2", "REGISTRANT.tsv", self.ncen_registrant),
            ("2026q2", "FUND_REPORTED_INFO.tsv", self.ncen_series),
        ):
            directory = self.build / "ncen" / quarter
            directory.mkdir(parents=True, exist_ok=True)
            header = "CIK\tLEI\n" if table == "REGISTRANT.tsv" else "SERIES_ID\tLEI\n"
            directory.joinpath(table).write_text(
                header + "".join(f"{key}\t{lei}\n" for key, lei in rows_), encoding="utf-8"
            )

    def run(self):
        """Run every shipped model in manifest order; return the terminal rows."""
        import duckdb

        self.write()
        cwd = Path.cwd()
        os.chdir(self.root)  # the models address build/… relatively, as `arc run` does
        try:
            con = duckdb.connect(str(self.build / "edgar_gleif.db"))
            # `getenv` is a builtin of the DuckDB CLI and of no other client;
            # models/package.sql reads ARC_PARAM_AS_OF through it. Standing it up here
            # is the run environment the engine supplies, not any part of the check
            # under test — no model byte changes, and `as_of` is not what is asserted.
            con.execute(f"CREATE OR REPLACE MACRO getenv(name) AS '{AS_OF}'")
            for model in sql_models():
                con.execute(self.model(model.name).read_text(encoding="utf-8"))
                if model.name == "sec_entities.sql":
                    # Stands in for the `export_sec_entities` parquet_export operator,
                    # the one hand-off between two models that is not itself a model.
                    con.execute("COPY sec_entities TO 'build/sec_entities.parquet' (FORMAT parquet)")
            rows = con.execute(
                "SELECT key_type, key, lei, method, tier FROM edgar_gleif_out ORDER BY key_type, key, lei"
            ).fetchall()
            con.close()
            return rows
        finally:
            os.chdir(cwd)

    def destroy(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


class IngestLeiSelfTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = Fixture()
        self.addCleanup(self.fixture.destroy)

    def leis(self, rows) -> set[str]:
        return {row[2] for row in rows}

    def assertRunFails(self, needle: str) -> None:  # noqa: N802 - unittest naming
        with self.assertRaises(Exception) as caught:
            self.fixture.run()
        message = str(caught.exception)
        self.assertIn("fails ISO 7064 MOD 97-10", message)
        self.assertIn(needle, message)

    # ── the arithmetic ────────────────────────────────────────────────────────────
    def test_the_arithmetic_agrees_with_an_independent_one(self) -> None:
        import duckdb

        con = duckdb.connect()
        con.execute(DATASET.joinpath("models/load.sql").read_text(encoding="utf-8").split("CREATE OR REPLACE TABLE")[0])
        for lei in (LEI_TRUST, LEI_SERIES, LEI_OPCO, LEI_RA, LEI_LEGACY, LEI_PLACEHOLDER, LEI_TRANSCRIBED, LEI_INVENTED):
            with self.subTest(lei=lei):
                self.assertEqual(con.execute("SELECT lei_mod97(?)", [lei]).fetchone()[0], mod97(lei))
        for lei in (LEI_TRUST, LEI_SERIES, LEI_OPCO, LEI_RA):
            self.assertEqual(mod97(lei), 1, f"fixture {lei} was meant to be a well-formed identifier")
        for lei in (LEI_PLACEHOLDER, LEI_TRANSCRIBED, LEI_INVENTED, LEI_LEGACY):
            self.assertNotEqual(mod97(lei), 1, f"fixture {lei} was meant to fail the checksum")
        con.close()

    def test_a_malformed_value_is_not_accepted_as_valid(self) -> None:
        """NULL and out-of-alphabet characters must not read as a satisfied check."""
        import duckdb

        con = duckdb.connect()
        con.execute(DATASET.joinpath("models/load.sql").read_text(encoding="utf-8").split("CREATE OR REPLACE TABLE")[0])

        def rejected(value: str | None) -> bool:
            return con.execute(
                "SELECT lei_mod97(CAST(? AS VARCHAR)) IS DISTINCT FROM 1", [value]
            ).fetchone()[0]

        for value in (None, "", "52990-T8BM49AURSDO55", "529900T8BM49AURSDO5"):
            with self.subTest(rejects=value):
                self.assertTrue(rejected(value))
        for value in ("529900t8bm49aursdo55", "  529900T8BM49AURSDO55  "):
            with self.subTest(accepts=value):
                self.assertFalse(rejected(value))
        con.close()

    # ── the happy path ────────────────────────────────────────────────────────────
    def test_well_formed_identifiers_all_survive(self) -> None:
        rows = self.fixture.run()
        self.assertEqual(self.leis(rows), {LEI_TRUST, LEI_SERIES, LEI_OPCO, LEI_RA})

    # ── the filter, in models/load.sql ────────────────────────────────────────────
    def test_an_ncen_placeholder_never_becomes_an_edge(self) -> None:
        self.fixture.ncen_registrant.append(("1000004", LEI_PLACEHOLDER))
        self.fixture.ncen_series.append(("S000000002", LEI_PLACEHOLDER))
        rows = self.fixture.run()
        self.assertNotIn(LEI_PLACEHOLDER, self.leis(rows))
        self.assertNotIn("1000004", {row[1] for row in rows})

    def test_an_ncen_transcription_never_becomes_an_edge(self) -> None:
        """A single letter O typed for a zero is a different number, not a weaker one."""
        self.fixture.ncen_registrant.append(("1000004", LEI_TRANSCRIBED))
        rows = self.fixture.run()
        self.assertNotIn(LEI_TRANSCRIBED, self.leis(rows))

    def test_an_ncen_lei_gleif_publishes_survives_a_failed_checksum(self) -> None:
        """The register's word beats our arithmetic: dropping this drops a real entity."""
        self.fixture.ncen_registrant.append(("1000004", LEI_LEGACY))
        rows = self.fixture.run()
        self.assertIn(LEI_LEGACY, self.leis(rows))

    # ── the gate, in models/package.sql ───────────────────────────────────────────
    def test_the_register_route_stops_the_run(self) -> None:
        """RA000665 carries no placeholders today; if it ever does, do not publish it."""
        self.fixture.ra_sec.append(("1000004", LEI_INVENTED, "FUND"))
        self.assertRunFails(LEI_INVENTED)

    def test_the_resolver_route_stops_the_run(self) -> None:
        self.fixture.resolved.append(("1000004", LEI_TRANSCRIBED, "exact_name", "confirmed", 0.97))
        self.assertRunFails(LEI_TRANSCRIBED)

    def test_an_unreadable_identifier_stops_the_run(self) -> None:
        """An unknown remainder is not a passing one.

        A character outside [0-9A-Z] makes `lei_mod97` NULL, and `NULL <> 1` is NULL,
        which a WHERE clause drops — the row would ship. That is what `IS DISTINCT
        FROM` in the gate is for. (A NULL identifier folds to 0 and either operator
        catches it, so a NULL alone does not exercise this.)
        """
        for lei in (LEI_MALFORMED, None):
            with self.subTest(lei=lei):
                fixture = Fixture()
                self.addCleanup(fixture.destroy)
                fixture.resolved.append(("1000004", lei, "exact_name", "confirmed", 0.97))
                with self.assertRaises(Exception) as caught:
                    fixture.run()
                self.assertIn("fails ISO 7064 MOD 97-10", str(caught.exception))

    def test_the_gate_counts_what_it_refuses(self) -> None:
        """A refusal that does not say how much is wrong cannot be acted on."""
        self.fixture.ra_sec.append(("1000004", LEI_INVENTED, "FUND"))
        self.fixture.ra_sec.append(("1000005", LEI_PLACEHOLDER, "FUND"))
        with self.assertRaises(Exception) as caught:
            self.fixture.run()
        self.assertIn("2 edge(s)", str(caught.exception))
        self.assertIn("2 distinct value(s)", str(caught.exception))

    def test_the_gate_lets_a_gleif_published_identifier_through(self) -> None:
        self.fixture.ra_sec.append(("1000004", LEI_LEGACY, "FUND"))
        self.assertIn(LEI_LEGACY, self.leis(self.fixture.run()))

    # ── the two mechanisms are not one mechanism ──────────────────────────────────
    def test_deleting_the_ncen_filter_is_caught_by_the_gate(self) -> None:
        """Mutate the shipped model: without the filter the placeholder reaches the gate."""
        self.fixture.ncen_registrant.append(("1000004", LEI_PLACEHOLDER))
        model = self.fixture.model("load.sql")
        unfiltered = re.sub(
            r"\n\s*AND \(lei_mod97\(n\.LEI\) = 1\n\s*OR EXISTS \(SELECT 1 FROM gleif g WHERE g\.lei = upper\(trim\(n\.LEI\)\)\)\)",
            "",
            model.read_text(encoding="utf-8"),
        )
        self.assertNotIn("lei_mod97(n.LEI)", unfiltered, "the filter was not where this test thought")
        model.write_text(unfiltered, encoding="utf-8")
        self.assertRunFails(LEI_PLACEHOLDER)


if __name__ == "__main__":
    unittest.main(verbosity=2)
