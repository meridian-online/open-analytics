#!/usr/bin/env python3
"""Read the arc revision a Cargo.lock pins, and point the manifest loader at another one.

Brightfield ships this repository's Protocols and loads each with the `arc` its own
Cargo.lock pins. The descriptors workflow's `arc-manifest-load-brightfield` job loads
every manifest a second time with that revision, so a change here that Brightfield's
`arc` refuses fails before it merges rather than turning Brightfield's main red after.

  brightfield_arc_pin.py rev LOCK
      Print the one arcform revision LOCK's git sources name. Exit 2 when LOCK names
      none, or more than one, since either leaves the revision to load with unknown.

  brightfield_arc_pin.py retarget CARGO_TOML REV
      Rewrite the rev on the loader's `arc` dependency line in CARGO_TOML to REV and
      change nothing else, so the copy keeps the feature set Brightfield's loader uses.
      Exit 2 when the file has not exactly one such line, or REV is not a full commit.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

LOCK_SOURCE = re.compile(
    r'^source = "git\+https://github\.com/meridian-online/arcform\?rev=([0-9a-f]{40})#([0-9a-f]{40})"$',
    re.MULTILINE,
)
ARC_DEP = re.compile(r'^(arc = \{ package = "arcform", git = "[^"]+", rev = ")([0-9a-f]{40})(".*)$', re.MULTILINE)
FULL_COMMIT = re.compile(r"^[0-9a-f]{40}$")


def rev(lock: Path) -> int:
    revs = sorted({m.group(2) for m in LOCK_SOURCE.finditer(lock.read_text())})
    if not revs:
        print(f"{lock} names no arcform source pinned to a full commit", file=sys.stderr)
        return 2
    if len(revs) > 1:
        print(f"{lock} pins arcform at more than one revision: {', '.join(revs)}", file=sys.stderr)
        return 2
    print(revs[0])
    return 0


def retarget(toml: Path, new_rev: str) -> int:
    if not FULL_COMMIT.match(new_rev):
        print(f"{new_rev!r} is not a 40-character commit", file=sys.stderr)
        return 2
    text = toml.read_text()
    lines = ARC_DEP.findall(text)
    if len(lines) != 1:
        print(f"{toml} has {len(lines)} arc dependency lines, wanted one arc dependency line", file=sys.stderr)
        return 2
    toml.write_text(ARC_DEP.sub(lambda m: m.group(1) + new_rev + m.group(3), text))
    return 0


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "rev":
        return rev(Path(argv[1]))
    if len(argv) == 3 and argv[0] == "retarget":
        return retarget(Path(argv[1]), argv[2])
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
