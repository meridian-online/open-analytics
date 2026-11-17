#!/usr/bin/env python3
"""Emit a table's datapackage.org descriptor from the open lake.

The lake's `meta.descriptors` holds each table's descriptor without the volatile `created`
and without its resource's `bytes` and `hash`. The lake's `manifest.json` holds those
three for each published table (`published`, `bytes`, `sha256`). This script joins them
back into the full descriptor, so no Protocol has to commit one.

    emit_descriptor.py <schema.table> [--out FILE]
    emit_descriptor.py --all --bundle FILE
    emit_descriptor.py --all --tree DIR [--bypass-edge]

`--tree` copies `datasets/` (every `build/` skipped) to DIR/datasets/ and writes each
published table's descriptor where its Protocol names it. `--bypass-edge` appends
`?v=<first 16 hex of the hash>` to each http resource path, so a reader behind the CDN
fetches the object this descriptor describes, not a cached older one. A Protocol table
the catalogue does not hold yet is left out and named on stderr.

    --catalog   a ducklake URL or path (default: the public catalogue)
    --manifest  a manifest.json URL or path (default: the public manifest)

Exit codes: 0 done; 1 a table has no manifest entry, is not in the lake, or the
catalogue or manifest cannot be read.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import urllib.request
from pathlib import Path

import duckdb
import yaml

PUBLIC_CATALOG = "https://openlake.meridian.online/catalog/open.ducklake"
PUBLIC_MANIFEST = "https://openlake.meridian.online/manifest.json"
DATASETS = Path(__file__).resolve().parent.parent / "datasets"


class Refused(Exception):
    """A condition the caller must fix; the message is printed and the exit status is 1."""


def read_stored(catalog: str) -> dict[str, dict]:
    """Every row of `meta.descriptors`: table name to the stored descriptor."""
    if catalog.startswith("ducklake:"):
        catalog = catalog[len("ducklake:") :]
    try:
        con = duckdb.connect()
        con.execute("INSTALL ducklake; INSTALL httpfs; LOAD ducklake; LOAD httpfs")
        con.execute(f"ATTACH 'ducklake:{catalog}' AS lake (READ_ONLY)")
        rows = con.execute(
            "SELECT table_name, descriptor::VARCHAR FROM lake.meta.descriptors ORDER BY 1"
        ).fetchall()
    except duckdb.Error as e:
        raise Refused(f"cannot read the catalogue {catalog}: {e}") from e
    return {name: json.loads(text) for name, text in rows}


def read_manifest(manifest: str) -> dict:
    try:
        if manifest.startswith(("http://", "https://")):
            # The CDN refuses urllib's default User-Agent.
            req = urllib.request.Request(manifest, headers={"User-Agent": "open-analytics-emit-descriptor"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        return json.loads(Path(manifest).read_text())
    except (OSError, ValueError) as e:
        raise Refused(f"cannot read the manifest {manifest}: {e}") from e


HEX64 = re.compile(r"[0-9a-f]{64}")


def full_descriptor(table: str, stored: dict, manifest: dict) -> dict:
    """`stored` plus `created`, and its sole resource's `bytes` and `hash`, from the manifest."""
    entry = manifest.get("datasets", {}).get(table.replace(".", "/"))
    if entry is None:
        raise Refused(f"{table}: the manifest has no entry for this table")
    for key in ("published", "bytes", "sha256"):
        if key not in entry:
            raise Refused(f"{table}: the manifest entry has no `{key}`")
    sha = entry["sha256"]
    if not isinstance(sha, str) or not HEX64.fullmatch(sha):
        raise Refused(
            f"{table}: the manifest sha256 must be 64 lowercase hex characters, found {sha!r}"
        )
    out = json.loads(json.dumps(stored))
    resources = out.get("resources") or []
    if len(resources) != 1:
        raise Refused(f"{table}: the stored descriptor has {len(resources)} resources, not one")
    out["created"] = entry["published"]
    resources[0]["bytes"] = entry["bytes"]
    resources[0]["hash"] = f"sha256:{sha}"
    return out


def load_descriptors(
    catalog: str, manifest: str, tables: set[str] | None = None
) -> dict[str, dict]:
    """Table name (`census.fips`) to its full descriptor, for `tables` or else every stored table.

    A table asked for that the lake does not store is left out; only a table that is
    returned needs a manifest entry. A table the manifest lists, among `tables`, with no
    stored descriptor is the lake contradicting itself and is refused.
    """
    stored = read_stored(catalog)
    m = read_manifest(manifest)
    chosen = set(stored) if tables is None else tables & set(stored)
    if tables is not None:
        published = {k.replace("/", ".", 1) for k in m.get("datasets", {})}
        for t in sorted((tables - set(stored)) & published):
            raise Refused(f"{t}: the manifest lists this table but meta.descriptors has no row for it")
    return {t: full_descriptor(t, stored[t], m) for t in sorted(chosen)}


def protocol_tables(datasets_dir: Path) -> list[tuple[str, str, str]]:
    """`(slug, table_name, descriptor_filename)` from each Protocol's `ducklake_write@1` step."""
    found = []
    for manifest in sorted(Path(datasets_dir).glob("*/arcform.yaml")):
        doc = yaml.safe_load(manifest.read_text()) or {}
        for step in doc.get("steps") or []:
            if step.get("op") != "ducklake_write@1":
                continue
            for t in (step.get("with") or {}).get("tables") or []:
                found.append((manifest.parent.name, t["table"], Path(t["descriptor"]).name))
    return found


def encode(value: object) -> str:
    """The one JSON encoding every output mode uses."""
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(encode(value), encoding="utf-8")


def write_bundle(descriptors: dict[str, dict], out: Path) -> None:
    write_json(out, descriptors)


def write_tree(
    datasets_dir: Path, descriptors: dict[str, dict], out: Path, bypass_edge: bool
) -> None:
    dest = out / "datasets"
    root = Path(datasets_dir).resolve()
    publishing = {slug for slug, _, _ in protocol_tables(datasets_dir)}
    named = {(slug, filename) for slug, _, filename in protocol_tables(datasets_dir)}

    def ignore(directory: str, names: list[str]) -> set[str]:
        skip = {n for n in names if n == "build"}
        d = Path(directory).resolve()
        if d.parent == root and d.name in publishing:
            skip |= {
                n
                for n in names
                if n.endswith("datapackage.json") or (d.name, n) in named
            }
        return skip

    shutil.copytree(datasets_dir, dest, ignore=ignore, dirs_exist_ok=True)
    for slug, table, filename in protocol_tables(datasets_dir):
        d = descriptors.get(table)
        if d is None:
            continue
        if bypass_edge:
            d = json.loads(json.dumps(d))
            for r in d["resources"]:
                if str(r.get("path", "")).startswith(("http://", "https://")):
                    r["path"] += "?v=" + r["hash"].removeprefix("sha256:")[:16]
        write_json(dest / slug / filename, d)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("table", nargs="?", help="schema.table, e.g. census.fips")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--bundle", type=Path)
    ap.add_argument("--tree", type=Path)
    ap.add_argument("--bypass-edge", action="store_true")
    ap.add_argument("--catalog", default=PUBLIC_CATALOG)
    ap.add_argument("--manifest", default=PUBLIC_MANIFEST)
    ap.add_argument("--datasets", type=Path, default=DATASETS)
    args = ap.parse_args()
    if bool(args.table) == args.all:
        ap.error("give a table, or --all")
    if args.all and not (args.bundle or args.tree):
        ap.error("--all needs --bundle or --tree")

    try:
        if args.table:
            descriptors = load_descriptors(args.catalog, args.manifest, {args.table})
            if args.table not in descriptors:
                raise Refused(f"{args.table}: not in the lake's meta.descriptors")
            text = encode(descriptors[args.table])
            if args.out:
                args.out.write_text(text, encoding="utf-8")
            else:
                sys.stdout.write(text)
            return 0
        wanted = {t for _, t, _ in protocol_tables(args.datasets)}
        descriptors = load_descriptors(args.catalog, args.manifest, wanted)
        for table in sorted(wanted - set(descriptors)):
            print(f"not yet published: {table}", file=sys.stderr)
        if args.bundle:
            write_bundle(descriptors, args.bundle)
        if args.tree:
            write_tree(args.datasets, descriptors, args.tree, args.bypass_edge)
    except Refused as e:
        print(f"emit_descriptor: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
