#!/usr/bin/env python3
"""Run the joins a package declares, against the objects that package points at.

`schema.foreignKeys` says *every* value on the left exists on the right. When that
is false the honest declaration is not a foreign key, so this repo states a partial
relationship under the package-level `x-joins` key instead — and a claim nothing
executes is a claim nobody can trust. This is the executor.

It is deliberately incurious about which datasets it is run over. Everything it
needs comes out of the descriptors: which columns join, which resource they point
at, where that resource's bytes live, whether the comparison needs a rendering, and
which subset of rows the join applies to. Nothing about EDGAR, GLEIF, CIKs or fund
identifiers is written here. That is the point — if the join can only be run by a
program that already knows the answer, the descriptor did not describe it.

The declaration it reads, at the root of a `datapackage.json`:

    "x-joins": {
      "joins": [
        {
          "name":        short label for the referenced package
          "fields":      [column, ...]        columns on this side
          "reference":   {"resource": name, "fields": [column, ...]}
          "compareAs":   "text" | "exact"     how to compare the two sides
          "where":       {"field": col, "equals": value}      optional row filter
          "coverage":    {"rows": int, "matched": int}
        }
      ]
    }

`coverage` is the pinned part. `rows` is how many rows the join applies to after
`where`; `matched` is how many of those find a partner. Both are measured against
the exact objects named in `resources[].path`, so they move when those objects are
republished — the same property `bytes` and `hash` already have, and the reason the
workflow runs this on a schedule rather than only on merge.

Exit codes, distinct because a status alone cannot tell a refusal from a crash:

  0  every declared join ran and its declared coverage was exact
  1  a join ran and the data disagreed with what the descriptor claims
  2  the check could not run — a declaration this script does not implement, a
     reference that does not resolve, or bytes it could not read
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

EXIT_OK = 0
EXIT_VIOLATIONS = 1
EXIT_ERROR = 2

# Comparisons this script performs. `text` renders both sides with DuckDB's VARCHAR
# cast before comparing; `exact` compares the stored values and leaves the engine to
# reconcile the two sides, which it does by casting the text one. The two agree
# wherever every compared value has a numeric reading, and part company where one
# does not — there `exact` cannot run at all. A declaration naming anything else is
# EXIT_ERROR, never a pass: an unimplemented comparison must not read as a satisfied
# one.
SUPPORTED_COMPARISONS = {"text", "exact"}
SUPPORTED_WHERE_KEYS = {"field", "equals"}
REQUIRED_COVERAGE_KEYS = {"rows", "matched"}


class JoinError(Exception):
    """The join could not be run. Never a verdict about the data."""


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def quote_literal(value: Any) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def is_remote(raw: str) -> bool:
    return bool(re.match(r"^(https?|s3|gs|r2|az)://", raw, re.IGNORECASE))


def resolve_path(descriptor_path: Path, raw: str) -> str:
    if re.match(r"^[a-z][a-z0-9+.-]*://", raw, re.IGNORECASE):
        if not is_remote(raw):
            raise JoinError(f"unsupported resource scheme in path: {raw}")
        return raw
    return str((descriptor_path.parent / raw).resolve())


def load_descriptor(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise JoinError(f"{path}: cannot read descriptor: {exc}") from exc


def index_resources(paths: list[Path]) -> dict[str, list[tuple[Path, dict[str, Any]]]]:
    """resource name -> [(descriptor path, resource), ...] across every package."""
    index: dict[str, list[tuple[Path, dict[str, Any]]]] = {}
    for path in paths:
        descriptor = load_descriptor(path)
        for resource in descriptor.get("resources") or []:
            index.setdefault(resource.get("name", ""), []).append((path, resource))
    return index


def declared_fields(resource: dict[str, Any]) -> dict[str, str]:
    return {
        field["name"]: field.get("type", "any")
        for field in (resource.get("schema") or {}).get("fields") or []
    }


def own_resource(descriptor: dict[str, Any], columns: list[str], label: str) -> dict[str, Any]:
    """The resource in this package that declares every column the join names."""
    matches = [
        resource
        for resource in descriptor.get("resources") or []
        if set(columns) <= set(declared_fields(resource))
    ]
    if not matches:
        raise JoinError(f"join {label!r}: no resource in this package declares all of {columns}")
    if len(matches) > 1:
        names = ", ".join(sorted(str(resource.get("name")) for resource in matches))
        raise JoinError(f"join {label!r}: {columns} is declared by more than one resource ({names})")
    return matches[0]


def validate(join: dict[str, Any], label: str) -> tuple[list[str], list[str], str, dict[str, Any] | None, dict[str, int]]:
    local = join.get("fields") or []
    if isinstance(local, str):
        local = [local]
    reference = join.get("reference") or {}
    target = reference.get("fields") or []
    if isinstance(target, str):
        target = [target]
    if not local or not target:
        raise JoinError(f"join {label!r}: declares no fields on one side")
    if len(local) != len(target):
        raise JoinError(f"join {label!r}: maps {len(local)} field(s) onto {len(target)}")
    if not reference.get("resource"):
        raise JoinError(f"join {label!r}: names no referenced resource")

    compare = join.get("compareAs", "exact")
    if compare not in SUPPORTED_COMPARISONS:
        raise JoinError(
            f"join {label!r}: compareAs {compare!r} is not a comparison this script performs "
            f"({', '.join(sorted(SUPPORTED_COMPARISONS))}); refusing to report the join as running"
        )

    where = join.get("where")
    if where is not None:
        unsupported = set(where) - SUPPORTED_WHERE_KEYS
        if unsupported or "field" not in where or "equals" not in where:
            raise JoinError(
                f"join {label!r}: `where` must name exactly {sorted(SUPPORTED_WHERE_KEYS)}; "
                f"got {sorted(where)}"
            )

    coverage = join.get("coverage")
    if not isinstance(coverage, dict) or set(coverage) != REQUIRED_COVERAGE_KEYS:
        raise JoinError(
            f"join {label!r}: `coverage` must declare exactly {sorted(REQUIRED_COVERAGE_KEYS)}; "
            f"got {sorted(coverage) if isinstance(coverage, dict) else type(coverage).__name__}"
        )
    for key in sorted(REQUIRED_COVERAGE_KEYS):
        if not isinstance(coverage[key], int) or isinstance(coverage[key], bool) or coverage[key] < 0:
            raise JoinError(f"join {label!r}: coverage.{key} is not a row count: {coverage[key]!r}")
    if coverage["matched"] > coverage["rows"]:
        raise JoinError(
            f"join {label!r}: declares more matched rows ({coverage['matched']:,}) than rows "
            f"({coverage['rows']:,}), which no data can satisfy"
        )
    return local, target, compare, where, coverage


def rendered(column: str, compare: str, qualifier: str = "") -> str:
    ident = (f"{qualifier}." if qualifier else "") + quote_ident(column)
    return f"CAST({ident} AS VARCHAR)" if compare == "text" else ident


def run_join(con, descriptor_path: Path, descriptor: dict[str, Any], join: dict[str, Any], index, sample: int):
    label = str(join.get("name") or (join.get("reference") or {}).get("resource") or "<unnamed>")
    local, target, compare, where, coverage = validate(join, label)

    left_resource = own_resource(descriptor, local + ([where["field"]] if where else []), label)
    left_declared = declared_fields(left_resource)

    target_name = (join.get("reference") or {})["resource"]
    candidates = index.get(target_name, [])
    if not candidates:
        raise JoinError(f"join {label!r}: references resource {target_name!r}, which no package declares")
    if len(candidates) > 1:
        owners = ", ".join(sorted(str(path) for path, _ in candidates))
        raise JoinError(
            f"join {label!r}: references resource {target_name!r}, declared by more than one package ({owners})"
        )
    target_descriptor_path, right_resource = candidates[0]
    right_declared = declared_fields(right_resource)
    for column in target:
        if column not in right_declared:
            raise JoinError(
                f"join {label!r}: references {target_name}.{column}, which that package does not declare"
            )

    left_path = resolve_path(descriptor_path, left_resource.get("path") or "")
    right_path = resolve_path(target_descriptor_path, right_resource.get("path") or "")
    for path in (left_path, right_path):
        if is_remote(path):
            con.execute("INSTALL httpfs")
            con.execute("LOAD httpfs")
        elif not Path(path).exists():
            raise JoinError(f"join {label!r}: resource points at {path}, which does not exist")

    left_columns = sorted(set(local) | ({where["field"]} if where else set()))
    filter_sql = (
        f" WHERE {quote_ident(where['field'])} = {quote_literal(where['equals'])}" if where else ""
    )
    try:
        con.execute("DROP TABLE IF EXISTS _left")
        con.execute("DROP TABLE IF EXISTS _right")
        con.execute(
            f"CREATE TEMP TABLE _left AS SELECT {', '.join(quote_ident(c) for c in left_columns)} "  # noqa: S608
            f"FROM read_parquet({quote_literal(left_path)}){filter_sql}"
        )
        con.execute(
            f"CREATE TEMP TABLE _right AS SELECT DISTINCT "  # noqa: S608
            f"{', '.join(f'{rendered(c, compare)} AS k{i}' for i, c in enumerate(target))} "
            f"FROM read_parquet({quote_literal(right_path)})"
        )
    except Exception as exc:  # duckdb raises a family of errors; all mean "cannot read"
        raise JoinError(f"join {label!r}: could not read the joined bytes: {exc}") from exc

    predicate = " AND ".join(
        f"{rendered(column, compare, 'l')} = r.k{i}" for i, column in enumerate(local)
    )
    try:
        rows, matched = con.execute(
            f"SELECT count(*), count(r.k0) FROM _left l LEFT JOIN _right r ON {predicate}"  # noqa: S608
        ).fetchone()
    except Exception as exc:
        raise JoinError(f"join {label!r}: the join would not execute: {exc}") from exc

    pairs = ", ".join(
        f"{left_resource.get('name')}.{a} ({left_declared[a]}) = {target_name}.{b} ({right_declared[b]})"
        for a, b in zip(local, target)
    )
    print(f"  {label}: {pairs}")
    if where:
        print(f"    where {where['field']} = {where['equals']!r}; compared as {compare}")
    else:
        print(f"    every row; compared as {compare}")
    print(f"    declared  rows {coverage['rows']:,}  matched {coverage['matched']:,}")
    print(f"    measured  rows {rows:,}  matched {matched:,}")

    if sample and matched:
        example = con.execute(
            f"SELECT {', '.join('l.' + quote_ident(c) for c in local)} "  # noqa: S608
            f"FROM _left l JOIN _right r ON {predicate} LIMIT {int(sample)}"
        ).fetchall()
        rendering = ", ".join(str(value[0]) if len(value) == 1 else str(value) for value in example)
        print(f"    joined rows, e.g.: {rendering}")

    problems: list[str] = []
    if rows != coverage["rows"]:
        problems.append(
            f"{label}: descriptor declares {coverage['rows']:,} row(s) in scope; the published data has {rows:,}"
        )
    if matched != coverage["matched"]:
        problems.append(
            f"{label}: descriptor declares {coverage['matched']:,} matched row(s); the join finds {matched:,}"
        )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--datasets-dir", default="datasets", type=Path)
    parser.add_argument(
        "--package",
        action="append",
        metavar="SLUG",
        help="only run the joins declared by this dataset (repeatable); default is every dataset that declares any",
    )
    parser.add_argument("--sample", default=3, type=int, help="joined values to print per join (0 to suppress)")
    args = parser.parse_args(argv)

    try:
        import duckdb
    except ImportError:
        print("crosswalk join: the duckdb Python package is required", file=sys.stderr)
        return EXIT_ERROR

    descriptor_paths = sorted(args.datasets_dir.glob("*/datapackage.json"))
    if not descriptor_paths:
        print(f"crosswalk join: no datasets/*/datapackage.json under {args.datasets_dir}", file=sys.stderr)
        return EXIT_ERROR

    if args.package:
        wanted = set(args.package)
        selected = [path for path in descriptor_paths if path.parent.name in wanted]
        missing = wanted - {path.parent.name for path in selected}
        if missing:
            print(f"crosswalk join: no descriptor for dataset(s): {', '.join(sorted(missing))}", file=sys.stderr)
            return EXIT_ERROR
    else:
        selected = descriptor_paths

    con = duckdb.connect()
    problems: list[str] = []
    ran = 0

    try:
        index = index_resources(descriptor_paths)
        for descriptor_path in selected:
            descriptor = load_descriptor(descriptor_path)
            declaration = descriptor.get("x-joins")
            if declaration is None:
                continue
            joins = declaration.get("joins") if isinstance(declaration, dict) else None
            if not joins:
                raise JoinError(f"{descriptor_path}: `x-joins` declares no joins")
            print(f"{descriptor_path}")
            for join in joins:
                problems.extend(run_join(con, descriptor_path, descriptor, join, index, args.sample))
                ran += 1
    except JoinError as exc:
        print(f"crosswalk join: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if not ran:
        packages = ", ".join(path.parent.name for path in selected)
        print(f"crosswalk join: no dataset declares `x-joins` ({packages})", file=sys.stderr)
        return EXIT_ERROR

    if problems:
        print(f"\ncrosswalk join: {len(problems)} declared join fact(s) the data does not bear out\n")
        for problem in problems:
            print(f"  {problem}")
        print(f"\ncrosswalk join: FAILED — {len(problems)} disagreement(s) across {ran} join(s)")
        return EXIT_VIOLATIONS

    print(f"\ncrosswalk join: {ran} declared join(s) ran against the published bytes and covered what they claim")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
