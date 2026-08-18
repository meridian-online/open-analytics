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
                     evaluated over every row in one pass.
  4. bytes         — the declared `resources[].bytes` matches the size the server
                     (or the filesystem) reports.
  5. self-description
                   — when the Parquet carries its own descriptor in its footer (see
                     `stamp_descriptor.py`), that copy is read back out of the file and
                     held to the same rules: it must agree with this repository's copy
                     apart from the two keys a file cannot state about itself, and its
                     field set and types must agree with the columns actually present.
                     An object that carries none is not a violation unless
                     `--require-self-description` is passed — nothing publishes a
                     stamped object yet, and a check that demanded one would refuse
                     every file currently served.

And per package:

  6. foreignKeys   — each declared foreign key resolves to a real resource+field,
                     in this package or a sibling one, of compatible declared type.

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


def constraint_probes(field: dict[str, Any]) -> list[tuple[str, str, str, list[Any]]]:
    """(rule, description, SQL boolean naming a violating row, params) for one field."""
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
        probes.append(("constraints.required", "declared required", f"{col} IS NULL", []))
    if "pattern" in constraints:
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
    con, path: str, package: str, resource_name: str, fields: list[dict[str, Any]], physical: dict[str, str]
) -> list[Violation]:
    """One pass over the resource, counting every constraint violation at once."""
    selects: list[str] = []
    params: list[Any] = []
    meta: list[tuple[dict[str, Any], str, str]] = []

    for field in fields:
        name = field["name"]
        if name not in physical:
            continue  # already reported as a missing field
        col = quote_ident(name)
        for rule, description, predicate, probe_params in constraint_probes(field):
            index = len(meta)
            selects.append(f"count(*) FILTER (WHERE {predicate}) AS v{index}")
            params.extend(probe_params)
            selects.append(f"any_value(CAST({col} AS VARCHAR)) FILTER (WHERE {predicate}) AS e{index}")
            params.extend(probe_params)
            selects.append(f"count({col}) AS n{index}")
            meta.append((field, rule, description))

    if not meta:
        return []

    sql = f"SELECT {', '.join(selects)} FROM read_parquet(?)"  # noqa: S608 - identifiers are quoted, values bound
    params.append(path)
    try:
        row = con.execute(sql, params).fetchone()
    except Exception as exc:
        raise CheckError(f"{package}: constraint scan of {resource_name} failed: {exc}") from exc

    violations: list[Violation] = []
    for index, (field, rule, description) in enumerate(meta):
        count, example, non_null = row[index * 3], row[index * 3 + 1], row[index * 3 + 2]
        if not count:
            continue
        detail = f"{count:,} of {non_null:,} non-null value(s) {description}"
        if example is not None:
            detail += f"; e.g. {example!r}"
        violations.append(Violation(package, resource_name, field["name"], rule, detail))
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

    scannable = [field for field in fields if field["name"] in physical and field["name"] not in unusable]
    violations.extend(check_constraints(con, path, package, name, scannable, physical))

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
        con, path, package, name, document, physical, require_self_description
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
    require: bool,
) -> tuple[list[Violation], bool]:
    """Read the descriptor the object carries and hold it to the same rules.

    Returns the violations and whether the object carried a description at all, so
    the run can report how much of the published surface answers for itself.

    The row scan is NOT repeated here. When the embedded copy and this repository's
    copy agree — which the first rule below enforces — a constraint scan of one is a
    constraint scan of the other, over the same file. When they disagree, the
    disagreement is already reported and a second scan would add nothing.
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
    return violations, True


def check_foreign_keys(packages: dict[str, dict[str, Any]]) -> list[Violation]:
    """Declared foreign keys must resolve, in this package or a sibling, to a compatible type."""
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
    for package_name, descriptor in packages.items():
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
    except CheckError as exc:
        print(f"descriptor check: {exc}", file=sys.stderr)
        return EXIT_ERROR

    con = duckdb.connect()
    packages: dict[str, dict[str, Any]] = {}
    violations: list[Violation] = []
    self_described: list[str] = []
    resource_count = 0

    for descriptor_path in descriptor_paths:
        package = str(descriptor_path)
        try:
            descriptor = load_descriptor(descriptor_path)
            packages[package] = descriptor
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
        violations.extend(check_foreign_keys(packages))
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
