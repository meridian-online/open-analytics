#!/usr/bin/env python3
"""Validate every published Data Package descriptor against the Parquet it describes.

Each `datasets/<slug>/datapackage.json` declares, per field, a Frictionless `type`
and a set of `constraints`. Nothing read those declarations back against the bytes
at `resources[].path`, so a descriptor could contradict its own data indefinitely.
This is that reader.

What it asserts, per resource:

  1. field set     — every declared field exists in the Parquet, and every Parquet
                     column is declared.
  2. type          — the declared Frictionless `type` is coercible from the Parquet
                     physical type (table in COERCIBLE below).
  3. constraints   — `pattern`, `minLength`, `maxLength`, `enum` and `required`,
                     evaluated over every row in one pass. A `pattern` that will not
                     compile is reported as its own disagreement and excludes only
                     itself from that pass — the field's other constraints are still
                     evaluated over the same rows.
  4. primaryKey    — every column named in `schema.primaryKey` exists, holds no NULL,
                     and identifies at most one row. This is the declaration a
                     consumer joins on, so a key the data does not honour hands them
                     duplicated rows or silently dropped ones.
  5. bytes         — the declared `resources[].bytes` matches the size the server
                     (or the filesystem) reports.
  6. self-description
                   — when the Parquet carries its own descriptor in its footer (see
                     `stamp_descriptor.py`), that copy is read back out of the file and
                     held to the same rules: it must agree with this repository's copy
                     apart from the two keys a file cannot state about itself, and its
                     field set, types and primary key must agree with the columns and
                     rows actually present.
                     An object that carries none is not a violation unless
                     `--require-self-description` is passed — nothing publishes a
                     stamped object yet, and a check that demanded one would refuse
                     every file currently served.

And per package:

  7. foreignKeys   — each declared foreign key resolves to a real resource+field,
                     in this package or a sibling one, of compatible declared type.
                     Resolved against every package the tree contains, `--only` or
                     not — `--only` narrows which packages' own keys are reported on,
                     never which packages a reference can land on.

And once, over the catalogue in README.md:

  8. catalogue     — every row of the table that states a row count is held to the
                     number of rows in the object it links to, counted from that
                     object. A figure written in full must match exactly; a figure
                     written to a stated precision (`3.36M`) must be what the count
                     rounds to at that precision. A published dataset the catalogue
                     omits is a disagreement too, so a row cannot be deleted to make
                     the check pass. `--write-catalogue` corrects the figures from the
                     measurement rather than by hand. See `render_count` for the rule.

Exit codes are distinct on purpose, because a status alone cannot tell a refusal
apart from a crash:

  0  every descriptor agrees with its data
  1  at least one disagreement, each named on stdout with field and rule
  2  the check could not run — unreadable resource, malformed descriptor, or a
     constraint keyword this script does not implement

Reading a remote resource needs DuckDB's httpfs extension, which is installed on
demand; a local path needs no network at all.

Why rule 5 exists at all: once an object carries its own description, that copy is
what a consumer reads — they hold a URL, not this repository. A check that kept
reading only the file in `datasets/` would go on passing while the surface people
actually see drifted away from it, which is the same failure that let 14
descriptor/data disagreements ship unnoticed. So the two copies are compared to each
other as well as to the bytes.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Iterable

from stamp_descriptor import DESCRIPTOR_KEY, SELF_REFERENTIAL, embedded_form, read_self_description

EXIT_OK = 0
EXIT_VIOLATIONS = 1
EXIT_ERROR = 2

# Which Parquet physical types a declared Frictionless type may be read from
# without parsing or reinterpreting the stored value. The direction matters: a
# descriptor saying `string` over a BIGINT column is as wrong as one saying
# `integer` over a VARCHAR column, and both have shipped.
INTEGER_PHYSICAL = {
    "TINYINT",
    "SMALLINT",
    "INTEGER",
    "BIGINT",
    "HUGEINT",
    "UTINYINT",
    "USMALLINT",
    "UINTEGER",
    "UBIGINT",
    "UHUGEINT",
}
COERCIBLE: dict[str, set[str]] = {
    "string": {"VARCHAR"},
    "integer": set(INTEGER_PHYSICAL),
    "number": INTEGER_PHYSICAL | {"FLOAT", "DOUBLE", "DECIMAL"},
    "boolean": {"BOOLEAN"},
    "date": {"DATE"},
    "time": {"TIME"},
    "datetime": {"TIMESTAMP", "TIMESTAMP WITH TIME ZONE", "TIMESTAMP_NS", "TIMESTAMP_MS", "TIMESTAMP_S"},
    "year": set(INTEGER_PHYSICAL),
    "duration": {"INTERVAL"},
    "object": {"STRUCT", "MAP", "JSON"},
    "array": {"LIST"},
    "geojson": {"VARCHAR", "STRUCT", "JSON"},
    "any": set(),  # `any` accepts every physical type; handled as a special case.
}

# Constraint keywords this script evaluates. Anything else is an EXIT_ERROR rather
# than a silent pass — an unimplemented constraint must not read as a conformant one.
SUPPORTED_CONSTRAINTS = {"pattern", "minLength", "maxLength", "enum", "required"}

# Declared types that may sit on either end of a foreign key together.
FK_COMPATIBLE_GROUPS = [{"integer", "number", "year"}]


@dataclass
class Violation:
    package: str
    resource: str
    field: str
    rule: str
    detail: str

    def line(self) -> str:
        where = f"{self.package} :: {self.resource}"
        if self.field:
            where += f".{self.field}"
        return f"{where}\n    {self.rule}: {self.detail}"


class CheckError(Exception):
    """The check could not be performed. Never a data verdict."""


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def normalise_physical(duckdb_type: str) -> str:
    """DECIMAL(18,3) -> DECIMAL, STRUCT(...) -> STRUCT, VARCHAR[] -> LIST."""
    t = duckdb_type.strip().upper()
    if t.endswith("[]"):
        return "LIST"
    base = t.split("(", 1)[0].strip()
    return base


def resolve_path(descriptor_path: Path, raw: str) -> tuple[str, bool]:
    """Return (path-for-duckdb, is_remote). Relative paths resolve next to the descriptor."""
    if re.match(r"^[a-z][a-z0-9+.-]*://", raw, re.IGNORECASE):
        if raw.lower().startswith(("http://", "https://", "s3://", "gs://", "r2://", "az://")):
            return raw, True
        raise CheckError(f"unsupported resource scheme in path: {raw}")
    return str((descriptor_path.parent / raw).resolve()), False


USER_AGENT = "meridian-open-analytics descriptor-check (+https://github.com/meridian-online/open-analytics)"


def remote_size(url: str) -> int | None:
    """Published size of a URL, or None when the server will not report one.

    Asks for a single byte and reads the total out of `Content-Range`, which costs
    one byte rather than the file. Some edges refuse `HEAD`, so that is the fallback
    rather than the first move.
    """
    request = urllib.request.Request(url, headers={"Range": "bytes=0-0", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            content_range = response.headers.get("Content-Range")
            length = response.headers.get("Content-Length")
            status = response.status
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        raise CheckError(f"GET {url} failed: {exc}") from exc

    if status == 206 and content_range:
        total = content_range.rsplit("/", 1)[-1].strip()
        if total.isdigit():
            return int(total)
    if status == 200 and length is not None and length.isdigit():
        return int(length)  # the edge ignored the range and reported the whole file
    return None


def load_descriptor(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise CheckError(f"{path}: cannot read descriptor: {exc}") from exc


def physical_types(con, path: str) -> dict[str, str]:
    try:
        rows = con.execute(
            "SELECT column_name, column_type FROM (DESCRIBE SELECT * FROM read_parquet(?))",
            [path],
        ).fetchall()
    except Exception as exc:  # duckdb raises a family of errors; all mean "cannot read"
        raise CheckError(f"cannot read Parquet at {path}: {exc}") from exc
    if not rows:
        raise CheckError(f"Parquet at {path} declares no columns")
    return {name: normalise_physical(kind) for name, kind in rows}


def check_pattern_compiles(con, pattern: str) -> str | None:
    try:
        con.execute("SELECT regexp_full_match('', ?)", [pattern]).fetchone()
    except Exception as exc:
        return str(exc).strip().splitlines()[0]
    return None


def constraint_probes(
    field: dict[str, Any], *, skip_pattern: bool = False
) -> list[tuple[str, str, str, list[Any]]]:
    """(rule, description, SQL boolean naming a violating row, params) for one field.

    `skip_pattern` leaves the `pattern` probe out even when the field declares one —
    set when that pattern has already failed to compile and been reported as its own
    disagreement, so the rest of the field's constraints are still evaluated rather
    than a bad regex taking the whole field down with it.
    """
    name = field["name"]
    col = quote_ident(name)
    text = f"CAST({col} AS VARCHAR)"
    constraints = field.get("constraints") or {}

    unsupported = set(constraints) - SUPPORTED_CONSTRAINTS
    if unsupported:
        raise CheckError(
            f"field {name!r} carries constraint(s) {sorted(unsupported)} that this check "
            "does not evaluate; refusing to report the field as conformant"
        )

    probes: list[tuple[str, str, str, list[Any]]] = []
    if constraints.get("required") is True:
        probes.append(("constraints.required", "missing though declared required", f"{col} IS NULL", []))
    if "pattern" in constraints and not skip_pattern:
        pattern = constraints["pattern"]
        probes.append(
            (
                "constraints.pattern",
                f"does not match {pattern}",
                f"{col} IS NOT NULL AND NOT regexp_full_match({text}, ?)",
                [pattern],
            )
        )
    if "minLength" in constraints:
        value = constraints["minLength"]
        probes.append(
            (
                "constraints.minLength",
                f"shorter than minLength {value}",
                f"{col} IS NOT NULL AND length({text}) < ?",
                [value],
            )
        )
    if "maxLength" in constraints:
        value = constraints["maxLength"]
        probes.append(
            (
                "constraints.maxLength",
                f"longer than maxLength {value}",
                f"{col} IS NOT NULL AND length({text}) > ?",
                [value],
            )
        )
    if "enum" in constraints:
        members = [str(member) for member in constraints["enum"]]
        if not members:
            probes.append(("constraints.enum", "enum is empty", f"{col} IS NOT NULL", []))
        else:
            slots = ", ".join("?" for _ in members)
            probes.append(
                (
                    "constraints.enum",
                    f"outside enum of {len(members)} member(s)",
                    f"{col} IS NOT NULL AND {text} NOT IN ({slots})",
                    members,
                )
            )
    return probes


def check_constraints(
    con,
    path: str,
    package: str,
    resource_name: str,
    fields: list[dict[str, Any]],
    physical: dict[str, str],
    unusable_patterns: set[str] = frozenset(),
) -> list[Violation]:
    """One pass over the resource, counting every constraint violation at once.

    `unusable_patterns` names fields whose `pattern` failed to compile — already
    reported as its own disagreement by the caller — so their `pattern` probe is left
    out here while their other constraints are still evaluated over the same scan.
    """
    selects: list[str] = []
    params: list[Any] = []
    meta: list[tuple[dict[str, Any], str, str]] = []

    for field in fields:
        name = field["name"]
        if name not in physical:
            continue  # already reported as a missing field
        col = quote_ident(name)
        for rule, description, predicate, probe_params in constraint_probes(
            field, skip_pattern=name in unusable_patterns
        ):
            index = len(meta)
            selects.append(f"count(*) FILTER (WHERE {predicate}) AS v{index}")
            params.extend(probe_params)
            selects.append(f"any_value(CAST({col} AS VARCHAR)) FILTER (WHERE {predicate}) AS e{index}")
            params.extend(probe_params)
            selects.append(f"count({col}) AS n{index}")
            meta.append((field, rule, description))

    if not meta:
        return []

    # One more column than any probe needs: the total row count, which is the correct
    # denominator for `required` (a NULL is absent from `count(col)`, so that column
    # can never be the total a null count is reported out of — see the module history
    # for the `2 of 1` this replaced).
    selects.append("count(*) AS total_rows")
    sql = f"SELECT {', '.join(selects)} FROM read_parquet(?)"  # noqa: S608 - identifiers are quoted, values bound
    params.append(path)
    try:
        row = con.execute(sql, params).fetchone()
    except Exception as exc:
        raise CheckError(f"{package}: constraint scan of {resource_name} failed: {exc}") from exc

    total_rows = row[-1]
    violations: list[Violation] = []
    for index, (field, rule, description) in enumerate(meta):
        count, example, non_null = row[index * 3], row[index * 3 + 1], row[index * 3 + 2]
        if not count:
            continue
        if rule == "constraints.required":
            # The violation IS the null count, so the total it is reported out of must
            # be every row, not `count(col)` — which counts only the non-null ones and
            # so excludes exactly the rows being counted.
            detail = f"{count:,} of {total_rows:,} row(s) {description}"
        else:
            detail = f"{count:,} of {non_null:,} non-null value(s) {description}"
        if example is not None:
            detail += f"; e.g. {example!r}"
        violations.append(Violation(package, resource_name, field["name"], rule, detail))
    return violations


class KeyDeclarationError(Exception):
    """`schema.primaryKey` is a shape this check does not evaluate."""


def primary_key_columns(schema: dict[str, Any]) -> list[str] | None:
    """The columns named by `schema.primaryKey`, or None when the schema declares none.

    Frictionless allows a single field name or an array of them, and both forms are
    read here. Any other shape raises: an unevaluated key must not read as a
    satisfied one.
    """
    if "primaryKey" not in schema:
        return None
    declared = schema["primaryKey"]
    if isinstance(declared, str):
        declared = [declared]
    if not isinstance(declared, list) or not declared:
        raise KeyDeclarationError(
            f"declares primaryKey {schema['primaryKey']!r}, which is neither a field name nor a "
            "non-empty array of field names"
        )
    for name in declared:
        if not isinstance(name, str):
            raise KeyDeclarationError(
                f"declares primaryKey {schema['primaryKey']!r}, in which {name!r} is not a field name"
            )
    return declared


@dataclass(frozen=True)
class KeyScan:
    """What one pass over a candidate key found among the rows.

    `duplicate_*` counts rows whose key is complete and shared with another row;
    `null_*` counts rows whose key is incomplete. The two sets are disjoint on
    purpose, so a file missing half its keys does not also report those rows as
    repeats of each other and bury the second finding under the first.
    """

    rows: int
    null_rows: int
    null_example: str | None
    duplicate_values: int
    duplicate_rows: int
    duplicate_example: str | None
    widest_duplicate: int | None


def key_scan(con, path: str, columns: list[str]) -> KeyScan:
    """Group the resource by the declared key and count what breaks it.

    The GROUP BY is the load-bearing part. `count(*)` on its own is answered out of
    the Parquet row-group metadata without reading a single value, so a check built
    on one would pass whatever the column held; a key is a claim about the values,
    and this reads them.
    """
    quoted = [quote_ident(column) for column in columns]
    incomplete = " OR ".join(f"{column} IS NULL" for column in quoted)
    # A SQL literal holding one apostrophe, so an example value comes back quoted and
    # a NULL comes back bare — 'AB' and NULL are different findings and must not print
    # the same way.
    tick = "''''"
    rendered = [
        f"CASE WHEN {column} IS NULL THEN 'NULL' ELSE {tick} || CAST({column} AS VARCHAR) || {tick} END"
        for column in quoted
    ]
    key_text = rendered[0] if len(rendered) == 1 else "'(' || " + " || ', ' || ".join(rendered) + " || ')'"
    grouped = (
        f"SELECT {', '.join(quoted)}, count(*) AS n FROM read_parquet(?) GROUP BY {', '.join(quoted)}"
    )
    sql = f"""
        SELECT
            coalesce(sum(n), 0),
            coalesce(sum(n) FILTER (WHERE incomplete), 0),
            any_value(key_text) FILTER (WHERE incomplete),
            coalesce(count(*) FILTER (WHERE repeated), 0),
            coalesce(sum(n) FILTER (WHERE repeated), 0),
            any_value(key_text) FILTER (WHERE repeated),
            max(n) FILTER (WHERE repeated)
        FROM (
            SELECT n,
                   ({incomplete}) AS incomplete,
                   NOT ({incomplete}) AND n > 1 AS repeated,
                   {key_text} AS key_text
            FROM ({grouped}) g
        ) x
    """  # noqa: S608 - identifiers are quoted, the path is bound
    try:
        row = con.execute(sql, [path]).fetchone()
    except Exception as exc:  # duckdb raises a family of errors; all mean "cannot scan"
        raise CheckError(
            f"primary-key scan of {path} over ({', '.join(columns)}) failed: {exc}"
        ) from exc
    return KeyScan(
        rows=int(row[0]),
        null_rows=int(row[1]),
        null_example=row[2],
        duplicate_values=int(row[3]),
        duplicate_rows=int(row[4]),
        duplicate_example=row[5],
        widest_duplicate=int(row[6]) if row[6] is not None else None,
    )


def check_primary_key(
    con,
    path: str,
    package: str,
    resource_name: str,
    schema: dict[str, Any],
    physical: dict[str, str],
    scans: dict[tuple[str, ...], KeyScan],
    *,
    embedded: bool,
) -> list[Violation]:
    """`schema.primaryKey` against the columns it names and the rows it claims to identify.

    `embedded` says which copy of the descriptor is speaking — this repository's, or
    the one the object carries in its footer — and it changes one thing beyond the
    wording: a key shape this check cannot read is EXIT_ERROR from the repository's
    copy and a violation from the object's. That is the split the rest of this file
    already makes, and for the same reason: an unknown declared type in `datasets/`
    stops the run, while the same thing in a footer is a statement the object made
    about itself and is reported as one.

    `scans` memoises by column tuple within a resource, so the two copies declaring
    the same key cost one pass over the bytes rather than two.
    """
    prefix = "self-description." if embedded else ""
    speaker = "the description the object carries" if embedded else "the descriptor"
    try:
        columns = primary_key_columns(schema)
    except KeyDeclarationError as exc:
        if not embedded:
            raise CheckError(f"{package}: resource {resource_name!r} {exc}") from exc
        return [Violation(package, resource_name, "", f"{prefix}schema.primaryKey", f"{speaker} {exc}")]
    if columns is None:
        return []

    label = ", ".join(columns)
    missing = [column for column in columns if column not in physical]
    if missing:
        # Not scanned: a key over a column that is not there cannot be counted, and
        # its absence is already the finding.
        return [
            Violation(
                package,
                resource_name,
                column,
                f"{prefix}schema.primaryKey",
                f"{speaker} names this column in the primary key ({label}), and the Parquet "
                "has no such column",
            )
            for column in missing
        ]

    signature = tuple(columns)
    scan = scans.get(signature)
    if scan is None:
        scan = key_scan(con, path, columns)
        scans[signature] = scan

    violations: list[Violation] = []
    if scan.null_rows:
        detail = (
            f"{speaker} declares ({label}) the primary key, and {scan.null_rows:,} of "
            f"{scan.rows:,} row(s) hold a NULL in it, which no key value may"
        )
        if scan.null_example:
            detail += f"; e.g. {scan.null_example}"
        violations.append(
            Violation(package, resource_name, label, f"{prefix}schema.primaryKey.null", detail)
        )
    if scan.duplicate_rows:
        detail = (
            f"{speaker} declares ({label}) the primary key, and {scan.duplicate_rows:,} of "
            f"{scan.rows:,} row(s) share a key value with another row, across "
            f"{scan.duplicate_values:,} repeated value(s)"
        )
        if scan.widest_duplicate:
            detail += f", the commonest on {scan.widest_duplicate:,} row(s)"
        if scan.duplicate_example:
            detail += f"; e.g. {scan.duplicate_example}"
        violations.append(
            Violation(package, resource_name, label, f"{prefix}schema.primaryKey.duplicate", detail)
        )
    return violations


def check_resource(
    con,
    descriptor_path: Path,
    package: str,
    document: dict[str, Any],
    resource: dict[str, Any],
    require_self_description: bool,
) -> tuple[list[Violation], bool]:
    name = resource.get("name") or resource.get("path") or "<unnamed>"
    raw_path = resource.get("path")
    if not raw_path:
        raise CheckError(f"{package}: resource {name!r} declares no path")
    path, is_remote = resolve_path(descriptor_path, raw_path)

    schema = resource.get("schema") or {}
    fields = schema.get("fields") or []
    if not fields:
        raise CheckError(f"{package}: resource {name!r} declares no schema fields")

    if is_remote:
        con.execute("INSTALL httpfs")
        con.execute("LOAD httpfs")
    elif not Path(path).exists():
        raise CheckError(f"{package}: resource {name!r} points at {path}, which does not exist")

    physical = physical_types(con, path)
    violations: list[Violation] = []

    declared = [field["name"] for field in fields]
    for missing in [column for column in declared if column not in physical]:
        violations.append(
            Violation(package, name, missing, "schema.fields", "declared but absent from the Parquet")
        )
    for extra in [column for column in physical if column not in declared]:
        violations.append(
            Violation(
                package,
                name,
                extra,
                "schema.fields",
                f"present in the Parquet as {physical[extra]} but undeclared in the descriptor",
            )
        )

    for field in fields:
        column = field["name"]
        if column not in physical:
            continue
        declared_type = field.get("type", "any")
        if declared_type == "any":
            continue
        allowed = COERCIBLE.get(declared_type)
        if allowed is None:
            raise CheckError(f"{package}: field {column!r} declares unknown type {declared_type!r}")
        if physical[column] not in allowed:
            violations.append(
                Violation(
                    package,
                    name,
                    column,
                    "type",
                    f"declared {declared_type!r} is not coercible from Parquet physical type "
                    f"{physical[column]} (coercible from: {', '.join(sorted(allowed))})",
                )
            )

    unusable: set[str] = set()
    for field in fields:
        pattern = (field.get("constraints") or {}).get("pattern")
        if pattern is None:
            continue
        failure = check_pattern_compiles(con, pattern)
        if failure:
            unusable.add(field["name"])
            violations.append(
                Violation(
                    package,
                    name,
                    field["name"],
                    "constraints.pattern",
                    f"is not a usable regular expression: {failure}",
                )
            )

    # A field named in `unusable` keeps its place here: its `pattern` was already
    # reported above as its own disagreement, and `unusable_patterns` tells
    # `check_constraints` to leave only that one probe out, not the field's other
    # constraints along with it.
    scannable = [field for field in fields if field["name"] in physical]
    violations.extend(check_constraints(con, path, package, name, scannable, physical, unusable))

    # One cache per resource: the repository's copy and the object's own copy usually
    # declare the same key, and that is one pass over the bytes, not two.
    key_scans: dict[tuple[str, ...], KeyScan] = {}
    violations.extend(
        check_primary_key(con, path, package, name, schema, physical, key_scans, embedded=False)
    )

    declared_bytes = resource.get("bytes")
    if declared_bytes is not None:
        actual = remote_size(path) if is_remote else Path(path).stat().st_size
        if actual is None:
            raise CheckError(f"{package}: {name!r} declares bytes but {path} reports no size")
        if actual != declared_bytes:
            violations.append(
                Violation(
                    package,
                    name,
                    "",
                    "resource.bytes",
                    f"descriptor declares {declared_bytes:,} bytes; the published file is {actual:,}",
                )
            )

    self_violations, self_described = check_self_description(
        con, path, package, name, document, physical, key_scans, require_self_description
    )
    violations.extend(self_violations)
    return violations, self_described


# How many structural differences between the two copies of a descriptor are worth
# printing before the list stops being a diagnosis and starts being a diff. The count
# is reported either way, so nothing is hidden by the cut-off.
MAX_REPORTED_DIFFERENCES = 12


def json_differences(expected: Any, actual: Any, pointer: str = "") -> list[str]:
    """Every place two JSON documents disagree, as `<pointer>: expected … got …`.

    Structural rather than textual on purpose: the two copies are written by
    different code paths, so indentation and key order are not disagreements and
    a value is.
    """
    at = pointer or "/"
    if type(expected) is not type(actual) and not (
        isinstance(expected, (int, float))
        and isinstance(actual, (int, float))
        and not isinstance(expected, bool)
        and not isinstance(actual, bool)
    ):
        return [f"{at}: this repository has {type(expected).__name__}, the object has {type(actual).__name__}"]
    if isinstance(expected, dict):
        differences: list[str] = []
        for key in sorted(set(expected) | set(actual)):
            if key not in actual:
                differences.append(f"{at.rstrip('/')}/{key}: absent from the object's own description")
            elif key not in expected:
                differences.append(f"{at.rstrip('/')}/{key}: present in the object but not in this repository")
            else:
                differences.extend(json_differences(expected[key], actual[key], f"{pointer}/{key}"))
        return differences
    if isinstance(expected, list):
        if len(expected) != len(actual):
            return [f"{at}: this repository has {len(expected)} item(s), the object has {len(actual)}"]
        differences = []
        for index, (left, right) in enumerate(zip(expected, actual)):
            differences.extend(json_differences(left, right, f"{pointer}/{index}"))
        return differences
    if expected != actual:
        return [f"{at}: this repository has {expected!r}, the object has {actual!r}"]
    return []


def check_self_description(
    con,
    path: str,
    package: str,
    resource_name: str,
    document: dict[str, Any],
    physical: dict[str, str],
    key_scans: dict[tuple[str, ...], KeyScan],
    require: bool,
) -> tuple[list[Violation], bool]:
    """Read the descriptor the object carries and hold it to the same rules.

    Returns the violations and whether the object carried a description at all, so
    the run can report how much of the published surface answers for itself.

    The CONSTRAINT scan is not repeated here. When the embedded copy and this
    repository's copy agree — which the first rule below enforces — a constraint scan
    of one is a constraint scan of the other, over the same file. When they disagree,
    the disagreement is already reported and a second scan would add nothing.

    The primary key IS re-read, because it is not a per-value rule: a key is a claim
    about the whole column, the object states it in its own footer, and the reader
    holding only the object URL is acting on that copy. It costs nothing when the two
    copies agree — `key_scans` is the pass already made for this resource.
    """
    violations: list[Violation] = []
    try:
        carried = read_self_description(con, path)
    except Exception as exc:  # noqa: BLE001 - any failure here means "cannot check"
        raise CheckError(f"{package}: cannot read the footer of {path}: {exc}") from exc

    if carried is None:
        if require:
            violations.append(
                Violation(
                    package,
                    resource_name,
                    "",
                    "self-description",
                    f"carries no {DESCRIPTOR_KEY} in its Parquet footer, so a consumer holding only "
                    "its URL cannot ask it what it is",
                )
            )
        return violations, False

    try:
        embedded = json.loads(carried)
    except json.JSONDecodeError as exc:
        violations.append(
            Violation(
                package,
                resource_name,
                "",
                "self-description",
                f"carries a {DESCRIPTOR_KEY} in its footer that is not JSON: {exc}",
            )
        )
        return violations, True

    if not isinstance(embedded, dict):
        violations.append(
            Violation(
                package,
                resource_name,
                "",
                "self-description",
                f"carries a {DESCRIPTOR_KEY} that is a {type(embedded).__name__}, not a JSON object",
            )
        )
        return violations, True

    for embedded_resource in embedded.get("resources") or []:
        if not isinstance(embedded_resource, dict):
            continue
        stated = [key for key in SELF_REFERENTIAL if key in embedded_resource]
        for key in stated:
            violations.append(
                Violation(
                    package,
                    resource_name,
                    "",
                    "self-description",
                    f"states its own {key!r} inside itself; a file cannot, because writing the "
                    f"value changes the value. {key!r} belongs in this repository's copy, which is "
                    "measured against the finished object",
                )
            )

    try:
        expected = embedded_form(document)
    except Exception as exc:  # noqa: BLE001 - a malformed repository copy is not checkable
        raise CheckError(f"{package}: cannot derive the embedded form: {exc}") from exc

    differences = json_differences(expected, embedded)
    for difference in differences[:MAX_REPORTED_DIFFERENCES]:
        violations.append(
            Violation(
                package,
                resource_name,
                "",
                "self-description",
                f"the description the object carries differs from this repository's at {difference}",
            )
        )
    if len(differences) > MAX_REPORTED_DIFFERENCES:
        violations.append(
            Violation(
                package,
                resource_name,
                "",
                "self-description",
                f"{len(differences)} difference(s) in total between the two copies; "
                f"the first {MAX_REPORTED_DIFFERENCES} are named above",
            )
        )

    # The object's own description against the object's own columns. This is the half
    # that does not go through this repository at all — it is what a consumer who never
    # finds `datasets/` is reading, checked against the bytes they are reading it from.
    for embedded_resource in embedded.get("resources") or []:
        if not isinstance(embedded_resource, dict):
            continue
        fields = (embedded_resource.get("schema") or {}).get("fields") or []
        declared = [field.get("name") for field in fields if isinstance(field, dict)]
        for missing in [column for column in declared if column not in physical]:
            violations.append(
                Violation(
                    package,
                    resource_name,
                    missing,
                    "self-description.schema.fields",
                    "the object's own description declares this field, and the object has no such column",
                )
            )
        for extra in [column for column in physical if column not in declared]:
            violations.append(
                Violation(
                    package,
                    resource_name,
                    extra,
                    "self-description.schema.fields",
                    f"the object has this column as {physical[extra]}, and its own description "
                    "does not declare it",
                )
            )
        for field in fields:
            if not isinstance(field, dict):
                continue
            column = field.get("name")
            if column not in physical:
                continue
            declared_type = field.get("type", "any")
            if declared_type == "any":
                continue
            allowed = COERCIBLE.get(declared_type)
            if allowed is None:
                violations.append(
                    Violation(
                        package,
                        resource_name,
                        column,
                        "self-description.type",
                        f"the object's own description declares unknown type {declared_type!r}",
                    )
                )
                continue
            if physical[column] not in allowed:
                violations.append(
                    Violation(
                        package,
                        resource_name,
                        column,
                        "self-description.type",
                        f"the object's own description declares {declared_type!r}, which is not "
                        f"coercible from the Parquet physical type {physical[column]} "
                        f"(coercible from: {', '.join(sorted(allowed))})",
                    )
                )
        violations.extend(
            check_primary_key(
                con,
                path,
                package,
                resource_name,
                embedded_resource.get("schema") or {},
                physical,
                key_scans,
                embedded=True,
            )
        )
    return violations, True


def check_foreign_keys(
    packages: dict[str, dict[str, Any]], restrict_to: dict[str, dict[str, Any]] | None = None
) -> list[Violation]:
    """Declared foreign keys must resolve, in this package or a sibling, to a compatible type.

    `packages` is every package this run has loaded a descriptor for — a reference
    resolves against the whole tree regardless of `--only`, so a documented flag
    cannot make a real sibling invisible and turn a resolvable reference into a
    fabricated violation. `restrict_to`, when given, narrows which packages' *own*
    declarations are walked and reported on; the packages a reference can land on are
    `packages` either way. `None` means walk and report on all of `packages`, which is
    the no-`--only` case.
    """
    index: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for package_name, descriptor in packages.items():
        for resource in descriptor.get("resources") or []:
            index.setdefault(resource.get("name", ""), []).append((package_name, resource))

    def declared_type(resource: dict[str, Any], field_name: str) -> str | None:
        for field in (resource.get("schema") or {}).get("fields") or []:
            if field["name"] == field_name:
                return field.get("type", "any")
        return None

    def compatible(left: str, right: str) -> bool:
        if left == right or "any" in (left, right):
            return True
        return any({left, right} <= group for group in FK_COMPATIBLE_GROUPS)

    violations: list[Violation] = []
    walked = packages if restrict_to is None else restrict_to
    for package_name, descriptor in walked.items():
        for resource in descriptor.get("resources") or []:
            resource_name = resource.get("name", "")
            for key in (resource.get("schema") or {}).get("foreignKeys") or []:
                local_fields = key.get("fields") or []
                if isinstance(local_fields, str):
                    local_fields = [local_fields]
                reference = key.get("reference") or {}
                target_name = reference.get("resource") or resource_name
                target_fields = reference.get("fields") or []
                if isinstance(target_fields, str):
                    target_fields = [target_fields]
                label = ",".join(local_fields)

                candidates = index.get(target_name, [])
                if not candidates:
                    violations.append(
                        Violation(
                            package_name,
                            resource_name,
                            label,
                            "schema.foreignKeys",
                            f"references resource {target_name!r}, which no package declares",
                        )
                    )
                    continue
                if len(candidates) > 1:
                    owners = ", ".join(sorted(owner for owner, _ in candidates))
                    violations.append(
                        Violation(
                            package_name,
                            resource_name,
                            label,
                            "schema.foreignKeys",
                            f"references resource {target_name!r}, declared by more than one package ({owners})",
                        )
                    )
                    continue
                if len(local_fields) != len(target_fields):
                    violations.append(
                        Violation(
                            package_name,
                            resource_name,
                            label,
                            "schema.foreignKeys",
                            f"maps {len(local_fields)} field(s) onto {len(target_fields)}",
                        )
                    )
                    continue

                target_owner, target_resource = candidates[0]
                for local_field, target_field in zip(local_fields, target_fields):
                    left = declared_type(resource, local_field)
                    right = declared_type(target_resource, target_field)
                    if left is None:
                        violations.append(
                            Violation(
                                package_name,
                                resource_name,
                                local_field,
                                "schema.foreignKeys",
                                "names a field this resource does not declare",
                            )
                        )
                        continue
                    if right is None:
                        violations.append(
                            Violation(
                                package_name,
                                resource_name,
                                local_field,
                                "schema.foreignKeys",
                                f"references {target_owner}::{target_name}.{target_field}, "
                                "which that package does not declare",
                            )
                        )
                        continue
                    if not compatible(left, right):
                        violations.append(
                            Violation(
                                package_name,
                                resource_name,
                                local_field,
                                "schema.foreignKeys",
                                f"declared {left!r} references {target_owner}::{target_name}."
                                f"{target_field} declared {right!r}; the join is described with "
                                "mismatched types",
                            )
                        )
    return violations


# ------------------------------------------------------------------- catalogue
#
# The table in README.md is the first quantitative claim a stranger meets about
# these datasets, and it sits directly beside links to the bytes. Nothing read it
# back against those bytes, so three of its four row counts had drifted — the
# crosswalk row said 6,570 against 207,099 published rows. The rules below read the
# table, count the rows in the object each row links to, and hold one to the other.

# The header cell that identifies the catalogue table. The table is found by its
# columns rather than by the heading above it, so renaming the section does not
# quietly stop the check.
CATALOGUE_ROWS_COLUMN = "rows"

# The link that identifies which dataset a catalogue row is about. A row is tied to
# a package by the descriptor it links to, not by its prose label, so retitling a
# dataset does not detach it from the bytes it is measured against.
CATALOGUE_DESCRIPTOR_LINK = re.compile(r"\[[^\]]*\]\(([^)]*datapackage\.json)\)")

# A count written in full, with or without thousands separators: 2,125 · 207099.
EXACT_FIGURE = re.compile(r"^(?:\d{1,3}(?:,\d{3})+|\d+)$")

# A count written to a stated precision: 3.36M · 1.2K · 4B.
ROUNDED_FIGURE = re.compile(r"^\d+(?:\.(\d+))?([KMB])$")

SUFFIX_SCALE = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}

TABLE_DELIMITER_CELL = re.compile(r":?-+:?")


def render_count(count: int, like: str) -> str | None:
    """`count` written in the same form and to the same precision as the figure `like`.

    This is the whole rounding rule, and it is deliberately one function so there is
    one place to read it: **a stated figure is read at the precision it is written
    to.** A figure written in full must equal the count exactly — 10,415 is a claim
    about every one of its digits, and the object holds 10,414. A figure written with
    a magnitude suffix is a rounded form, and must equal the count rounded half-up to
    the number of decimal places the figure itself carries: for 3,377,398 rows, `3.38M`
    is right, `3.4M` and `3M` are right at their own coarser precisions, and `3.36M`
    is wrong — not because it is rounded, but because it is not what that count rounds
    to at two decimal places.

    So rounding is not banned, and neither is any figure of the right magnitude
    accepted: the tolerance is exactly the precision the author chose to write.

    Returns None for a figure this rule cannot read, which is a disagreement rather
    than a skip — see `check_catalogue`.
    """
    if EXACT_FIGURE.match(like):
        return f"{count:,}" if "," in like else str(count)
    rounded = ROUNDED_FIGURE.match(like)
    if rounded:
        decimals, suffix = rounded.groups()
        places = len(decimals or "")
        scaled = (Decimal(count) / Decimal(SUFFIX_SCALE[suffix])).quantize(
            Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP
        )
        return f"{scaled:.{places}f}{suffix}"
    return None


@dataclass(frozen=True)
class CatalogueRow:
    index: int  # 0-based index into the README's lines, for rewriting and for the message
    stated: str  # the Rows cell exactly as written
    rows_cell: int  # which cell of the row that was
    descriptor: str  # the datapackage.json path the row links to, as written


def split_on_unescaped_pipes(text: str) -> list[str]:
    r"""Split on the `|` characters that delimit cells, leaving escaped ones in place.

    A cell may carry a literal pipe by escaping it, so `|` and `\|` are different
    characters to this format and a split that cannot tell them apart is not a lax
    reading of it but a wrong one. The backslash is consumed together with whatever
    follows it, which is what keeps `\\|` — a literal backslash and then a real
    delimiter — from being read as an escaped pipe.
    """
    cells: list[str] = []
    current: list[str] = []
    index = 0
    while index < len(text):
        character = text[index]
        if character == "\\" and index + 1 < len(text):
            current.append(character)
            current.append(text[index + 1])
            index += 2
            continue
        if character == "|":
            cells.append("".join(current))
            current = []
            index += 1
            continue
        current.append(character)
        index += 1
    cells.append("".join(current))
    return cells


def table_cells(line: str) -> list[str]:
    r"""The raw cells of a pipe-table row, or [] when the line is not one.

    **Keep this split escape-aware.** Dividing on every `|` fails in the direction
    that passes: a row with one column dropped and a `\|` inside another cell splits
    into exactly the number of cells the header wants, and the figure that lands in
    the Rows slot is read out of a neighbouring cell. The check then agrees with a
    number no reader of the rendered table is ever shown, which is worse than not
    checking at all. A row whose cell count disagrees with its header is refused by
    `parse_catalogue` rather than measured, so a malformed row cannot pass either way.

    A row must open and close with a delimiter, which is stricter than the format
    requires — a closing `|` is optional there. Refusing a row this cannot frame is
    the safe direction, and every row of the catalogue carries both.
    """
    stripped = line.strip()
    if not stripped.startswith("|") or len(stripped) < 2:
        return []
    cells = split_on_unescaped_pipes(stripped)
    if len(cells) < 2 or cells[0] != "" or cells[-1] != "":
        return []
    return cells[1:-1]


def parse_catalogue(readme_path: Path, text: str) -> list[CatalogueRow]:
    """Every body row of every table in `text` that has a `Rows` column.

    Raises rather than returning nothing when there is no such table: a catalogue the
    check cannot find is a check that has stopped running, and that must not read as
    a pass.
    """
    lines = text.split("\n")
    rows: list[CatalogueRow] = []
    tables = 0

    for index, line in enumerate(lines):
        header = table_cells(line)
        if not header or index + 1 >= len(lines):
            continue
        delimiter = table_cells(lines[index + 1])
        if len(delimiter) != len(header):
            continue
        if not all(TABLE_DELIMITER_CELL.fullmatch(cell.strip()) for cell in delimiter):
            continue
        names = [cell.strip().lower() for cell in header]
        if CATALOGUE_ROWS_COLUMN not in names:
            continue

        tables += 1
        rows_cell = names.index(CATALOGUE_ROWS_COLUMN)
        for body in range(index + 2, len(lines)):
            cells = table_cells(lines[body])
            if not cells:
                break
            if len(cells) != len(header):
                raise CheckError(
                    f"{readme_path}: line {body + 1} of the catalogue has {len(cells)} cell(s) "
                    f"against a header of {len(header)}"
                )
            link = next(
                (match.group(1) for match in map(CATALOGUE_DESCRIPTOR_LINK.search, cells) if match),
                None,
            )
            if link is None:
                raise CheckError(
                    f"{readme_path}: line {body + 1} of the catalogue links to no datapackage.json, "
                    "so there is nothing to measure its row count against"
                )
            rows.append(CatalogueRow(body, cells[rows_cell].strip(), rows_cell, link))

    if not tables:
        raise CheckError(
            f"{readme_path}: no table with a {CATALOGUE_ROWS_COLUMN!r} column — the catalogue is "
            "either missing or no longer states row counts, and either way nothing here was checked"
        )
    return rows


def object_row_count(con, path: str) -> int:
    """How many rows the published object holds.

    Asked of the object, never of a descriptor: a descriptor that is itself wrong
    about its data would otherwise let the catalogue agree with it and still be wrong
    about the bytes. DuckDB answers this from the Parquet's row-group metadata, so it
    is a range read of the footer rather than a download.
    """
    try:
        row = con.execute("SELECT count(*) FROM read_parquet(?)", [path]).fetchone()
    except Exception as exc:  # duckdb raises a family of errors; all mean "cannot read"
        raise CheckError(f"cannot count the rows at {path}: {exc}") from exc
    return int(row[0])


def check_catalogue(
    con,
    readme_path: Path,
    descriptor_paths: list[Path],
    packages: dict[str, dict[str, Any]],
    only: set[str] | None,
    write: bool,
) -> tuple[list[Violation], int]:
    """Hold every catalogue row count to the object that row links to.

    With `write`, the measurement rewrites the figure instead of reporting it — the
    correction is produced by counting, which is the only way it stays true. A figure
    the rule cannot read is not one it can rewrite, and a published dataset the
    catalogue omits cannot be invented, so both of those stay disagreements either way.
    """
    try:
        text = readme_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CheckError(f"cannot read the catalogue at {readme_path}: {exc}") from exc

    rows = parse_catalogue(readme_path, text)
    by_path = {Path(key).resolve(): document for key, document in packages.items()}

    violations: list[Violation] = []
    lines = text.split("\n")
    rewritten = 0
    listed: set[Path] = set()
    checked = 0

    for row in rows:
        descriptor_path = (readme_path.parent / row.descriptor).resolve()
        if only is not None and descriptor_path.parent.name not in only:
            continue
        document = by_path.get(descriptor_path)
        if document is None:
            raise CheckError(
                f"{readme_path}: line {row.index + 1} of the catalogue links to {row.descriptor}, "
                "which is not a descriptor this run loaded"
            )
        listed.add(descriptor_path)

        resources = document.get("resources") or []
        if len(resources) != 1:
            raise CheckError(
                f"{readme_path}: line {row.index + 1} states one row count, but {row.descriptor} "
                f"declares {len(resources)} resource(s), so there is no one count it names"
            )
        raw_path = resources[0].get("path")
        if not raw_path:
            raise CheckError(f"{row.descriptor}: resource declares no path")
        path, is_remote = resolve_path(descriptor_path, raw_path)
        if is_remote:
            con.execute("INSTALL httpfs")
            con.execute("LOAD httpfs")
        elif not Path(path).exists():
            raise CheckError(f"{row.descriptor}: resource points at {path}, which does not exist")

        count = object_row_count(con, path)
        checked += 1
        rendered = render_count(count, row.stated)

        if rendered is None:
            violations.append(
                Violation(
                    str(readme_path),
                    row.descriptor,
                    "",
                    "catalogue.rows",
                    f"line {row.index + 1} states {row.stated!r}, which is not a row count this "
                    f"check can read; the published object holds {count:,} rows",
                )
            )
        elif rendered != row.stated:
            if write:
                cells = table_cells(lines[row.index])
                cells[row.rows_cell] = cells[row.rows_cell].replace(row.stated, rendered, 1)
                lines[row.index] = "|" + "|".join(cells) + "|"
                rewritten += 1
                print(
                    f"catalogue: {readme_path.name} line {row.index + 1} — {row.stated} -> {rendered} "
                    f"({count:,} rows in {raw_path})"
                )
            else:
                detail = (
                    f"line {row.index + 1} states {row.stated}; "
                    f"the published object holds {count:,} rows"
                )
                if rendered not in (str(count), f"{count:,}"):
                    detail += f", which is {rendered} at the precision {row.stated} is written to"
                violations.append(
                    Violation(str(readme_path), row.descriptor, "", "catalogue.rows", detail)
                )

    if only is None:
        for descriptor_path in descriptor_paths:
            if descriptor_path.resolve() not in listed:
                violations.append(
                    Violation(
                        str(readme_path),
                        str(descriptor_path),
                        "",
                        "catalogue.missing",
                        f"{descriptor_path} is published, and no catalogue row in "
                        f"{readme_path.name} links to it, so nothing states its size",
                    )
                )

    if write and rewritten:
        readme_path.write_text("\n".join(lines), encoding="utf-8")
    if write:
        print(f"catalogue: {rewritten} of {checked} row(s) rewritten from the measurement")

    return violations, checked


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--datasets-dir", default="datasets", type=Path)
    parser.add_argument("--only", action="append", metavar="SLUG", help="restrict to one dataset slug (repeatable)")
    parser.add_argument("--json", metavar="FILE", type=Path, help="also write the findings as JSON")
    parser.add_argument(
        "--readme",
        metavar="FILE",
        type=Path,
        help="the catalogue to check (default: README.md beside --datasets-dir)",
    )
    parser.add_argument(
        "--write-catalogue",
        action="store_true",
        help="rewrite each catalogue row count from the measured count, in the form the cell is "
        "already written in, instead of reporting the disagreement. The correction is produced by "
        "counting the published rows, which is what keeps it true.",
    )
    parser.add_argument(
        "--require-self-description",
        action="store_true",
        help="refuse a resource whose Parquet does not carry its own descriptor in its footer. "
        "Off by default: the published objects predate stamping, and a check that demanded one "
        "would report every one of them as a disagreement about data that is in fact correct.",
    )
    args = parser.parse_args(argv)

    try:
        import duckdb
    except ImportError:
        print("descriptor check: the duckdb Python package is required", file=sys.stderr)
        return EXIT_ERROR

    try:
        descriptor_paths = discover(args.datasets_dir, args.only)
        # A foreign key resolves against the whole tree, `--only` or not (see
        # `check_foreign_keys`), so the full set of descriptors is loaded even when
        # `--only` narrows what gets scanned and reported below. Skip the second
        # discovery when there is nothing to narrow.
        all_descriptor_paths = descriptor_paths if not args.only else discover(args.datasets_dir, None)
    except CheckError as exc:
        print(f"descriptor check: {exc}", file=sys.stderr)
        return EXIT_ERROR

    con = duckdb.connect()

    all_packages: dict[str, dict[str, Any]] = {}
    for descriptor_path in all_descriptor_paths:
        try:
            all_packages[str(descriptor_path)] = load_descriptor(descriptor_path)
        except CheckError as exc:
            print(f"descriptor check: {exc}", file=sys.stderr)
            return EXIT_ERROR

    packages: dict[str, dict[str, Any]] = (
        all_packages if not args.only else {str(p): all_packages[str(p)] for p in descriptor_paths}
    )
    violations: list[Violation] = []
    self_described: list[str] = []
    resource_count = 0

    for descriptor_path in descriptor_paths:
        package = str(descriptor_path)
        descriptor = packages[package]
        try:
            resources = descriptor.get("resources") or []
            if not resources:
                raise CheckError(f"{package}: declares no resources")
            for resource in resources:
                resource_count += 1
                found, carried = check_resource(
                    con, descriptor_path, package, descriptor, resource, args.require_self_description
                )
                violations.extend(found)
                if carried:
                    self_described.append(f"{descriptor_path.parent.name}::{resource.get('name')}")
        except CheckError as exc:
            print(f"descriptor check: {exc}", file=sys.stderr)
            return EXIT_ERROR

    try:
        violations.extend(check_foreign_keys(all_packages, packages if args.only else None))
    except CheckError as exc:
        print(f"descriptor check: {exc}", file=sys.stderr)
        return EXIT_ERROR

    readme_path = args.readme if args.readme is not None else args.datasets_dir.parent / "README.md"
    try:
        catalogue_violations, catalogue_rows = check_catalogue(
            con,
            readme_path,
            descriptor_paths,
            packages,
            set(args.only) if args.only else None,
            args.write_catalogue,
        )
        violations.extend(catalogue_violations)
    except CheckError as exc:
        print(f"descriptor check: {exc}", file=sys.stderr)
        return EXIT_ERROR

    checked = ", ".join(path.parent.name for path in descriptor_paths)
    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "checked": checked,
                    "self_described": self_described,
                    "resources": resource_count,
                    "violations": [asdict(v) for v in violations],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    carried = (
        f"{len(self_described)} of {resource_count} resource(s) carry their own description"
        + (f" ({', '.join(self_described)})" if self_described else "")
        + f"; {catalogue_rows} catalogue row count(s) measured against the objects they link to"
    )
    if not violations:
        print(
            f"descriptor check: {len(descriptor_paths)} descriptor(s) agree with their data ({checked}); "
            f"{carried}"
        )
        return EXIT_OK

    print(f"descriptor check: {len(violations)} disagreement(s) between descriptor and data\n")
    for violation in violations:
        print(violation.line())
    print(
        f"\ndescriptor check: FAILED — {len(violations)} disagreement(s) across "
        f"{len(descriptor_paths)} descriptor(s) ({checked}); {carried}"
    )
    return EXIT_VIOLATIONS


if __name__ == "__main__":
    sys.exit(main())
