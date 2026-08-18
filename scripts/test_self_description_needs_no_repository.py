#!/usr/bin/env python3
"""Ask a stamped object what it is with the source repository unreachable.

The point of putting a descriptor inside its own Parquet is not convenience. It is
that a consumer holding an object URL has no way to find `datasets/<slug>/datapackage.json`
in a repository, no reason to know the repository exists, and — for anything served
from behind an authenticating edge — no repository to find at all. A mechanism that
merely *prefers* the footer and falls back to the repository would look identical on
a green run and fail exactly where it matters.

So this makes the difference observable. It runs with `github.com` and
`raw.githubusercontent.com` unreachable, and asserts both halves:

  the OLD way stops working — fetching `datapackage.json` out of the repository
  raises, which is what proves the block is real rather than assumed; and

  the NEW way still answers — `stamp_descriptor.py read <object-url>` returns the
  full descriptor, with the schema, the types, the constraints, the primary key,
  the licence and the sources in it.

It also records every request the object's own server receives, so "reads nothing
outside its own object URL" is a list of requests rather than a reading of the code.

THE BLOCK IS THIS SCRIPT'S PRECONDITION, NOT ITS JOB. It refuses to run (exit 2)
while a repository host is still reachable, because a demonstration that quietly
ran unblocked would pass forever and mean nothing. Put the block in place first:

  CI (Linux)   echo "0.0.0.0 github.com raw.githubusercontent.com" | sudo tee -a /etc/hosts
  macOS        sandbox-exec -p '(version 1)(allow default)(deny network-outbound)
               (allow network-outbound (remote ip "localhost:*"))' python3 scripts/...

Exit codes:

  0  the repository was unreachable and the object still answered
  1  the object did not answer, or answered with something other than its descriptor
  2  the demonstration could not be set up — a reachable repository host, no DuckDB,
     no loopback. Never a verdict.
"""

from __future__ import annotations

import json
import socket
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import stamp_descriptor as sd  # noqa: E402 - found beside this file
from test_stamp_descriptor import ObjectServer, build_dataset  # noqa: E402

EXIT_OK = 0
EXIT_DISAGREEMENT = 1
EXIT_ERROR = 2

# The hosts a consumer must not need. `github.com` is where this repository lives and
# `raw.githubusercontent.com` is where its `datapackage.json` is actually served from,
# so blocking the first without the second would leave the old path working.
REPOSITORY_HOSTS = ("github.com", "raw.githubusercontent.com")

# The descriptor URL a consumer would have had to know about. Fetching it is the
# control: under the block it must fail.
SIDECAR_URL = (
    "https://raw.githubusercontent.com/meridian-online/open-analytics/main/"
    "datasets/naics/datapackage.json"
)

CONNECT_TIMEOUT = 5.0


def unreachable(host: str) -> str | None:
    """Why `host` cannot be reached, or None when it can."""
    try:
        with socket.create_connection((host, 443), CONNECT_TIMEOUT):
            return None
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"


def main() -> int:
    for host in REPOSITORY_HOSTS:
        reason = unreachable(host)
        if reason is None:
            print(
                f"blocked-host demonstration: REFUSING TO RUN — {host} is still reachable.\n"
                "  This script only means something with the repository blocked; running it\n"
                "  unblocked would pass whether or not the mechanism depends on the repository.\n"
                "  Put the block in place (see the module docstring) and run it again.",
                file=sys.stderr,
            )
            return EXIT_ERROR
        print(f"blocked-host demonstration: {host} is unreachable — {reason}")

    with tempfile.TemporaryDirectory(prefix="needs-no-repository-") as scratch:
        root = Path(scratch) / "widgets"
        try:
            descriptor, parquet = build_dataset(root, rows=2_000)
            code = sd.main(["stamp", "--descriptor", str(descriptor), "--parquet", str(parquet)])
        except sd.StampError as exc:
            print(f"blocked-host demonstration: could not build the fixture: {exc}", file=sys.stderr)
            return EXIT_ERROR
        if code != EXIT_OK:
            print(f"blocked-host demonstration: stamping the fixture exited {code}", file=sys.stderr)
            return EXIT_ERROR

        # The repository copy is then deleted. Nothing on this machine holds a
        # datapackage.json when the read below runs, so a fallback to a sibling file
        # has nothing to fall back to.
        expected = json.loads(descriptor.read_text(encoding="utf-8"))
        for key in ("bytes", "hash"):
            expected["resources"][0].pop(key, None)
        descriptor.unlink()

        try:
            with urllib.request.urlopen(SIDECAR_URL, timeout=CONNECT_TIMEOUT) as response:
                response.read()
        except (urllib.error.URLError, OSError) as exc:
            print(f"blocked-host demonstration: the repository copy is out of reach — {exc}")
        else:
            print(
                f"blocked-host demonstration: REFUSING — {SIDECAR_URL} answered, so the block "
                "is not in force",
                file=sys.stderr,
            )
            return EXIT_ERROR

        with ObjectServer(parquet.read_bytes()) as server:
            try:
                con = sd.connect()
                carried = sd.read_self_description(con, server.url)
            except sd.StampError as exc:
                print(f"blocked-host demonstration: {exc}", file=sys.stderr)
                return EXIT_ERROR
            requests = list(server.requests)

    if carried is None:
        print(
            f"blocked-host demonstration: FAILED — {server.url} returned no description",
            file=sys.stderr,
        )
        return EXIT_DISAGREEMENT

    document = json.loads(carried)
    if document != expected:
        print(
            "blocked-host demonstration: FAILED — the object answered with something other "
            "than its descriptor",
            file=sys.stderr,
        )
        return EXIT_DISAGREEMENT

    touched = sorted({path for _, path, _ in requests})
    if touched != [server.object_path]:
        print(
            f"blocked-host demonstration: FAILED — the read touched {touched}, not just "
            f"{server.object_path}",
            file=sys.stderr,
        )
        return EXIT_DISAGREEMENT

    fields = document["resources"][0]["schema"]["fields"]
    print(
        "blocked-host demonstration: the object answered from its URL alone — "
        f"{len(fields)} field(s), primaryKey {document['resources'][0]['schema']['primaryKey']}, "
        f"licence {[licence['name'] for licence in document['licenses']]}, "
        f"in {len(requests)} request(s) to {touched[0]} and nothing else"
    )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
