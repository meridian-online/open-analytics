#!/usr/bin/env python3
"""Check a Protocol's built tables against the lake, just before they are published.

Three checks can only be known at the point of publishing, because each needs both
what this Run built and what the open lake already holds:

  label agreement  — `check_label_agreement.py --strict` over every package: the
                     built descriptors beside every other table's, so a field labelled
                     one way here and another way in the lake is caught before it lands.
  frictionless     — `check_frictionless.py --only <each built package>`: the
                     reference implementation loads each built descriptor.
  descriptors      — `check_descriptors.py --only <each built package>
                     --require-self-description --no-catalogue`: rules 1 to 7 and the
                     licence rules, read against the local Parquet. The catalogue rule
                     is the lake check's.

    check_before_publish.py --datasets-dir ../../datasets --slug <slug> \\
        --lake-bundle build/lake_descriptors.json \\
        --built <descriptor>=<parquet> [--built ...] \\
        [--json build/check_before_publish.json]

`--lake-bundle` is `emit_descriptor.py --all --bundle`'s output: table name to the
lake's descriptor. Each `--built` names a descriptor the Protocol's stamp step wrote
and the Parquet it describes, relative to the current directory (the Protocol's
folder, when `arc` runs this).

THE TREE. `build/check_tree/` is removed and rebuilt on every run, since a reused
tree keeps files a later run no longer writes. `datasets/` in it is `datasets/` with
every Protocol's descriptor taken from the lake bundle (`emit_descriptor.write_tree`),
and each built descriptor laid over its table's at `datasets/<slug>/<file>`. A table
the lake does not hold yet, as on a first publish, is simply absent, so the built
descriptor is compared against its siblings alone.

`packages/` beside it holds one `<package>/datapackage.json` per table, because the
per-package checks read only `*/datapackage.json`: a Protocol's per-table
`<name>.datapackage.json` appears there as package `<name>`. Label agreement reads
`datasets/` as the lake check does, so a per-table descriptor is not compared there.

THE BYTES. A built descriptor keeps the `path` it will be published at. The reference
implementation refuses an absolute local path, and the Parquet's footer carries the
published address, so rewriting `path` would fail both checks for a reason that is not
a defect. `check_descriptors.py --read-from` reads each one from the local Parquet
instead.

The checks run in-process, through each script's `main(argv)`, so the `uv@1` step
that runs this pins one script digest.

Exit codes: 0 every check passed; 1 a check failed, each failing check named with its
findings; 2 the inputs could not be read or do not describe this Protocol.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_descriptors  # noqa: E402
import check_frictionless  # noqa: E402
import check_label_agreement  # noqa: E402
import emit_descriptor  # noqa: E402

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_ERROR = 2

TREE = Path("build/check_tree")
PACKAGE_FILE = "datapackage.json"
PER_TABLE_SUFFIX = ".datapackage.json"


class Fault(Exception):
    """An input this script cannot check; exit 2."""


def read_json(path: Path, what: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Fault(f"cannot read {what} {path}: {exc}") from exc


def package_name(slug: str, filename: str) -> str:
    """The package a descriptor file is checked as: the slug, or a per-table file's own name."""
    return slug if filename == PACKAGE_FILE else filename.removesuffix(PER_TABLE_SUFFIX)


def parse_built(values: list[str]) -> list[tuple[Path, Path]]:
    pairs = []
    for value in values:
        descriptor, sep, parquet = value.partition("=")
        if not sep or not descriptor or not parquet:
            raise Fault(f"--built {value!r} is not DESCRIPTOR=PARQUET")
        pairs.append((Path(descriptor), Path(parquet)))
    return pairs


def lay_out(
    datasets_dir: Path, slug: str, bundle: dict[str, Any], built: list[tuple[Path, Path]], tree: Path
) -> tuple[list[str], dict[str, str]]:
    """Build the tree; return the built packages' names and each built path's local Parquet."""
    published = {
        filename: table for s, table, filename in emit_descriptor.protocol_tables(datasets_dir) if s == slug
    }
    if not published:
        raise Fault(f"{datasets_dir / slug / 'arcform.yaml'} publishes no table, so there is nothing to check")

    if tree.exists():
        shutil.rmtree(tree)
    emit_descriptor.write_tree(datasets_dir, bundle, tree, bypass_edge=False)
    datasets = tree / "datasets"

    names: list[str] = []
    read_from: dict[str, str] = {}
    laid: set[str] = set()
    for descriptor_path, parquet in built:
        filename = descriptor_path.name
        if filename not in published:
            raise Fault(
                f"--built {descriptor_path}: {slug} publishes no table with that descriptor; "
                f"its publish step names {', '.join(sorted(published))}"
            )
        if filename in laid:
            raise Fault(f"--built {descriptor_path} is given twice")
        laid.add(filename)
        if not parquet.is_file():
            raise Fault(f"--built {descriptor_path}: no Parquet at {parquet}")
        document = read_json(descriptor_path, "the built descriptor")
        resources = document.get("resources") if isinstance(document, dict) else None
        if not isinstance(resources, list) or len(resources) != 1 or not isinstance(resources[0], dict):
            raise Fault(f"--built {descriptor_path}: a published table's descriptor declares one resource")
        declared = resources[0].get("path")
        if not isinstance(declared, str) or not declared:
            raise Fault(f"--built {descriptor_path}: its resource declares no path")
        if declared in read_from:
            raise Fault(f"--built {descriptor_path}: another built descriptor also declares {declared}")
        read_from[declared] = str(parquet.resolve())
        emit_descriptor.write_json(datasets / slug / filename, document)
        names.append(package_name(slug, filename))

    # One `<package>/datapackage.json` per table, for the checks that read no other name.
    packages = tree / "packages"
    sources: dict[str, Path] = {}
    for path in sorted(datasets.glob(f"*/{PACKAGE_FILE}")):
        sources[path.parent.name] = path
    for s, _, filename in emit_descriptor.protocol_tables(datasets_dir):
        path = datasets / s / filename
        if filename == PACKAGE_FILE or not path.exists():
            continue
        name = package_name(s, filename)
        if name in sources:
            raise Fault(
                f"{s}/{filename} is checked as package {name!r}, and {sources[name].relative_to(datasets)} "
                f"is already that package"
            )
        sources[name] = path
    for name, path in sources.items():
        (packages / name).mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, packages / name / PACKAGE_FILE)
    return names, read_from


def run(name: str, main: Callable[[list[str]], int], argv: list[str], findings: Path) -> dict[str, Any]:
    """One check, in-process, with its output captured and its findings read back."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try:
            status = main([*argv, "--json", str(findings)])
        except SystemExit as exc:  # argparse refusing its arguments
            status = exc.code if isinstance(exc.code, int) else EXIT_ERROR
    result: dict[str, Any] = {
        "check": name,
        "passed": status == EXIT_OK,
        "exit": status,
        "output": out.getvalue(),
        "findings": None,
    }
    if findings.exists():
        result["findings"] = json.loads(findings.read_text(encoding="utf-8"))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--datasets-dir", type=Path, required=True, help="the repository's datasets/")
    parser.add_argument("--slug", required=True, help="the Protocol whose tables were built")
    parser.add_argument("--lake-bundle", type=Path, required=True, help="emit_descriptor.py --all --bundle output")
    parser.add_argument(
        "--built", action="append", required=True, metavar="DESCRIPTOR=PARQUET", help="a built table (repeatable)"
    )
    parser.add_argument("--json", dest="json_out", type=Path, default=None)
    args = parser.parse_args(argv)

    try:
        built = parse_built(args.built)
        bundle = read_json(args.lake_bundle, "the lake bundle")
        if not isinstance(bundle, dict):
            raise Fault(f"{args.lake_bundle} is not a JSON object of table name to descriptor")
        names, read_from = lay_out(args.datasets_dir, args.slug, bundle, built, TREE)
    except Fault as exc:
        print(f"check before publish: {exc}", file=sys.stderr)
        return EXIT_ERROR

    datasets = TREE / "datasets"
    packages = TREE / "packages"
    only = [arg for name in names for arg in ("--only", name)]
    checks = [
        run(
            "label agreement",
            check_label_agreement.main,
            ["--strict", "--datasets-dir", str(datasets)],
            TREE / "label_agreement.json",
        ),
        run(
            "frictionless",
            check_frictionless.main,
            ["--datasets-dir", str(packages), "--rejections", str(datasets / "frictionless-rejections.json"), *only],
            TREE / "frictionless.json",
        ),
        run(
            "descriptors",
            check_descriptors.main,
            [
                "--datasets-dir",
                str(packages),
                *only,
                "--require-self-description",
                "--no-catalogue",
                "--notice-table",
                str(datasets / "licence-notices.json"),
                *(arg for declared, local in read_from.items() for arg in ("--read-from", f"{declared}={local}")),
            ],
            TREE / "descriptors.json",
        ),
    ]

    passed = [check for check in checks if check["passed"]]
    for check in checks:
        if check["passed"]:
            print(f"PASSED  {check['check']}")
    for check in checks:
        if not check["passed"]:
            print(f"FAILED  {check['check']} (exit {check['exit']})")
            for line in check["output"].rstrip("\n").splitlines():
                print(f"    {line}")
    print(
        f"check before publish: {args.slug} ({', '.join(names)}): "
        f"{len(passed)} of {len(checks)} check(s) passed"
    )

    if args.json_out:
        emit_descriptor.write_json(
            args.json_out,
            {"slug": args.slug, "packages": names, "passed": len(passed) == len(checks), "checks": checks},
        )
    return EXIT_OK if len(passed) == len(checks) else EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
