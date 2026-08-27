#!/usr/bin/env python3
# /// script
# requires-python = "==3.12.*"
# ///
# The interpreter this script runs under, for anything that runs it as a `uv` script.
# duckdb is NOT declared here on purpose: the manifest step that runs this under
# `op: uv@1` pins it in `deps:`, and `uv run --script` installs what this block names
# as well as what the step names, so a second copy would be a second thing to keep in
# step. The three manifests that still invoke this through `python3` are unaffected —
# these are comments.
"""Put a dataset's descriptor inside the dataset, and read it back out of one URL.

A `datapackage.json` says what a Parquet's columns are, what they may contain,
where the data came from and under what licence. Until now it said all of that
from a second file, in a source repository, that a consumer holding the object's
URL has no way to find and no reason to know exists. Ask the object and it could
not answer.

Parquet's footer carries an arbitrary key/value map, so the descriptor can travel
**inside the file it describes**. `stamp` writes it there under the key
`datapackage.json`; `read` takes it back out of a URL and prints it. Nothing in
`read` touches a repository, a sibling file or a registry — its only argument is
the object's own URL, and `scripts/test_self_description_needs_no_repository.py`
runs it with the repository host unreachable to show that.

Two properties are load-bearing and are asserted rather than assumed.

**The data pages do not move.** A stamp rewrites the file through DuckDB, so the
worry is that it re-encodes the rows and the `order_by` clauses that make an
export byte-reproducible stop meaning anything. Everything before the footer is
compared byte for byte between the input and the stamped output, and a stamp that
moved a single data byte is refused with the original left in place.

The pages CAN move, which is why that comparison is a guard and not a formality.
The rewrite has to reproduce the layout the file was written with, and the layout
is not in this script — it is in whatever wrote the file. `edgar_gleif` exports at
`row_group_size: 50000`, which DuckDB rounds to 51,200; a rewrite at DuckDB's
default 122,880 re-groups every page and the guard refuses the whole run. So the
layout is READ OFF THE SOURCE FILE — row-group size and codec both — rather than
named here. Anything this script hardcodes about the shape of a file it did not
write is a latent version of that failure.

**The embedded copy carries no `bytes` and no `hash`.** A file cannot state its
own size or its own digest, because stating them changes them — write the size in
and the file is longer than the number just written. Those two fields stay in the
repository copy, which is measured against the finished object; the embedded copy
is the repository copy with exactly those two keys removed from each resource, and
`check_descriptors.py` holds it to that.

That is why `stamp` also rewrites the descriptor's `bytes` and `hash`: stamping
lengthens the file, so the figures `describe` measured off the unstamped build are
stale the instant the stamp lands. Stamping and re-measuring are one act here for
the same reason uploading and declaring are one act in `publish_dataset.py`.

    stamp_descriptor.py stamp --descriptor datapackage.json --parquet build/x.parquet
    stamp_descriptor.py read  https://openlake.meridian.online/naics.parquet

Exit codes are distinct on purpose, because a status alone cannot tell a refusal
apart from a crash:

  0  the object carries its description, and it was written or read
  1  the object carries no description, or the stamp did not survive readback
  2  the operation could not be performed — unreadable file, malformed descriptor,
     missing DuckDB. Never a verdict about the data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

EXIT_OK = 0
EXIT_DISAGREEMENT = 1
EXIT_ERROR = 2

# The footer key the descriptor travels under. It is the filename a consumer
# already knows from the Frictionless spec, so `decode(key) = 'datapackage.json'`
# reads as itself with nothing to look up.
DESCRIPTOR_KEY = "datapackage.json"

# Resource keys the embedded copy must not carry, because they describe the
# container rather than the contents and writing them changes what they measure.
SELF_REFERENTIAL = ("bytes", "hash")

CHUNK = 1 << 20
PARQUET_MAGIC = b"PAR1"


class StampError(Exception):
    """The operation could not be performed. Never a verdict about the data."""


# ─────────────────────────────────────────────────────────── the embedded form


def embedded_form(document: dict[str, Any]) -> dict[str, Any]:
    """The descriptor as it travels inside the file: every resource loses `bytes` and `hash`."""
    if not isinstance(document, dict):
        raise StampError("descriptor is not a JSON object")
    copy = json.loads(json.dumps(document))
    resources = copy.get("resources")
    if not isinstance(resources, list) or not resources:
        raise StampError("descriptor declares no resources")
    for resource in resources:
        if not isinstance(resource, dict):
            raise StampError("descriptor declares a resource that is not an object")
        for key in SELF_REFERENTIAL:
            resource.pop(key, None)
    return copy


def embedded_text(document: dict[str, Any]) -> str:
    """The exact bytes stamped into the footer.

    2-space indent, unescaped non-ASCII and a trailing newline — the formatting
    `describe` and `publish_dataset.py` already write, so what comes back out of a
    file diffs against the repository copy line for line rather than as one blob.
    """
    return json.dumps(embedded_form(document), indent=2, ensure_ascii=False) + "\n"


# ──────────────────────────────────────────────────────────────── the bytes


def footer_offset(path: Path) -> int:
    """Where the Parquet footer starts: file length minus the trailer minus the footer.

    A Parquet file is `PAR1` + data pages + footer + 4-byte footer length + `PAR1`.
    Everything before the returned offset is data, and a stamp must leave it alone.
    """
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size < 12:
                raise StampError(f"{path} is {size} bytes — too short to be a Parquet file")
            handle.seek(0)
            if handle.read(4) != PARQUET_MAGIC:
                raise StampError(f"{path} does not start with the Parquet magic")
            handle.seek(size - 8)
            trailer = handle.read(8)
    except OSError as exc:
        raise StampError(f"cannot read {path}: {exc}") from exc
    if trailer[4:] != PARQUET_MAGIC:
        raise StampError(f"{path} does not end with the Parquet magic")
    footer_length = int.from_bytes(trailer[:4], "little")
    offset = size - 8 - footer_length
    if offset < 4:
        raise StampError(f"{path} declares a {footer_length}-byte footer that does not fit in {size} bytes")
    return offset


def data_digest(path: Path) -> str:
    """SHA-256 of everything before the footer — the rows, the encodings, the order."""
    end = footer_offset(path)
    digest = hashlib.sha256()
    read = 0
    try:
        with path.open("rb") as handle:
            while read < end:
                chunk = handle.read(min(CHUNK, end - read))
                if not chunk:
                    break
                digest.update(chunk)
                read += len(chunk)
    except OSError as exc:
        raise StampError(f"cannot read {path}: {exc}") from exc
    return digest.hexdigest()


def file_measurement(path: Path) -> tuple[int, str]:
    """(size, "sha256:<hex>") of the whole file, from one pass."""
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(CHUNK)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
    except OSError as exc:
        raise StampError(f"cannot read {path}: {exc}") from exc
    return size, "sha256:" + digest.hexdigest()


# ──────────────────────────────────────────────────────────────────── DuckDB


def connect():
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover - environment, not logic
        raise StampError("the duckdb Python package is required") from exc
    return duckdb.connect()


def enable_remote_reads(con) -> None:
    """Make https readable without letting DuckDB fetch anything else.

    `LOAD` first and `INSTALL` only on failure, so an already-installed httpfs
    costs no request — which is what lets `read` run with every host but the
    object's own unreachable. Autoinstall is turned off for the same reason: a
    silent extension download is a read outside the object URL.
    """
    try:
        con.execute("SET autoinstall_known_extensions = false")
    except Exception:  # pragma: no cover - older builds without the setting
        pass
    try:
        con.execute("LOAD httpfs")
        return
    except Exception:
        pass
    try:
        con.execute("SET autoinstall_known_extensions = true")
        con.execute("INSTALL httpfs")
        con.execute("LOAD httpfs")
    except Exception as exc:
        raise StampError(f"cannot enable httpfs, so {DESCRIPTOR_KEY} cannot be read over https: {exc}") from exc


def is_remote(target: str) -> bool:
    return target.lower().startswith(("http://", "https://", "s3://", "gs://", "r2://", "az://"))


def read_self_description(con, target: str) -> str | None:
    """The descriptor the object carries, or None when it carries none.

    One statement, one argument: the object's own URL. No sibling file is opened
    and no repository is consulted, which is the whole point of the mechanism.
    """
    if is_remote(target):
        enable_remote_reads(con)
    try:
        rows = con.execute(
            "SELECT decode(value) FROM parquet_kv_metadata(?) WHERE decode(key) = ?",
            [target, DESCRIPTOR_KEY],
        ).fetchall()
    except Exception as exc:
        raise StampError(f"cannot read the footer of {target}: {exc}") from exc
    if not rows:
        return None
    if len(rows) > 1:
        raise StampError(f"{target} carries {len(rows)} entries under {DESCRIPTOR_KEY!r}")
    return rows[0][0]


@dataclass(frozen=True)
class Layout:
    """How a Parquet file was written: rows per row group, and the page codec.

    Both are read off the file about to be rewritten rather than named here, with
    one exception the code states where it happens: a file with no column chunks
    has no codec to read, so the rewrite falls back to zstd. A rewrite that took
    DuckDB's COPY defaults instead — 122,880 rows, and SNAPPY when no COMPRESSION
    is given — would re-group and re-compress every page of any file written with
    anything else, which is not a footer change and is refused.

    `max()` over the row groups is right for a file DuckDB wrote, and only for
    one. DuckDB rounds a declared size UP to a multiple of its 2048-row vector
    size, so the size it writes is already a fixed point and feeding it back is
    idempotent. A Parquet from elsewhere with a non-uniform layout has no single
    ROW_GROUP_SIZE that reproduces it, so this refuses with the data-pages
    message rather than stamping it. That is the safe direction and it will still
    look like a bug to whoever meets it.
    """

    row_group_size: int | None
    compression: str


def source_layout(con, source: Path) -> Layout:
    """Read the row-group size and codec out of `source`'s own footer.

    `max(row_group_num_rows)` rather than the first row group's: the last one is a
    remainder, and DuckDB rounds a declared size up to a multiple of its vector
    size (a declared 50,000 is written as 51,200), so the widest group is the size
    that was actually in force.
    """
    try:
        row = con.execute(
            "SELECT max(row_group_num_rows), list(DISTINCT compression) FROM parquet_metadata(?)",
            [str(source)],
        ).fetchone()
    except Exception as exc:
        raise StampError(f"cannot read the layout of {source}: {exc}") from exc

    row_group_size, codecs = (row or (None, None))
    codecs = sorted(codecs or [])
    if len(codecs) > 1:
        raise StampError(
            f"{source} mixes {len(codecs)} Parquet codecs ({', '.join(codecs)}). One COPY writes "
            "one codec, so this file was not written by one, and rewriting it would re-compress "
            "pages this step must leave alone."
        )
    # An empty file has no column chunks and so no observable codec. It also has no
    # data pages to move, so the choice cannot change the bytes being protected.
    compression = codecs[0].lower() if codecs else "zstd"
    return Layout(int(row_group_size) if row_group_size else None, compression)


def stamp_parquet(con, source: Path, destination: Path, text: str) -> None:
    """Write `source` to `destination` with `text` in the footer, data untouched."""
    literal = text.replace("'", "''")
    key = DESCRIPTOR_KEY.replace("'", "''")
    layout = source_layout(con, source)
    options = ["FORMAT parquet", f"COMPRESSION {layout.compression}"]
    if layout.row_group_size:
        options.append(f"ROW_GROUP_SIZE {layout.row_group_size}")
    options.append(f"KV_METADATA {{'{key}': '{literal}'}}")
    sql = f"COPY (SELECT * FROM read_parquet(?)) TO '{destination}' ({', '.join(options)})"
    try:
        con.execute(sql, [str(source)])
    except Exception as exc:
        raise StampError(f"cannot stamp {source}: {exc}") from exc


# ─────────────────────────────────────────────────────────────── descriptors


def load_descriptor(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise StampError(f"{path}: cannot read descriptor: {exc}") from exc
    if not isinstance(document, dict):
        raise StampError(f"{path}: descriptor is not a JSON object")
    return document


def save_descriptor(path: Path, document: dict[str, Any]) -> None:
    body = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(path.parent), prefix=path.name + ".", suffix=".tmp", delete=False
    )
    try:
        with handle:
            handle.write(body)
        os.replace(handle.name, path)
    except OSError as exc:
        Path(handle.name).unlink(missing_ok=True)
        raise StampError(f"{path}: cannot write descriptor: {exc}") from exc


def pick_resource(document: dict[str, Any], path: Path, name: str | None) -> dict[str, Any]:
    resources = document.get("resources") or []
    if not resources:
        raise StampError(f"{path}: declares no resources")
    if name is None:
        if len(resources) > 1:
            names = ", ".join(sorted(str(r.get("name")) for r in resources))
            raise StampError(f"{path}: declares {len(resources)} resources ({names}); name one with --resource")
        return resources[0]
    for resource in resources:
        if resource.get("name") == name:
            return resource
    raise StampError(f"{path}: declares no resource named {name!r}")


# ──────────────────────────────────────────────────────────────── subcommands


def command_stamp(args: argparse.Namespace) -> int:
    descriptor_path = Path(args.descriptor)
    parquet = Path(args.parquet)
    if not parquet.exists():
        raise StampError(f"{parquet} does not exist")

    document = load_descriptor(descriptor_path)
    resource = pick_resource(document, descriptor_path, args.resource)
    text = embedded_text(document)

    before = data_digest(parquet)
    con = connect()
    destination = Path(args.out) if args.out else parquet
    with tempfile.TemporaryDirectory(prefix="stamp-descriptor-", dir=str(destination.parent)) as scratch:
        staged = Path(scratch) / (destination.name + ".stamped")
        stamp_parquet(con, parquet, staged, text)

        after = data_digest(staged)
        if after != before:
            raise StampError(
                f"REFUSED: stamping {parquet} moved the data pages "
                f"(sha256 {before[:12]}… became {after[:12]}…). The stamp must change the footer "
                "and nothing else; the original is untouched."
            )
        readback = read_self_description(con, str(staged))
        if readback != text:
            print(
                f"stamp: the footer of {staged} did not read back as what was written",
                file=sys.stderr,
            )
            return EXIT_DISAGREEMENT
        os.replace(staged, destination)

    size, digest = file_measurement(destination)
    if not args.keep_declared_bytes:
        resource["bytes"] = size
        resource["hash"] = digest
        save_descriptor(descriptor_path, document)

    print(
        f"stamp: {destination} carries {DESCRIPTOR_KEY} ({len(text.encode('utf-8')):,} bytes in the footer); "
        f"{descriptor_path} declares {size:,} bytes, {digest}",
        file=sys.stderr,
    )
    return EXIT_OK


def command_read(args: argparse.Namespace) -> int:
    con = connect()
    text = read_self_description(con, args.target)
    if text is None:
        print(
            f"read: {args.target} carries no {DESCRIPTOR_KEY} in its footer — "
            "this object does not describe itself",
            file=sys.stderr,
        )
        return EXIT_DISAGREEMENT
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        print(
            f"read: {args.target} carries a {DESCRIPTOR_KEY} that is not JSON: {exc}",
            file=sys.stderr,
        )
        return EXIT_DISAGREEMENT
    sys.stdout.write(text if text.endswith("\n") else text + "\n")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    stamp = sub.add_parser("stamp", help="write a descriptor into its own Parquet's footer")
    stamp.add_argument("--descriptor", required=True, help="the datapackage.json to embed")
    stamp.add_argument("--parquet", required=True, help="the built Parquet to stamp")
    stamp.add_argument("--resource", help="which resource's bytes/hash to restamp (default: the only one)")
    stamp.add_argument("--out", help="write the stamped file here instead of in place")
    stamp.add_argument(
        "--keep-declared-bytes",
        action="store_true",
        help="do not rewrite the descriptor's bytes/hash — for stamping a copy that is not the published object",
    )
    stamp.set_defaults(handler=command_stamp)

    read = sub.add_parser("read", help="print the description an object carries, from its URL alone")
    read.add_argument("target", help="the object's URL or path — the only thing this reads")
    read.set_defaults(handler=command_read)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except StampError as exc:
        print(f"{args.command}: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
