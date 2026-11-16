#!/usr/bin/env python3
"""Self-test for member_date.py: it reads a member's date from the zip's own directory.

Each case builds a scratch zip whose members carry known dates, runs the shipped
script's `main` on it, and reads the file it writes. No case touches the network.
"""

from __future__ import annotations

import calendar
import io
import json
import os
import struct
import sys
import tempfile
import time
import unittest
import warnings
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import member_date  # noqa: E402


def make_zip(
    path: Path,
    members: list[tuple[str, tuple[int, int, int, int, int, int]]],
    utc_mtime: int | None = None,
) -> None:
    """A zip of `members`, each with its DOS date and time; with `utc_mtime`, each also
    carries an extended-timestamp field (0x5455) holding that UTC modification time, as
    Census's sbsdf.zip does."""
    with warnings.catch_warnings():
        # zipfile warns on a duplicate name; one case writes one on purpose.
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(path, "w") as handle:
            for name, stamp in members:
                info = zipfile.ZipInfo(name, date_time=stamp)
                if utc_mtime is not None:
                    info.extra = struct.pack("<HHBI", 0x5455, 5, 1, utc_mtime)
                handle.writestr(info, b"x")


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

    def test_the_dos_date_is_read_not_the_utc_timestamp_in_local_time(self) -> None:
        # Census's econcord.txt: DOS time 2026-07-27 12:38, extended timestamp 16:38 UTC.
        # Shown in Sydney's local time the extended timestamp is 2026-07-28 02:38, which is
        # what `unzip -l` prints there; the date the zip records is the 27th.
        utc = calendar.timegm((2026, 7, 27, 16, 38, 6, 0, 0, 0))
        make_zip(self.archive, [("econcord.txt", (2026, 7, 27, 12, 38, 6))], utc_mtime=utc)
        with zipfile.ZipFile(self.archive) as handle:
            self.assertEqual(handle.getinfo("econcord.txt").extra[:2], b"\x55\x54")
        saved = os.environ.get("TZ")
        os.environ["TZ"] = "Australia/Sydney"
        time.tzset()
        try:
            self.assertEqual(time.localtime(utc)[:3], (2026, 7, 28))
            self.assertEqual(
                run("--archive", str(self.archive), "--member", "econcord.txt", "--out", str(self.out)), 0
            )
        finally:
            if saved is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = saved
            time.tzset()
        self.assertEqual(json.loads(self.out.read_text()), {"date": "2026-07-27"})

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
        self.assertFalse(self.out.exists())


if __name__ == "__main__":
    unittest.main()
