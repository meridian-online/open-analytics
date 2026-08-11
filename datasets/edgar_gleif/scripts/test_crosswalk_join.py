#!/usr/bin/env python3
"""Self-test for crosswalk_join.py — proves the runner can fail, and on what.

Every case builds a scratch dataset tree (real Parquet files and real
`datapackage.json` descriptors beside them), runs the runner as a subprocess, and
asserts BOTH the exit status AND the message. Status alone cannot tell a refusal
apart from a crash, which is why the runner distinguishes 1 (the data disagrees
with the declaration) from 2 (the declaration could not be run) and why every case
below pins the number it expects.

The two shapes worth reddening are a coverage figure that has drifted from the data
and a declaration the runner does not implement. Both are covered here, along with
the join that spans an integer column and a text one — which is the case the real
crosswalk needs and the case a naive equality would get wrong.

Not covered, named rather than implied: the multi-field arity mismatch, the
ambiguous-resource branch, and the missing-`x-joins` branch each behave correctly in
the shipped runner but no case here reddens when they are deleted.

No case touches the network: every fixture resource is a relative path next to its
descriptor, so this runs offline and cannot be flaky for a reason that has nothing
to do with the runner.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

RUNNER = Path(__file__).with_name("crosswalk_join.py")

EXIT_OK = 0
EXIT_VIOLATIONS = 1
EXIT_ERROR = 2


def write_parquet(target: Path, select_sql: str) -> None:
    import duckdb

    target.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"COPY ({select_sql}) TO '{target}' (FORMAT parquet)")
    con.close()


def write_package(
    root: Path,
    slug: str,
    *,
    select_sql: str,
    fields: list[dict[str, Any]],
    joins: list[dict[str, Any]] | None = None,
) -> Path:
    """Lay down datasets/<slug>/{<slug>.parquet, datapackage.json} and return the descriptor."""
    package_dir = root / slug
    parquet = package_dir / f"{slug}.parquet"
    write_parquet(parquet, select_sql)

    descriptor: dict[str, Any] = {
        "name": slug,
        "title": slug,
        "resources": [
            {
                "name": slug,
                "format": "parquet",
                "path": parquet.name,
                "schema": {"fields": fields},
            }
        ],
    }
    if joins is not None:
        descriptor["x-joins"] = {"joins": joins}

    path = package_dir / "datapackage.json"
    path.write_text(json.dumps(descriptor, indent=2) + "\n", encoding="utf-8")
    return path


def run_join(datasets_dir: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(RUNNER), "--datasets-dir", str(datasets_dir), *extra],
        capture_output=True,
        text=True,
        check=False,
    )


# The fixture the cases vary: a text key column holding two schemes, joined to an
# integer column in a sibling package. Two of the three numeric-typed rows have a
# partner; the prefixed row can never have one.
LEFT_SQL = """
SELECT * FROM (VALUES
    ('num', '101'), ('num', '102'), ('num', '999'), ('pre', 'S000000001')
) t(key_type, key)
"""
RIGHT_SQL = "SELECT * FROM (VALUES (101), (102), (777)) t(id)"

LEFT_FIELDS = [
    {"name": "key_type", "type": "string"},
    {"name": "key", "type": "string"},
]
RIGHT_FIELDS = [{"name": "id", "type": "integer"}]


def join_declaration(**overrides: Any) -> dict[str, Any]:
    declaration: dict[str, Any] = {
        "name": "right",
        "fields": ["key"],
        "reference": {"resource": "right", "fields": ["id"]},
        "compareAs": "text",
        "where": {"field": "key_type", "equals": "num"},
        "coverage": {"rows": 3, "matched": 2},
    }
    declaration.update(overrides)
    return declaration


class CrosswalkJoinSelfTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="crosswalk-join-")
        self.addCleanup(self._tmp.cleanup)
        self.datasets = Path(self._tmp.name) / "datasets"
        self.datasets.mkdir(parents=True)
        write_package(self.datasets, "right", select_sql=RIGHT_SQL, fields=RIGHT_FIELDS)

    def lay_left(self, **overrides: Any) -> None:
        write_package(
            self.datasets,
            "left",
            select_sql=LEFT_SQL,
            fields=LEFT_FIELDS,
            joins=[join_declaration(**overrides)],
        )

    def assertOutcome(  # noqa: N802 - unittest naming
        self,
        result: subprocess.CompletedProcess[str],
        expected_exit: int,
        *needles: str,
    ) -> None:
        output = result.stdout + result.stderr
        self.assertEqual(
            result.returncode,
            expected_exit,
            f"expected exit {expected_exit}, got {result.returncode}\n--- output ---\n{output}",
        )
        for needle in needles:
            self.assertIn(needle, output, f"message did not name {needle!r}\n--- output ---\n{output}")

    # ---------------------------------------------------------------- baseline

    def test_true_declaration_runs_and_passes(self) -> None:
        """A text-to-integer join, filtered by the discriminator, with exact coverage."""
        self.lay_left()
        self.assertOutcome(
            run_join(self.datasets),
            EXIT_OK,
            "left.key (string) = right.id (integer)",
            "declared  rows 3  matched 2",
            "measured  rows 3  matched 2",
            "1 declared join(s) ran",
        )

    # ------------------------------------------------------- the data disagrees

    def test_overstated_match_count_reddens(self) -> None:
        """The defect this exists to catch: a coverage figure better than the data."""
        self.lay_left(coverage={"rows": 3, "matched": 3})
        self.assertOutcome(
            run_join(self.datasets),
            EXIT_VIOLATIONS,
            "declares 3 matched row(s); the join finds 2",
            "FAILED",
        )

    def test_understated_match_count_reddens(self) -> None:
        """Drift in either direction is drift; a stale low figure is not 'safe'."""
        self.lay_left(coverage={"rows": 3, "matched": 1})
        self.assertOutcome(
            run_join(self.datasets),
            EXIT_VIOLATIONS,
            "declares 1 matched row(s); the join finds 2",
        )

    def test_wrong_row_count_reddens(self) -> None:
        self.lay_left(coverage={"rows": 4, "matched": 2})
        self.assertOutcome(
            run_join(self.datasets),
            EXIT_VIOLATIONS,
            "declares 4 row(s) in scope; the published data has 3",
        )

    def test_dropping_the_filter_reddens(self) -> None:
        """Without `where`, the prefixed row is in scope and the declared rows are wrong."""
        self.lay_left(where=None)
        self.assertOutcome(
            run_join(self.datasets),
            EXIT_VIOLATIONS,
            "declares 3 row(s) in scope; the published data has 4",
        )

    def test_text_rendering_spans_a_polymorphic_column(self) -> None:
        """Rendered as text, the whole column joins — the prefixed row simply misses."""
        self.lay_left(where=None, coverage={"rows": 4, "matched": 2})
        self.assertOutcome(
            run_join(self.datasets),
            EXIT_OK,
            "every row; compared as text",
            "measured  rows 4  matched 2",
        )

    def test_exact_comparison_cannot_span_a_polymorphic_column(self) -> None:
        """The pair above and this one are why `compareAs` is load-bearing.

        Same rows, same columns, same coverage — only the declared comparison
        differs. `exact` leaves the engine to reconcile a text column with an
        integer one, which it does by casting the text, and the prefixed value has
        no integer to cast to. The join does not return a wrong answer; it does not
        run at all. That is the whole reason the shipped declaration says `text`.
        """
        self.lay_left(where=None, compareAs="exact", coverage={"rows": 4, "matched": 2})
        self.assertOutcome(
            run_join(self.datasets),
            EXIT_ERROR,
            "the join would not execute",
        )

    # ------------------------------------------------- the declaration cannot run

    def test_unimplemented_comparison_refuses_rather_than_passing(self) -> None:
        self.lay_left(compareAs="fuzzy")
        self.assertOutcome(
            run_join(self.datasets),
            EXIT_ERROR,
            "compareAs 'fuzzy' is not a comparison this script performs",
            "refusing to report the join as running",
        )

    def test_unimplemented_where_shape_refuses_rather_than_passing(self) -> None:
        self.lay_left(where={"field": "key_type", "startsWith": "n"})
        self.assertOutcome(
            run_join(self.datasets),
            EXIT_ERROR,
            "`where` must name exactly ['equals', 'field']",
        )

    def test_missing_coverage_refuses(self) -> None:
        """A join with no pinned coverage would run and assert nothing."""
        self.lay_left(coverage={"rows": 3})
        self.assertOutcome(
            run_join(self.datasets),
            EXIT_ERROR,
            "`coverage` must declare exactly ['matched', 'rows']",
        )

    def test_impossible_coverage_refuses(self) -> None:
        self.lay_left(coverage={"rows": 3, "matched": 9})
        self.assertOutcome(
            run_join(self.datasets),
            EXIT_ERROR,
            "declares more matched rows (9) than rows (3)",
        )

    def test_unresolvable_reference_refuses(self) -> None:
        self.lay_left(reference={"resource": "nowhere", "fields": ["id"]})
        self.assertOutcome(
            run_join(self.datasets),
            EXIT_ERROR,
            "references resource 'nowhere', which no package declares",
        )

    def test_reference_to_undeclared_field_refuses(self) -> None:
        self.lay_left(reference={"resource": "right", "fields": ["missing"]})
        self.assertOutcome(
            run_join(self.datasets),
            EXIT_ERROR,
            "references right.missing, which that package does not declare",
        )

    def test_local_field_no_resource_declares_refuses(self) -> None:
        self.lay_left(fields=["absent"])
        self.assertOutcome(
            run_join(self.datasets),
            EXIT_ERROR,
            "no resource in this package declares all of ['absent', 'key_type']",
        )

    def test_no_dataset_declares_joins_refuses(self) -> None:
        """Silence is not a pass: a run that asserted nothing must not exit 0."""
        write_package(self.datasets, "left", select_sql=LEFT_SQL, fields=LEFT_FIELDS)
        self.assertOutcome(run_join(self.datasets), EXIT_ERROR, "no dataset declares `x-joins`")


if __name__ == "__main__":
    unittest.main(verbosity=2)
