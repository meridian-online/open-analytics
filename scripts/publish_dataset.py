#!/usr/bin/env python3
"""Upload a dataset's Parquet and declare its size and hash in the same act.

`datasets/<slug>/datapackage.json` tells a consumer how many bytes to expect at
`resources[].path` and what SHA-256 they should hash to. Those two figures were
written by one act — describing a local build — and the object was moved by a
different one — uploading it. Nothing tied them together, so a republish moved
the served object out from under a descriptor that still described the previous
one. Both `bytes` and `hash` on `edgar` were stale that way.

This is the seam that would make them one act. `publish` runs the uploader, reads
the object back **from the URL the descriptor itself declares**, and writes
`bytes` and `hash` from that read. It cannot upload to one place and declare
another, because it never takes the URL from the caller.

NOT YET WIRED. The pipeline that actually uploads these objects lives outside
this repository and does not call `publish`; the values in the checked-in
descriptors were written by `restamp`. Until that pipeline calls this, an object
can still move without its descriptor.

  publish   upload a local file, then declare what the endpoint serves back
  restamp   declare what the endpoint already serves, for an object published
            out of band before this seam existed
  verify    refuse when the descriptor and the endpoint disagree

`bytes` and `hash` are measured from a single pass over one stream, so they
cannot describe different bytes from each other. A short read is a refusal
rather than a smaller number: `HTTPResponse.read()` returns what arrived and
closes when a body is truncated, so a declared size taken from an interrupted
download would look entirely plausible. Completeness is decided against the
announced `Content-Length`, so a response that announces none is refused too —
there is no length to check a truncation against.

The uploader is injected, never built in — this repository holds no credentials
and names no bucket. Pass a command carrying `{file}` and `{url}`:

    publish_dataset.py publish --dataset edgar --file build/edgar.parquet \
        --uploader 'my-object-put --local {file} --to {url}'

Exit codes are distinct on purpose, because a status alone cannot tell a refusal
apart from a crash:

  0  the descriptor declares what the endpoint serves
  1  a disagreement, named on stdout with both figures
  2  the operation could not be completed — unreachable endpoint, failed
     uploader, malformed descriptor. Never a verdict about the data.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Iterable

EXIT_OK = 0
EXIT_DISAGREEMENT = 1
EXIT_ERROR = 2

CHUNK = 1 << 20
USER_AGENT = "meridian-open-analytics publish (+https://github.com/meridian-online/open-analytics)"

# A readback straight after an upload can reach an edge that has not caught up.
# That is a wait, not a disagreement, so the refusal is only reported once the
# endpoint has had these attempts to agree.
READBACK_ATTEMPTS = 3
READBACK_DELAY = 2.0


class PublishError(Exception):
    """The operation could not be completed. Never a verdict about the data."""


class Refusal(Exception):
    """The descriptor and the object disagree, and the descriptor was not written."""


@dataclass(frozen=True)
class Measurement:
    """A size and a digest taken from the same pass over the same bytes."""

    size: int
    digest: str  # "sha256:<hex>"

    def describe(self) -> str:
        return f"{self.size:,} bytes, {self.digest}"


def measure_stream(stream: BinaryIO) -> Measurement:
    digest = hashlib.sha256()
    size = 0
    while True:
        chunk = stream.read(CHUNK)
        if not chunk:
            break
        digest.update(chunk)
        size += len(chunk)
    return Measurement(size, "sha256:" + digest.hexdigest())


def measure_file(path: Path) -> Measurement:
    try:
        with path.open("rb") as handle:
            return measure_stream(handle)
    except OSError as exc:
        raise PublishError(f"cannot read {path}: {exc}") from exc


def measure_url(url: str, *, timeout: float = 600.0) -> Measurement:
    """Size and SHA-256 of what the endpoint serves, from one streamed GET.

    A body that stops short of its announced `Content-Length` raises rather than
    returning the short measurement — see the module docstring.
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            announced = response.headers.get("Content-Length")
            measured = measure_stream(response)
    except http.client.HTTPException as exc:  # IncompleteRead and its siblings
        raise PublishError(f"{url} delivered an incomplete response: {exc!r}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise PublishError(f"GET {url} failed: {exc}") from exc

    if announced is None or not announced.strip().isdigit():
        raise PublishError(
            f"{url} announced no usable Content-Length ({announced!r}); a complete response "
            "cannot be told from a truncated one, so there is nothing safe to declare"
        )
    expected = int(announced.strip())
    if expected != measured.size:
        raise PublishError(
            f"{url} delivered an incomplete response: {measured.size:,} bytes "
            f"of an announced {expected:,}"
        )
    return measured


def load_descriptor(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise PublishError(f"{path}: cannot read descriptor: {exc}") from exc
    if not isinstance(document, dict):
        raise PublishError(f"{path}: descriptor is not a JSON object")
    return document


def save_descriptor(path: Path, document: dict[str, Any]) -> None:
    """Rewrite the descriptor in place, whole or not at all.

    2-space indent, unescaped non-ASCII and a trailing newline reproduce what the
    describe step emits, so a stamp shows as two changed lines rather than a
    reformat of the file.
    """
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
        raise PublishError(f"{path}: cannot write descriptor: {exc}") from exc


def pick_resource(document: dict[str, Any], descriptor_path: Path, name: str | None) -> dict[str, Any]:
    resources = document.get("resources") or []
    if not resources:
        raise PublishError(f"{descriptor_path}: declares no resources")
    if name is None:
        if len(resources) > 1:
            names = ", ".join(sorted(str(r.get("name")) for r in resources))
            raise PublishError(f"{descriptor_path}: declares {len(resources)} resources ({names}); name one with --resource")
        return resources[0]
    for resource in resources:
        if resource.get("name") == name:
            return resource
    raise PublishError(f"{descriptor_path}: declares no resource named {name!r}")


def resource_url(resource: dict[str, Any], descriptor_path: Path) -> str:
    path = resource.get("path")
    if not path:
        raise PublishError(f"{descriptor_path}: resource {resource.get('name')!r} declares no path")
    if not str(path).lower().startswith(("http://", "https://")):
        raise PublishError(
            f"{descriptor_path}: resource {resource.get('name')!r} points at {path!r}, "
            "which is not an endpoint this seam can read back"
        )
    return str(path)


def declared_measurement(resource: dict[str, Any]) -> Measurement | None:
    size, digest = resource.get("bytes"), resource.get("hash")
    if size is None or digest is None:
        return None
    return Measurement(int(size), str(digest))


def stamp(resource: dict[str, Any], measured: Measurement) -> None:
    resource["bytes"] = measured.size
    resource["hash"] = measured.digest


def descriptor_for(datasets_dir: Path, slug: str) -> Path:
    path = datasets_dir / slug / "datapackage.json"
    if not path.is_file():
        raise PublishError(f"no descriptor at {path}")
    return path


def run_uploader(command: str, file: Path, url: str) -> None:
    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        raise PublishError(f"--uploader is not a parsable command line ({exc}): {command!r}") from exc
    if not tokens:
        raise PublishError("--uploader is empty")
    if not any("{file}" in token for token in tokens) or not any("{url}" in token for token in tokens):
        raise PublishError("--uploader must carry both {file} and {url}")
    argv = [token.replace("{file}", str(file)).replace("{url}", url) for token in tokens]

    print(f"publish: uploading {file} -> {url}")
    result = subprocess.run(argv, capture_output=True, text=True, check=False)
    if result.stdout.strip():
        print(result.stdout.rstrip())
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        raise PublishError(f"uploader exited {result.returncode}; the descriptor is unchanged")


def readback(url: str, *, expect: Measurement | None, attempts: int, delay: float) -> Measurement:
    """Measure the endpoint, allowing it to catch up with a just-completed upload."""
    last: Measurement | None = None
    for attempt in range(1, max(1, attempts) + 1):
        last = measure_url(url)
        if expect is None or last == expect:
            return last
        if attempt < attempts:
            print(f"publish: endpoint serves {last.describe()}, waiting for {expect.describe()}")
            time.sleep(delay)
    assert last is not None
    return last


# --------------------------------------------------------------------- commands


def command_publish(args: argparse.Namespace) -> int:
    descriptor_path = descriptor_for(args.datasets_dir, args.dataset)
    document = load_descriptor(descriptor_path)
    resource = pick_resource(document, descriptor_path, args.resource)
    url = resource_url(resource, descriptor_path)

    local_file = Path(args.file)
    if not local_file.is_file():
        raise PublishError(f"nothing to publish at {local_file}")
    local = measure_file(local_file)
    if local.size == 0:
        raise PublishError(f"{local_file} is empty; refusing to publish it")
    print(f"publish: {local_file} is {local.describe()}")

    run_uploader(args.uploader, local_file, url)

    served = readback(url, expect=local, attempts=args.readback_attempts, delay=args.readback_delay)
    if served != local:
        raise Refusal(
            f"{url} serves {served.describe()} after uploading {local.describe()}; "
            "the descriptor is unchanged"
        )

    stamp(resource, served)
    save_descriptor(descriptor_path, document)
    print(f"publish: {descriptor_path} declares {served.describe()}")
    return EXIT_OK


def command_restamp(args: argparse.Namespace) -> int:
    descriptor_path = descriptor_for(args.datasets_dir, args.dataset)
    document = load_descriptor(descriptor_path)
    resource = pick_resource(document, descriptor_path, args.resource)
    url = resource_url(resource, descriptor_path)

    before = declared_measurement(resource)
    served = measure_url(url)
    stamp(resource, served)
    save_descriptor(descriptor_path, document)

    if before == served:
        print(f"restamp: {descriptor_path} already declared {served.describe()}")
    else:
        print(
            f"restamp: {descriptor_path} declared {before.describe() if before else 'nothing'}; "
            f"{url} serves {served.describe()}"
        )
    return EXIT_OK


def command_verify(args: argparse.Namespace) -> int:
    slugs = args.dataset or sorted(p.parent.name for p in args.datasets_dir.glob("*/datapackage.json"))
    if not slugs:
        raise PublishError(f"no */datapackage.json under {args.datasets_dir}")

    disagreements: list[str] = []
    for slug in slugs:
        descriptor_path = descriptor_for(args.datasets_dir, slug)
        document = load_descriptor(descriptor_path)
        for resource in document.get("resources") or []:
            name = resource.get("name") or slug
            url = resource_url(resource, descriptor_path)
            declared = declared_measurement(resource)
            if declared is None:
                disagreements.append(f"{slug} :: {name}\n    declares no bytes/hash for {url}")
                continue
            served = measure_url(url)
            if served == declared:
                print(f"verify: {slug} :: {name} — {served.describe()}")
                continue
            detail = [f"{slug} :: {name}"]
            if served.size != declared.size:
                detail.append(f"    bytes: descriptor declares {declared.size:,}; the endpoint serves {served.size:,}")
            if served.digest != declared.digest:
                detail.append(f"    hash: descriptor declares {declared.digest}; the endpoint serves {served.digest}")
            disagreements.append("\n".join(detail))

    if disagreements:
        print(f"\nverify: {len(disagreements)} disagreement(s) between descriptor and endpoint\n")
        for line in disagreements:
            print(line)
        print(f"\nverify: FAILED — {len(disagreements)} of {len(slugs)} dataset(s) describe an object that moved")
        return EXIT_DISAGREEMENT
    print(f"verify: {len(slugs)} dataset(s) declare what the endpoint serves ({', '.join(slugs)})")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--datasets-dir", default=Path("datasets"), type=Path)
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--resource", metavar="NAME", help="name the resource when the package declares several")

    publish = subparsers.add_parser("publish", help="upload a file, then declare what the endpoint serves back")
    publish.add_argument("--dataset", required=True, metavar="SLUG")
    publish.add_argument("--file", required=True, metavar="PATH", help="the built artefact to upload")
    publish.add_argument(
        "--uploader",
        default=os.environ.get("DATASET_UPLOAD_CMD"),
        metavar="CMD",
        help="command carrying {file} and {url}; defaults to $DATASET_UPLOAD_CMD",
    )
    publish.add_argument("--readback-attempts", type=int, default=READBACK_ATTEMPTS)
    publish.add_argument("--readback-delay", type=float, default=READBACK_DELAY)
    add_common(publish)
    publish.set_defaults(handler=command_publish)

    restamp = subparsers.add_parser(
        "restamp", help="declare what the endpoint already serves, for an object published before this seam"
    )
    restamp.add_argument("--dataset", required=True, metavar="SLUG")
    add_common(restamp)
    restamp.set_defaults(handler=command_restamp)

    verify = subparsers.add_parser("verify", help="refuse when a descriptor and its endpoint disagree")
    verify.add_argument("--dataset", action="append", metavar="SLUG", help="restrict to one dataset (repeatable)")
    verify.set_defaults(handler=command_verify, resource=None)

    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    if args.command == "publish" and not args.uploader:
        print("publish: no uploader — pass --uploader or set DATASET_UPLOAD_CMD", file=sys.stderr)
        return EXIT_ERROR
    try:
        return int(args.handler(args))
    except Refusal as exc:
        print(f"{args.command}: REFUSED — {exc}", file=sys.stderr)
        return EXIT_DISAGREEMENT
    except PublishError as exc:
        print(f"{args.command}: could not complete — {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
