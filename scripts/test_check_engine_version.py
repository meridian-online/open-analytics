#!/usr/bin/env python3
"""Self-test for check_engine_version.py — proves the gate can fail, and on what.

Every case builds a scratch dataset tree (a real Parquet file, written by the DuckDB
this job actually installed, and a real `datapackage.json` beside it), runs the
checker as a subprocess, and asserts BOTH the exit status AND the message.

The disagreeing case does not merely assert a fabricated string; it patches the
`created_by` field inside a real footer that DuckDB itself wrote, in place, at the
byte level — the same technique a version bump uses in production, since
`created_by` is a fixed-width field for any `vX.Y.Z (build <10 hex chars>)` string.
Reading it back with `parquet_file_metadata()` afterwards proves the patched footer
is what the checker actually sees, not a string asserted in this file's memory.

A rule this suite does not cover: comment out the `written != pin` comparison in
`check_resources()` (or replace it with `False`), rerun `test_mismatch_reddens_...`,
and it fails — see the card's report for the exact line broken and put back.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CHECKER = Path(__file__).with_name("check_engine_version.py")

EXIT_OK = 0
EXIT_MISMATCH = 1
EXIT_ERROR = 2


def write_parquet(target: Path, select_sql: str) -> None:
    import duckdb

    target.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"COPY ({select_sql}) TO '{target}' (FORMAT parquet)")
    con.close()


def real_created_by(target: Path) -> str:
    import duckdb

    con = duckdb.connect()
    row = con.execute("SELECT created_by FROM parquet_file_metadata(?)", [str(target)]).fetchone()
    con.close()
    return row[0]


def patch_created_by_to_disagree(target: Path) -> tuple[str, str]:
    """Rewrite the real `created_by` in `target`'s footer to a different version,
    at the same byte length, so no other offset in the footer has to move.

    Returns (original_created_by, patched_created_by).
    """
    original = real_created_by(target)
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", original)
    if match is None:
        raise AssertionError(f"fixture created_by {original!r} does not carry a version to patch")
    patch_digits = match.group(3)
    # Rotate every digit by one — guaranteed different, guaranteed the same length.
    rotated = "".join(str((int(digit) + 1) % 10) for digit in patch_digits)
    patched = original[: match.start(3)] + rotated + original[match.end(3) :]
    assert len(patched) == len(original), "patch changed the footer's byte length"
    assert patched != original

    raw = target.read_bytes()
    old_bytes = original.encode("utf-8")
    new_bytes = patched.encode("utf-8")
    assert raw.count(old_bytes) == 1, f"expected exactly one occurrence of {original!r} in the footer"
    target.write_bytes(raw.replace(old_bytes, new_bytes, 1))
    return original, patched


def write_package(root: Path, slug: str) -> Path:
    package_dir = root / slug
    parquet = package_dir / f"{slug}.parquet"
    write_parquet(parquet, "SELECT i AS n FROM range(3) t(i)")
    descriptor = package_dir / "datapackage.json"
    descriptor.write_text(
        json.dumps(
            {
                "name": slug,
                "resources": [
                    {
                        "name": slug,
                        "format": "parquet",
                        "path": parquet.name,
                        "schema": {"fields": [{"name": "n", "type": "integer"}]},
                    }
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return descriptor


def run_check(datasets_dir: Path, *extra: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    import os

    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, str(CHECKER), "--datasets-dir", str(datasets_dir), *extra],
        capture_output=True,
        text=True,
        check=False,
        env=full_env,
    )


class EngineVersionCheckSelfTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="engine-version-check-")
        self.addCleanup(self._tmp.cleanup)
        self.datasets = Path(self._tmp.name) / "datasets"
        self.datasets.mkdir(parents=True)

    def assertOutcome(  # noqa: N802 - unittest naming
        self, result: subprocess.CompletedProcess[str], expected_exit: int, *needles: str
    ) -> None:
        output = result.stdout + result.stderr
        self.assertEqual(
            result.returncode,
            expected_exit,
            f"expected exit {expected_exit}, got {result.returncode}\n--- output ---\n{output}",
        )
        for needle in needles:
            self.assertIn(needle, output, f"message did not name {needle!r}\n--- output ---\n{output}")

    def test_footer_agreeing_with_the_pin_passes(self) -> None:
        write_package(self.datasets, "widgets")
        # No DUCKDB_VERSION override: the pin falls back to the running duckdb, which
        # is exactly the engine that wrote this fixture's footer.
        self.assertOutcome(run_check(self.datasets, env={"DUCKDB_VERSION": ""}), EXIT_OK, "were written by")

    def test_mismatch_reddens_and_names_engine_mismatch_not_data(self) -> None:
        descriptor = write_package(self.datasets, "widgets")
        parquet = descriptor.parent / "widgets.parquet"
        original, patched = patch_created_by_to_disagree(parquet)
        original_version = re.search(r"(\d+\.\d+\.\d+)", original).group(1)
        patched_version = re.search(r"(\d+\.\d+\.\d+)", patched).group(1)

        # The pin is whatever actually wrote the fixture (original_version); the
        # footer now claims patched_version. This is the disagreement.
        result = run_check(self.datasets, env={"DUCKDB_VERSION": original_version})
        self.assertOutcome(
            result,
            EXIT_MISMATCH,
            "WRITER/READER ENGINE MISMATCH",
            "not a data disagreement",
            patched_version,
            original_version,
        )
        # The point of AC3b: this vocabulary, not "disagreement between descriptor
        # and data" — the phrase check_descriptors.py uses for an actual data defect.
        self.assertNotIn("disagreement between descriptor and data", result.stdout + result.stderr)

    def test_mismatch_still_reddens_when_the_pin_itself_differs_from_the_footer(self) -> None:
        """The same disagreement, produced the other way: an untouched footer held
        to a pin nothing on disk wrote. Exercises `reader_pin()` reading
        `DUCKDB_VERSION` rather than the byte-patch path above.
        """
        write_package(self.datasets, "widgets")
        self.assertOutcome(
            run_check(self.datasets, env={"DUCKDB_VERSION": "0.0.0"}),
            EXIT_MISMATCH,
            "WRITER/READER ENGINE MISMATCH",
            "0.0.0",
        )

    def test_footer_with_no_created_by_is_an_error_not_a_mismatch(self) -> None:
        descriptor = write_package(self.datasets, "widgets")
        parquet = descriptor.parent / "widgets.parquet"
        original = real_created_by(parquet)
        blanked = " " * len(original)
        raw = parquet.read_bytes()
        old_bytes = original.encode("utf-8")
        new_bytes = blanked.encode("utf-8")
        self.assertEqual(raw.count(old_bytes), 1)
        parquet.write_bytes(raw.replace(old_bytes, new_bytes, 1))

        self.assertOutcome(run_check(self.datasets), EXIT_ERROR, "does not name a DuckDB version")

    def test_only_narrows_to_the_dataset_it_names(self) -> None:
        write_package(self.datasets, "widgets")
        descriptor = write_package(self.datasets, "gadgets")
        original, _patched = patch_created_by_to_disagree(descriptor.parent / "gadgets.parquet")
        version = re.search(r"(\d+\.\d+\.\d+)", original).group(1)

        self.assertOutcome(run_check(self.datasets, "--only", "widgets", env={"DUCKDB_VERSION": version}), EXIT_OK)
        self.assertOutcome(
            run_check(self.datasets, "--only", "gadgets", env={"DUCKDB_VERSION": version}), EXIT_MISMATCH
        )

    def test_json_report_names_the_pin_and_the_mismatch(self) -> None:
        descriptor = write_package(self.datasets, "widgets")
        patch_created_by_to_disagree(descriptor.parent / "widgets.parquet")
        out = Path(self._tmp.name) / "report.json"

        result = run_check(self.datasets, "--json", str(out), env={"DUCKDB_VERSION": "0.0.0"})
        self.assertEqual(result.returncode, EXIT_MISMATCH)
        report = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(report["reader_pin"], "0.0.0")
        self.assertEqual(len(report["mismatches"]), 1)
        self.assertEqual(report["mismatches"][0]["reader_pin"], "0.0.0")

    def test_no_datasets_is_an_error(self) -> None:
        self.assertOutcome(run_check(self.datasets), EXIT_ERROR, "no datasets/*/datapackage.json")


if __name__ == "__main__":
    unittest.main(verbosity=2)
