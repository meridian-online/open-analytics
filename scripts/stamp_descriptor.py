#!/usr/bin/env python3
# /// script
# requires-python = "==3.12.*"
# ///
# The interpreter this script runs under, for anything that runs it as a `uv` script.
# duckdb is NOT declared here on purpose: the manifest step that runs this under
# `op: uv@1` pins it in `deps:`, and `uv run --script` installs what this block names
# as well as what the step names, so a second copy would be a second thing to keep in
# step.
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

**The data pages do not move.** The `order_by` clauses that make an export
byte-reproducible would stop meaning anything if the stamp re-encoded the rows. So
the stamp does not rewrite the file through any engine: it copies everything before
the footer byte for byte and rewrites only the footer's key/value map, leaving the
rest of the footer — schema, row groups, `created_by` — as the writer wrote it.
Everything before the footer is then compared byte for byte between the input and
the stamped output, and a stamp that moved a single data byte is refused with the
original left in place. DuckDB reads the stamped footer back before it replaces
anything, so a footer this script wrote wrong never lands.

It used to rewrite the file with DuckDB's `COPY … KV_METADATA`. That only kept the
pages where they were when the stamp's DuckDB was the version that wrote the file
and the layout it was given matched the writer's, and arc 0.2.2 writes with DuckDB
2.0 while this step pins the reader's 1.5.5, so every such run was refused.

**The embedded copy carries no `bytes` and no `hash`.** A file cannot state its
own size or its own digest, because stating them changes them — write the size in
and the file is longer than the number just written. Those two fields stay in the
repository copy, which is measured against the finished object; the embedded copy
is the repository copy with those two keys removed from each resource and `created`
removed from the package (below), and `check_descriptors.py` holds it to that.

That is why `stamp` also rewrites the descriptor's `bytes` and `hash`: stamping
lengthens the file, so the figures `describe` measured off the unstamped build are
stale the instant the stamp lands. Stamping and re-measuring are one act here for
the same reason uploading and declaring are one act in `publish_dataset.py`.

**The embedded copy carries no `created` either.** `describe` stamps `created` from
the wall clock, so a file that embeds it is a different file on every publish even
when its data pages are byte-identical, and an analyst who pinned the `hash` out of
`datapackage.json` finds the pin stale for no reason the run can give. `created` is
the third fact a file cannot state about itself without changing it, and the only one
of the three that moves when nothing else does.

In the repository copy, beside the object, `created` means *when this data first
appeared*, not *when this run happened*. With `--carry-created`, a stamp that finds
the same data pages already published at its output keeps the `created` that
descriptor states; a stamp over different pages takes the fresh one `describe` wrote.

    stamp_descriptor.py stamp --descriptor datapackage.json --parquet build/x.parquet
    stamp_descriptor.py stamp --descriptor stage/measured.json --parquet stage/x.parquet
        --out build/x.parquet --out-descriptor datapackage.json --carry-created
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

# Package-level keys the embedded copy must not carry either: they state when the
# descriptor was written, which changes on a publish that changed nothing else, and so
# would change the file that states it. They stay in the repository copy.
WRITTEN_AT = ("created",)

CHUNK = 1 << 20
PARQUET_MAGIC = b"PAR1"


class StampError(Exception):
    """The operation could not be performed. Never a verdict about the data."""


# ─────────────────────────────────────────────────────────── the embedded form


def embedded_form(document: dict[str, Any]) -> dict[str, Any]:
    """The descriptor as it travels inside the file: every resource loses `bytes` and `hash`, and the package loses `created`."""
    if not isinstance(document, dict):
        raise StampError("descriptor is not a JSON object")
    copy = json.loads(json.dumps(document))
    for key in WRITTEN_AT:
        copy.pop(key, None)
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


# ─────────────────────────────────────────────────────────────── the footer
#
# The footer is a Thrift `FileMetaData` in the compact protocol. Its key/value map is
# field 5, a list of `KeyValue {1: string key, 2: optional string value}`. The stamp
# rewrites that one field and copies every other field's bytes as they were, so the
# schema, the row groups, the column-chunk offsets and `created_by` are the writer's,
# whatever wrote the file and whatever DuckDB this runs under.

KEY_VALUE_METADATA = 5

# Compact-protocol type ids.
T_STOP, T_TRUE, T_FALSE, T_BYTE, T_I16, T_I32, T_I64, T_DOUBLE = 0, 1, 2, 3, 4, 5, 6, 7
T_BINARY, T_LIST, T_SET, T_MAP, T_STRUCT = 8, 9, 10, 11, 12


class FooterError(Exception):
    """The footer is not compact-protocol Thrift this parser can walk."""


def _varint(buf: bytes, pos: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        if pos >= len(buf):
            raise FooterError("a varint runs past the end of the footer")
        byte = buf[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, pos
        shift += 7
        if shift > 63:
            raise FooterError("a varint is longer than 64 bits")


def _write_varint(value: int) -> bytes:
    out = bytearray()
    while True:
        if value < 0x80:
            out.append(value)
            return bytes(out)
        out.append((value & 0x7F) | 0x80)
        value >>= 7


def _zigzag(value: int) -> int:
    return (value << 1) ^ (value >> 63)


def _unzigzag(value: int) -> int:
    return (value >> 1) ^ -(value & 1)


def _field_header(buf: bytes, pos: int, last_id: int) -> tuple[int, int, int]:
    """(field type, field id, position after the header); type T_STOP ends the struct."""
    if pos >= len(buf):
        raise FooterError("a struct runs past the end of the footer")
    byte = buf[pos]
    pos += 1
    kind = byte & 0x0F
    if kind == T_STOP:
        return T_STOP, 0, pos
    delta = byte >> 4
    if delta:
        return kind, last_id + delta, pos
    raw, pos = _varint(buf, pos)
    return kind, _unzigzag(raw), pos


def _skip(buf: bytes, pos: int, kind: int, in_field: bool = True) -> int:
    """The position after one value of `kind` starting at `pos`."""
    if kind in (T_TRUE, T_FALSE):
        # In a field header the value is the type itself; in a list it is one byte.
        return pos if in_field else pos + 1
    if kind == T_BYTE:
        return pos + 1
    if kind in (T_I16, T_I32, T_I64):
        return _varint(buf, pos)[1]
    if kind == T_DOUBLE:
        return pos + 8
    if kind == T_BINARY:
        length, pos = _varint(buf, pos)
        return pos + length
    if kind in (T_LIST, T_SET):
        if pos >= len(buf):
            raise FooterError("a list runs past the end of the footer")
        header = buf[pos]
        pos += 1
        size, element = header >> 4, header & 0x0F
        if size == 15:
            size, pos = _varint(buf, pos)
        for _ in range(size):
            pos = _skip(buf, pos, element, in_field=False)
        return pos
    if kind == T_MAP:
        size, pos = _varint(buf, pos)
        if size:
            if pos >= len(buf):
                raise FooterError("a map runs past the end of the footer")
            types = buf[pos]
            pos += 1
            for _ in range(size):
                pos = _skip(buf, pos, types >> 4, in_field=False)
                pos = _skip(buf, pos, types & 0x0F, in_field=False)
        return pos
    if kind == T_STRUCT:
        last_id = 0
        while True:
            field_kind, last_id, pos = _field_header(buf, pos, last_id)
            if field_kind == T_STOP:
                return pos
            pos = _skip(buf, pos, field_kind)
    raise FooterError(f"type {kind} is not a compact-protocol type")


def _fields(buf: bytes) -> list[tuple[int, int, bytes]]:
    """(field id, type, value bytes) for each top-level field, in file order.

    Refuses a footer it cannot walk to exactly its declared end: bytes it did not
    account for are bytes it would not know how to carry.
    """
    fields: list[tuple[int, int, bytes]] = []
    pos = last_id = 0
    while True:
        kind, last_id, after_header = _field_header(buf, pos, last_id)
        if kind == T_STOP:
            if after_header != len(buf):
                raise FooterError(f"{len(buf) - after_header} bytes follow the end of the metadata")
            return fields
        end = _skip(buf, after_header, kind)
        if end > len(buf):
            raise FooterError("a field runs past the end of the footer")
        fields.append((last_id, kind, buf[after_header:end]))
        pos = end


def _key_value_entries(value: bytes) -> list[tuple[bytes, bytes]]:
    """(key, the entry's raw struct bytes) for each entry of a key/value list."""
    if not value:
        raise FooterError("the key/value list is empty bytes")
    header = value[0]
    size, element = header >> 4, header & 0x0F
    pos = 1
    if size == 15:
        size, pos = _varint(value, pos)
    if size and element != T_STRUCT:
        raise FooterError(f"the key/value list holds type {element}, not structs")
    entries: list[tuple[bytes, bytes]] = []
    for _ in range(size):
        start, key, last_id = pos, None, 0
        while True:
            kind, last_id, pos = _field_header(value, pos, last_id)
            if kind == T_STOP:
                break
            if last_id == 1 and kind == T_BINARY:
                length, at = _varint(value, pos)
                key = value[at : at + length]
            pos = _skip(value, pos, kind)
        if key is None:
            raise FooterError("a key/value entry has no key")
        entries.append((key, value[start:pos]))
    if pos != len(value):
        raise FooterError("the key/value list does not end where its entries do")
    return entries


def _binary_field(field_id: int, last_id: int, data: bytes) -> bytes:
    return bytes([((field_id - last_id) << 4) | T_BINARY]) + _write_varint(len(data)) + data


def with_key_value(footer: bytes, key: str, text: str) -> bytes:
    """`footer` with `key` set to `text` in its key/value map, and nothing else changed.

    An entry already under `key` is replaced and the new one goes last, so stamping a
    stamped file writes the same bytes again. Every other entry keeps its place.
    """
    try:
        fields = _fields(footer)
        current = next((value for fid, _, value in fields if fid == KEY_VALUE_METADATA), None)
        entries = _key_value_entries(current) if current is not None else []
    except FooterError as exc:
        raise StampError(f"cannot parse the footer: {exc}") from exc

    encoded_key = key.encode("utf-8")
    kept = [raw for entry_key, raw in entries if entry_key != encoded_key]
    kept.append(_binary_field(1, 0, encoded_key) + _binary_field(2, 1, text.encode("utf-8")) + b"\x00")
    header = bytes([(len(kept) << 4) | T_STRUCT]) if len(kept) < 15 else bytes([0xF0 | T_STRUCT]) + _write_varint(len(kept))
    new_value = header + b"".join(kept)

    rebuilt = [(fid, kind, value) for fid, kind, value in fields if fid != KEY_VALUE_METADATA]
    rebuilt.append((KEY_VALUE_METADATA, T_LIST, new_value))
    rebuilt.sort(key=lambda field: field[0])

    out = bytearray()
    last_id = 0
    for fid, kind, value in rebuilt:
        delta = fid - last_id
        if 0 < delta <= 15:
            out.append((delta << 4) | kind)
        else:
            out.append(kind)
            out += _write_varint(_zigzag(fid))
        out += value
        last_id = fid
    out.append(T_STOP)
    return bytes(out)


def stamp_parquet(con, source: Path, destination: Path, text: str) -> None:
    """Write `source` to `destination` with `text` in the footer, data untouched.

    Everything before the footer is copied byte for byte and only the footer's
    key/value map is rewritten, so no engine re-encodes a page. `con` is unused here
    and kept so the stamp's steps share one signature; DuckDB reads the result back.
    """
    del con
    offset = footer_offset(source)
    try:
        size = source.stat().st_size
        with source.open("rb") as handle:
            handle.seek(offset)
            footer = handle.read(size - 8 - offset)
    except OSError as exc:
        raise StampError(f"cannot read {source}: {exc}") from exc
    new_footer = with_key_value(footer, DESCRIPTOR_KEY, text)
    try:
        with source.open("rb") as reader, destination.open("wb") as writer:
            remaining = offset
            while remaining:
                chunk = reader.read(min(CHUNK, remaining))
                if not chunk:
                    raise StampError(f"{source} ended before its footer")
                writer.write(chunk)
                remaining -= len(chunk)
            writer.write(new_footer)
            writer.write(len(new_footer).to_bytes(4, "little"))
            writer.write(PARQUET_MAGIC)
    except OSError as exc:
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


def carried_created(parquet: Path, descriptor: Path, digest: str) -> str | None:
    """The `created` already published for these data pages, or None when it cannot be vouched for.

    The previous output vouches for it only when its pages are byte-identical to
    `digest` and its descriptor states a `created`. Anything else — no previous output,
    an unreadable one, different pages — returns None, and the caller keeps the fresh
    value, which is the truthful one: this data first appeared now.
    """
    if not parquet.exists() or not descriptor.exists():
        return None
    try:
        if data_digest(parquet) != digest:
            return None
        created = load_descriptor(descriptor).get("created")
    except StampError:
        return None
    return created if isinstance(created, str) and created else None


# ──────────────────────────────────────────────────────────────── subcommands


def command_stamp(args: argparse.Namespace) -> int:
    descriptor_path = Path(args.descriptor)
    parquet = Path(args.parquet)
    if not parquet.exists():
        raise StampError(f"{parquet} does not exist")

    document = load_descriptor(descriptor_path)
    resource = pick_resource(document, descriptor_path, args.resource)

    before = data_digest(parquet)
    con = connect()
    destination = Path(args.out) if args.out else parquet
    out_descriptor = Path(args.out_descriptor) if args.out_descriptor else descriptor_path
    if args.carry_created:
        carried = carried_created(destination, out_descriptor, before)
        if carried is not None:
            document["created"] = carried
    text = embedded_text(document)
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
    if not args.keep_declared_bytes or args.out_descriptor:
        save_descriptor(out_descriptor, document)

    print(
        f"stamp: {destination} carries {DESCRIPTOR_KEY} ({len(text.encode('utf-8')):,} bytes in the footer); "
        f"{out_descriptor} declares {size:,} bytes, {digest}",
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
        "--out-descriptor",
        help="write the descriptor, with its re-measured bytes/hash, here instead of over --descriptor; "
        "a step that reads the descriptor and writes it back to the same path rewrites its own input",
    )
    stamp.add_argument(
        "--carry-created",
        action="store_true",
        help="keep the `created` of the descriptor already at --out-descriptor when the Parquet already at "
        "--out holds the same data pages: `created` then means when this data first appeared",
    )
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
