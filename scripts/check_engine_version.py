#!/usr/bin/env python3
"""Hold the engine that wrote each published Parquet to the engine this check runs as.

Every `datasets/<slug>/datapackage.json` resource is a Parquet object. Parquet's
footer carries a `created_by` field the writer sets itself — for DuckDB it reads
`DuckDB version vX.Y.Z (build ...)` — so the file is honest about what wrote it even
when nothing else is. This script reads that field back out of every published
resource with `parquet_file_metadata()` (a footer read, not a download) and holds it
to `DUCKDB_VERSION`, the version this very check just had installed.

Why this exists: the descriptor check in `check_descriptors.py` proves a descriptor
agrees with the bytes it describes, on the assumption that reading those bytes back
means the same thing to the writer and the reader. A version skew between the two
breaks that assumption silently — a run pinned to one DuckDB version can build
different bytes, at the same declared size, than a run pinned to another, so a real
engine skew and a real data defect can look identical to a check that only compares
sizes and hashes. One shipped undetected: a published object's own footer recorded a
different DuckDB build than the pipeline that was supposed to have produced it, and
the disagreement it caused was read and resolved as a data problem, because a
size/hash comparison has no vocabulary for "the writer was not the version you
expected." This script gives it one.

A mismatch here is never a claim about the data. The rows, encodings and row groups
this script touches are exactly zero — it reads one string out of the footer and
compares it to one string this job installed. Two different engines can write
identical rows; that is not what this checks. It checks whether the engine that
wrote the file this run is judging is the engine this run is pinned to, which is the
one thing a byte-for-byte comparison cannot see either way.

Exit codes, matching check_descriptors.py's convention:

  0  every checked resource's footer names the same DuckDB version this check is
     pinned to
  1  at least one writer/reader engine mismatch — named with both versions
  2  the check could not run: unreadable resource, missing/unparseable created_by,
     or no datasets found

`--datasets-dir` and `--only` mirror check_descriptors.py so the two can be pointed
at the same fixture tree in a test; this script otherwise shares no code and no
state with it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

EXIT_OK = 0
EXIT_MISMATCH = 1
EXIT_ERROR = 2

# `created_by` for a DuckDB-written file reads e.g. "DuckDB version v1.5.5 (build
# d8cdaa33fd)". Only the semantic version is compared — the build hash identifies a
# specific compile of that version and is not part of the pin this check reads.
CREATED_BY_VERSION = re.compile(r"DuckDB version v?(\d+\.\d+\.\d+)", re.IGNORECASE)


class CheckError(Exception):
    """The check could not be performed. Never an engine verdict."""


@dataclass
class Mismatch:
    package: str
    resource: str
    path: str
    writer_version: str
    reader_pin: str

    def line(self) -> str:
        return (
            f"{self.package} :: {self.resource}\n"
            f"    engine.version: WRITER/READER ENGINE MISMATCH (not a data disagreement) — "
            f"{self.path} was written by DuckDB {self.writer_version}; this check is pinned to "
            f"DuckDB {self.reader_pin}. The bytes may be entirely correct; the engine that wrote "
            f"them is not the engine judging them."
        )


def resolve_path(descriptor_path: Path, raw: str) -> tuple[str, bool]:
    """Return (path-for-duckdb, is_remote). Relative paths resolve next to the descriptor."""
    if re.match(r"^[a-z][a-z0-9+.-]*://", raw, re.IGNORECASE):
        if raw.lower().startswith(("http://", "https://", "s3://", "gs://", "r2://", "az://")):
            return raw, True
        raise CheckError(f"unsupported resource scheme in path: {raw}")
    return str((descriptor_path.parent / raw).resolve()), False


def discover(datasets_dir: Path, only: Iterable[str] | None) -> list[Path]:
    paths = sorted(datasets_dir.glob("*/datapackage.json"))
    if only:
        wanted = set(only)
        paths = [path for path in paths if path.parent.name in wanted]
        missing = wanted - {path.parent.name for path in paths}
        if missing:
            raise CheckError(f"no descriptor for dataset(s): {', '.join(sorted(missing))}")
    if not paths:
        raise CheckError(f"no datasets/*/datapackage.json under {datasets_dir}")
    return paths


def load_descriptor(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise CheckError(f"{path}: cannot read descriptor: {exc}") from exc


def footer_created_by(con, path: str) -> str:
    """The writer's own `created_by` string, straight out of the Parquet footer."""
    try:
        rows = con.execute("SELECT created_by FROM parquet_file_metadata(?)", [path]).fetchall()
    except Exception as exc:  # duckdb raises a family of errors; all mean "cannot read"
        raise CheckError(f"cannot read the footer of {path}: {exc}") from exc
    if not rows or not rows[0][0]:
        raise CheckError(f"{path}: footer carries no created_by, so no writer engine is on record")
    return rows[0][0]


def parse_writer_version(created_by: str, path: str) -> str:
    match = CREATED_BY_VERSION.search(created_by)
    if match is None:
        raise CheckError(
            f"{path}: footer created_by is {created_by!r}, which does not name a DuckDB version "
            "this check knows how to parse"
        )
    return match.group(1)


def reader_pin() -> str:
    """The DuckDB version this check itself is pinned to.

    `DUCKDB_VERSION` is what the workflow installs immediately before this script
    runs, so it is authoritative when set. A local run with no such env falls back to
    the version actually imported — the two are the same thing by construction in CI,
    since the install step pins the exact version this reads.
    """
    pinned = os.environ.get("DUCKDB_VERSION")
    if pinned:
        return pinned
    import duckdb

    return duckdb.__version__


def check_resources(con, descriptor_paths: list[Path], pin: str) -> tuple[list[Mismatch], int]:
    mismatches: list[Mismatch] = []
    checked = 0
    for descriptor_path in descriptor_paths:
        package = str(descriptor_path)
        descriptor = load_descriptor(descriptor_path)
        resources = descriptor.get("resources") or []
        if not resources:
            raise CheckError(f"{package}: declares no resources")
        for resource in resources:
            raw_path = resource.get("path")
            if not raw_path:
                raise CheckError(f"{package}: resource declares no path")
            path, is_remote = resolve_path(descriptor_path, raw_path)
            if is_remote:
                con.execute("INSTALL httpfs")
                con.execute("LOAD httpfs")
            elif not Path(path).exists():
                raise CheckError(f"{package}: resource points at {path}, which does not exist")
            created_by = footer_created_by(con, path)
            written = parse_writer_version(created_by, path)
            checked += 1
            if written != pin:
                mismatches.append(Mismatch(package, resource.get("name", ""), path, written, pin))
    # A RUN THAT READ NOTHING IS NOT A RUN THAT AGREED. Every declared resource above
    # is either read or raises, so reaching here with `checked` at zero means the loop
    # was entered and every path through it declined — which is indistinguishable, from
    # outside, from four footers that all matched. The live check reads only remote
    # objects, so anything that quietly skips a remote takes the whole gate to exit 0
    # having verified nothing.
    if checked == 0:
        raise CheckError(
            f"read 0 resource footers across {len(descriptor_paths)} descriptor(s). "
            "The check verified nothing, which is not the same as everything agreeing."
        )
    return mismatches, checked


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--datasets-dir", default="datasets", type=Path)
    parser.add_argument("--only", action="append", metavar="SLUG", help="restrict to one dataset slug (repeatable)")
    parser.add_argument("--json", metavar="FILE", type=Path, help="also write the findings as JSON")
    args = parser.parse_args(argv)

    try:
        import duckdb
    except ImportError:
        print("engine version check: the duckdb Python package is required", file=sys.stderr)
        return EXIT_ERROR

    try:
        descriptor_paths = discover(args.datasets_dir, args.only)
    except CheckError as exc:
        print(f"engine version check: {exc}", file=sys.stderr)
        return EXIT_ERROR

    pin = reader_pin()
    con = duckdb.connect()

    try:
        mismatches, checked = check_resources(con, descriptor_paths, pin)
    except CheckError as exc:
        print(f"engine version check: {exc}", file=sys.stderr)
        return EXIT_ERROR

    checked_names = ", ".join(path.parent.name for path in descriptor_paths)
    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "checked": checked_names,
                    "resources": checked,
                    "reader_pin": pin,
                    "mismatches": [asdict(m) for m in mismatches],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    if not mismatches:
        print(
            f"engine version check: {checked} resource(s) across {len(descriptor_paths)} descriptor(s) "
            f"({checked_names}) were written by the DuckDB this check is pinned to ({pin})"
        )
        return EXIT_OK

    print(
        f"engine version check: {len(mismatches)} WRITER/READER ENGINE MISMATCH(ES) — not a data "
        "disagreement\n"
    )
    for mismatch in mismatches:
        print(mismatch.line())
    print(
        f"\nengine version check: FAILED — {len(mismatches)} engine mismatch(es) across {checked} "
        f"resource(s) ({checked_names}); this is a version skew between the engine that wrote the "
        "bytes and the engine pinned to check them, not a claim about the data itself"
    )
    return EXIT_MISMATCH


if __name__ == "__main__":
    sys.exit(main())
