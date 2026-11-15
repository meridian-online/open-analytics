#!/usr/bin/env python3
"""Check a rebuilt table against the open lake before `ducklake_write` publishes it.

`ducklake_write@1` refuses to publish a table whose shape changed: a dropped column, a
changed type, a column added before the last existing one, or a reorder. New columns at
the end are fine. This script applies the same rule (arc's `schema_difference`) to the
live table and the Parquet about to replace it, and also holds the row count to a
limit, so a refusal or a surprising size is caught before the live run.

    check_lake_shape.py --catalog <ducklake url or path> --table <schema.name> \
        --parquet <path> [--max-row-change 0.10]

Exit codes:

  0  `shape ok: <table> <n> columns, rows <live> -> <new>`, or `new table` when the
     catalogue has no such table
  1  the shape differs or the row count moved past the limit; each difference is named
  2  the check could not run
"""

from __future__ import annotations

import argparse
import sys

import duckdb


def shape_difference(
    existing: list[tuple[str, str]], incoming: list[tuple[str, str]]
) -> list[str]:
    """How `incoming` columns differ from `existing`, in words; empty when it only appends."""
    found: list[str] = []
    for name, ty in existing:
        match = [t for n, t in incoming if n == name]
        if not match:
            found.append(f"column {name} was dropped")
        elif match[0] != ty:
            found.append(f"column {name} was {ty}, is now {match[0]}")
    prefix = len(incoming) >= len(existing) and incoming[: len(existing)] == existing
    if not found and not prefix:
        names = {n for n, _ in existing}
        for name, _ in incoming[: len(existing)]:
            if name not in names:
                found.append(f"column {name} was added before the last existing column")
        if not found:
            found.append("the columns were reordered")
    return found


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--table", required=True, help="schema.name")
    ap.add_argument("--parquet", required=True)
    ap.add_argument("--max-row-change", type=float, default=0.10)
    args = ap.parse_args()
    schema, _, name = args.table.partition(".")
    if not name:
        print("--table must be schema.name", file=sys.stderr)
        return 2

    con = duckdb.connect()
    try:
        con.execute("INSTALL ducklake; LOAD ducklake")
        if args.catalog.startswith(("http://", "https://")):
            con.execute("INSTALL httpfs; LOAD httpfs")
        con.execute(f"ATTACH 'ducklake:{args.catalog}' AS lake (READ_ONLY)")
        existing = [
            (c, t)
            for c, t in con.execute(
                "SELECT column_name, data_type FROM duckdb_columns() "
                "WHERE database_name = 'lake' AND schema_name = ? AND table_name = ? "
                "ORDER BY column_index",
                [schema, name],
            ).fetchall()
        ]
        incoming = [
            (r[0], r[1])
            for r in con.execute(
                "DESCRIBE SELECT * FROM read_parquet(?)", [args.parquet]
            ).fetchall()
        ]
        new_rows = con.execute(
            "SELECT count(*) FROM read_parquet(?)", [args.parquet]
        ).fetchone()[0]
        if not existing:
            print(f"new table: {args.table} {len(incoming)} columns, rows {new_rows}")
            return 0
        live_rows = con.execute(
            f'SELECT count(*) FROM lake."{schema}"."{name}"'
        ).fetchone()[0]
    except duckdb.Error as e:
        print(f"could not run the check: {e}", file=sys.stderr)
        return 2

    problems = shape_difference(existing, incoming)
    if live_rows and abs(new_rows - live_rows) / live_rows > args.max_row_change:
        problems.append(
            f"rows {live_rows} -> {new_rows} moves more than {args.max_row_change:.0%}"
        )
    if problems:
        for p in problems:
            print(f"{args.table}: {p}")
        return 1
    print(
        f"shape ok: {args.table} {len(incoming)} columns, rows {live_rows} -> {new_rows}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
