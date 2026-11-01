#!/usr/bin/env python3
"""Self-test for member_date.py: it reads a member's date from the zip's own directory.

Each case builds a scratch zip whose members carry known dates, runs the shipped
script's `main` on it, and reads the file it writes. No case touches the network.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
import warnings
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import member_date  # noqa: E402


def make_zip(path: Path, members: list[tuple[str, tuple[int, int, int, int, int, int]]]) -> None:
    with warnings.catch_warnings():
        # zipfile warns on a duplicate name; one case writes one on purpose.
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(path, "w") as handle:
            for name, stamp in members:
                handle.writestr(zipfile.ZipInfo(name, date_time=stamp), b"x")


def run(*args: str) -> int:
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        return member_date.main(list(args))


class MemberDateTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.archive = self.dir / "sbsdf.zip"
        self.out = self.dir / "build" / "econcord_date.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_the_named_member_dates_the_file_not_its_neighbours(self) -> None:
        make_zip(
            self.archive,
            [
                ("iconcord.txt", (2026, 10, 22, 0, 54, 0)),
                ("econcord.txt", (2026, 7, 28, 2, 38, 0)),
                ("readme.txt", (2015, 12, 30, 6, 38, 0)),
            ],
        )
        self.assertEqual(run("--archive", str(self.archive), "--member", "econcord.txt", "--out", str(self.out)), 0)
        self.assertEqual(json.loads(self.out.read_text()), {"date": "2026-07-28"})

    def test_the_date_is_written_as_recorded_late_at_night(self) -> None:
        make_zip(self.archive, [("econcord.txt", (2026, 12, 31, 23, 59, 58))])
        self.assertEqual(run("--archive", str(self.archive), "--member", "econcord.txt", "--out", str(self.out)), 0)
        self.assertEqual(json.loads(self.out.read_text()), {"date": "2026-12-31"})

    def test_a_missing_member_fails_and_writes_nothing(self) -> None:
        make_zip(self.archive, [("iconcord.txt", (2026, 10, 22, 0, 54, 0))])
        self.assertEqual(run("--archive", str(self.archive), "--member", "econcord.txt", "--out", str(self.out)), 1)
        self.assertFalse(self.out.exists())

    def test_a_member_named_twice_fails(self) -> None:
        make_zip(
            self.archive,
            [("econcord.txt", (2026, 7, 28, 0, 0, 0)), ("econcord.txt", (2026, 8, 1, 0, 0, 0))],
        )
        self.assertEqual(run("--archive", str(self.archive), "--member", "econcord.txt", "--out", str(self.out)), 1)
        self.assertFalse(self.out.exists())

    def test_a_file_that_is_not_a_zip_fails(self) -> None:
        self.archive.write_bytes(b"<html>not found</html>")
        self.assertEqual(run("--archive", str(self.archive), "--member", "econcord.txt", "--out", str(self.out)), 1)
        self.assertFalse(self.out.exists())

    def test_a_member_in_a_folder_is_not_the_top_level_one(self) -> None:
        make_zip(self.archive, [("old/econcord.txt", (2025, 7, 1, 0, 0, 0))])
        self.assertEqual(run("--archive", str(self.archive), "--member", "econcord.txt", "--out", str(self.out)), 1)


if __name__ == "__main__":
    unittest.main()
