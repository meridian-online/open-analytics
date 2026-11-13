#!/usr/bin/env python3
# /// script
# requires-python = "==3.12.*"
# dependencies = ["duckdb==1.5.5"]
# ///
"""Hold every hosted code list to the shared core columns.

A code list (HTS, Schedule B, and any later one) is a ladder of codes, each pointing
at its parent. An analyst who joins two of them, or walks one, relies on the same ten
columns meaning the same thing in both. This check reads a built Parquet and refuses a
file that breaks the contract. Each rule is one SQL query over the file, and each
finding is printed `code-list: <rule>: <detail>`.

    check_code_list.py --parquet PATH --levels L1,L2,...   check one built file
    check_code_list.py                                     check every datasets/*/datapackage.json
                                                           carrying `x-meridian-code-list`
    --json OUT                                             write the findings as JSON as well

`--resolve-root PREFIX=DIR` (repeatable) reads resource URLs under PREFIX from DIR, so a
test needs no network; any other URL is read over httpfs.

`--levels` lists the level names from the top of the ladder down; only the first may
have no parent, and a row's `depth` is its level's 0-based position in that list.

Exit 0 pass, 1 contract broken, 2 cannot run (including: no
descriptor declares a code list, so a sweep that examined nothing cannot pass).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import duckdb

EXIT_OK = 0
EXIT_BROKEN = 1
EXIT_ERROR = 2

CORE_COLUMNS: tuple[str, ...] = (
    "code",
    "code_display",
    "level",
    "depth",
    "parent_code",
    "description",
    "description_full",
    "release",
    "text_source",
    "corpus",
)

SAMPLE = 5


def _scalar(con, sql: str, params: list | None = None):
    return con.execute(sql, params or []).fetchone()[0]


def _sample(con, sql: str, params: list | None = None) -> str:
    rows = [str(r[0]) for r in con.execute(sql, params or []).fetchall()]
    return ", ".join(rows)


def check(con, relation: str, levels: list[str]) -> list[str]:
    """Findings for `relation` (a table or `read_parquet(...)` expression); empty if it passes."""
    findings: list[str] = []

    def add(rule: str, detail: str) -> None:
        findings.append(f"code-list: {rule}: {detail}")

    columns = [r[0] for r in con.execute(f"SELECT column_name FROM (DESCRIBE SELECT * FROM {relation})").fetchall()]
    if columns[: len(CORE_COLUMNS)] != list(CORE_COLUMNS):
        add(
            "core-columns",
            f"the first {len(CORE_COLUMNS)} columns must be {', '.join(CORE_COLUMNS)} in that order; "
            f"found {', '.join(columns)}",
        )
        return findings  # every later rule reads these columns by name

    n = _scalar(con, f"SELECT count(*) FROM {relation}")
    if n == 0:
        add("non-empty", "the file has no rows")
        return findings

    dup = _scalar(con, f"SELECT count(*) FROM (SELECT code FROM {relation} GROUP BY code HAVING count(*) > 1)")
    if dup:
        eg = _sample(con, f"SELECT code FROM {relation} GROUP BY code HAVING count(*) > 1 ORDER BY code LIMIT {SAMPLE}")
        add("code-unique", f"{dup} code(s) appear more than once, e.g. {eg}")

    nulls = _scalar(con, f"SELECT count(*) FROM {relation} WHERE code IS NULL")
    if nulls:
        add("code-not-null", f"{nulls} row(s) have a null code")

    missing = _scalar(
        con,
        f"SELECT count(*) FROM {relation} c WHERE c.parent_code IS NOT NULL "
        f"AND NOT EXISTS (SELECT 1 FROM {relation} p WHERE p.code = c.parent_code)",
    )
    if missing:
        eg = _sample(
            con,
            f"SELECT c.code FROM {relation} c WHERE c.parent_code IS NOT NULL "
            f"AND NOT EXISTS (SELECT 1 FROM {relation} p WHERE p.code = c.parent_code) ORDER BY 1 LIMIT {SAMPLE}",
        )
        add("parent-present", f"{missing} row(s) name a parent_code that is not in the file, e.g. {eg}")

    top = levels[0]
    orphans = _scalar(
        con, f"SELECT count(*) FROM {relation} WHERE parent_code IS NULL AND level IS DISTINCT FROM ?", [top]
    )
    if orphans:
        eg = _sample(
            con,
            f"SELECT code FROM {relation} WHERE parent_code IS NULL AND level IS DISTINCT FROM ? ORDER BY 1 LIMIT {SAMPLE}",
            [top],
        )
        add("parent-null-below-top", f"{orphans} row(s) below the top level `{top}` have no parent_code, e.g. {eg}")

    steps = _scalar(
        con,
        f"SELECT count(*) FROM {relation} c JOIN {relation} p ON p.code = c.parent_code "
        f"WHERE c.depth IS DISTINCT FROM p.depth + 1",
    )
    if steps:
        eg = _sample(
            con,
            f"SELECT c.code FROM {relation} c JOIN {relation} p ON p.code = c.parent_code "
            f"WHERE c.depth IS DISTINCT FROM p.depth + 1 ORDER BY 1 LIMIT {SAMPLE}",
        )
        add("depth-step", f"{steps} row(s) have a depth that is not their parent's depth + 1, e.g. {eg}")

    pairs: list = []
    for i, lv in enumerate(levels):
        pairs += [lv, i]
    values = ", ".join("(?, ?)" for _ in levels)
    bad_depth = (
        f"FROM {relation} r JOIN (VALUES {values}) AS l(name, pos) ON l.name = r.level "
        f"WHERE r.depth IS DISTINCT FROM l.pos"
    )
    wrong = _scalar(con, f"SELECT count(*) {bad_depth}", pairs)
    if wrong:
        eg = _sample(con, f"SELECT r.code {bad_depth} ORDER BY 1 LIMIT {SAMPLE}", pairs)
        add("level-depth", f"{wrong} row(s) have a depth that is not their level's 0-based position in "
            f"{', '.join(levels)}, e.g. {eg}")

    marks = ", ".join("?" for _ in levels)
    stray = _scalar(con, f"SELECT count(*) FROM {relation} WHERE level IS NULL OR level NOT IN ({marks})", levels)
    if stray:
        eg = _sample(
            con,
            f"SELECT DISTINCT coalesce(level, '(null)') FROM {relation} "
            f"WHERE level IS NULL OR level NOT IN ({marks}) ORDER BY 1 LIMIT {SAMPLE}",
            levels,
        )
        add("level-set", f"{stray} row(s) carry a level outside {', '.join(levels)}: {eg}")

    nr = _scalar(con, f"SELECT count(*) FROM {relation} WHERE release IS NULL")
    if nr:
        add("release-not-null", f"{nr} row(s) have a null release")

    releases = _scalar(con, f"SELECT count(DISTINCT release) FROM {relation}")
    if releases > 1:
        eg = _sample(con, f"SELECT DISTINCT release FROM {relation} WHERE release IS NOT NULL ORDER BY 1 LIMIT {SAMPLE}")
        add("release-single", f"the file mixes {releases} releases: {eg}")

    nd = _scalar(con, f"SELECT count(*) FROM {relation} WHERE description IS NULL")
    if nd:
        add("description-not-null", f"{nd} row(s) have a null description")

    return findings


def is_url(path: str) -> bool:
    return path.startswith(("http://", "https://"))


def locate(path: str, base: Path, roots: list[tuple[str, Path]]) -> str:
    """Where to read a declared resource path from: a mapped local file, the URL, or a file beside the descriptor."""
    if is_url(path):
        for prefix, directory in roots:
            if path.startswith(prefix):
                return str(directory / path[len(prefix):].lstrip("/"))
        return path
    return str(base / path)


def load(con, location: str) -> str:
    """Read `location` into a table and return its name. The path is bound, never spliced into SQL."""
    if is_url(location):
        con.execute("INSTALL httpfs")
        con.execute("LOAD httpfs")
    elif not Path(location).is_file():
        raise RuntimeError(f"{location}: not a readable file")
    con.execute("CREATE TABLE code_list AS SELECT * FROM read_parquet(?)", [location])
    return "code_list"


def targets(datasets_dir: Path) -> list[tuple[str, str, Path, list[str]]]:
    found = []
    for descriptor in sorted(datasets_dir.glob("*/datapackage.json")):
        try:
            doc = json.loads(descriptor.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"{descriptor}: cannot be read as JSON: {exc}") from exc
        block = doc.get("x-meridian-code-list") if isinstance(doc, dict) else None
        if block is None:
            continue
        levels = block.get("levels") if isinstance(block, dict) else None
        if not isinstance(levels, list) or not levels or not all(isinstance(x, str) for x in levels):
            raise RuntimeError(f"{descriptor}: x-meridian-code-list.levels must be a non-empty list of names")
        resources = doc.get("resources")
        if not isinstance(resources, list) or not resources or not isinstance(resources[0], dict) \
                or not isinstance(resources[0].get("path"), str):
            raise RuntimeError(f"{descriptor}: resources[0].path is not declared")
        found.append((descriptor.parent.name, resources[0]["path"], descriptor.parent, levels))
    if not found:
        raise RuntimeError(f"no code-list descriptors under {datasets_dir}")
    return found


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--parquet", help="a local path or an http(s) URL")
    ap.add_argument("--levels")
    ap.add_argument("--json", type=Path, dest="json_out")
    ap.add_argument(
        "--resolve-root", action="append", default=[], metavar="PREFIX=DIR",
        help="read resource URLs starting with PREFIX from DIR instead (repeatable)",
    )
    ap.add_argument("--datasets-dir", type=Path, default=Path(__file__).resolve().parent.parent / "datasets")
    args = ap.parse_args(argv)

    try:
        if args.parquet is not None:
            if not args.levels:
                raise RuntimeError("--parquet needs --levels")
            levels = [x.strip() for x in args.levels.split(",") if x.strip()]
            if not levels:
                raise RuntimeError("--levels names no level")
            work = [(args.parquet.rsplit("/", 1)[-1] or args.parquet, args.parquet, Path("."), levels)]
        else:
            work = targets(args.datasets_dir)
        roots = []
        for spec in args.resolve_root:
            prefix, sep, directory = spec.partition("=")
            if not sep or not prefix or not directory:
                raise RuntimeError(f"--resolve-root {spec!r} is not PREFIX=DIR")
            # A prefix names a directory, so it matches whole path segments: without the
            # slash, .../usitc would also capture .../usitc-archive/...
            roots.append((prefix if prefix.endswith("/") else prefix + "/", Path(directory)))

        report = {}
        for name, path, base, levels in work:
            location = locate(path, base, roots)
            con = duckdb.connect()
            try:
                report[name] = check(con, load(con, location), levels)
            except duckdb.Error as exc:
                raise RuntimeError(f"{location}: cannot be read: {exc}") from exc
            finally:
                con.close()
    except RuntimeError as exc:
        print(f"code-list: cannot run: {exc}")
        return EXIT_ERROR

    broken = False
    for name, findings in report.items():
        for line in findings:
            print(f"{name}: {line}" if len(report) > 1 else line)
        if not findings:
            print(f"code-list: {name}: ok")
        broken = broken or bool(findings)
    if args.json_out:
        payload = {
            "findings": [f for found in report.values() for f in found],
            "files": report,
        }
        args.json_out.write_text(json.dumps(payload, indent=2) + "\n")
    return EXIT_BROKEN if broken else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
