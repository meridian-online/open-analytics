#!/usr/bin/env python3
# /// script
# requires-python = "==3.12.*"
# ///
# The interpreter this script runs under, for anything that runs it as a `uv` script.
# It needs nothing outside the standard library, so the manifest step that runs it
# (`op: uv@1`) declares no `deps:`.
"""Write the date a zip archive records for one of its members, as `{"date": "YYYY-MM-DD"}`.

Census's `sbsdf.zip` holds `econcord.txt`, and the date the zip's central directory
records for that member is the date of the Schedule B export file, which the
`census/schedule_b` release carries. arc's `archive_extract@1` does not set a member's
date on the file it writes out, so the date is read from the archive itself.

A zip stores each member's date as a local date and time with no time zone, so the
date is written as recorded, not converted.

The run fails, with nothing written, if the archive does not hold the member exactly
once.

    member_date.py --archive build/sbsdf.zip --member econcord.txt --out build/econcord_date.json
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
import zipfile
from pathlib import Path


def member_date(archive: Path, member: str) -> str:
    """The ISO date the archive's central directory records for `member`."""
    with zipfile.ZipFile(archive) as handle:
        entries = [info for info in handle.infolist() if info.filename == member]
    if len(entries) != 1:
        raise ValueError(f"{archive} holds {len(entries)} members named {member!r}; expected 1")
    year, month, day = entries[0].date_time[:3]
    return datetime.date(year, month, day).isoformat()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--member", required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        date = member_date(args.archive, args.member)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f"member date: {exc}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"date": date}) + "\n")
    print(f"member date: {args.member} in {args.archive} is dated {date}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
