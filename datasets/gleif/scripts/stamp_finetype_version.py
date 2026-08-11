#!/usr/bin/env python3
"""Record the finetype release that produced a descriptor.

`datapackage_describe` emits the inferred schema but not the identity of the
binary that inferred it, so a descriptor produced by a superseded finetype is
indistinguishable from a current one by reading it. This step resolves the
`finetype` on PATH and writes its version into the descriptor as the
package-level `x-finetype-version`. It asserts the binary is the one the caller
expects only when `--expect` is passed; the pipeline invokes it without one, so
as the pipeline runs it stamps whatever PATH resolves to.

The stamp is derived at generation time rather than hand-maintained: a checked-in
literal records what someone believed when they typed it, which is the thing that
cannot be trusted here.

Usage:
    stamp_finetype_version.py --descriptor datasets/<name>/datapackage.json
    stamp_finetype_version.py --descriptor ... --expect 0.6.56
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys


def finetype_version(binary: str) -> str:
    """Return the dotted version reported by `<binary> --version`."""
    try:
        proc = subprocess.run([binary, "--version"], capture_output=True, text=True)
    except FileNotFoundError:
        sys.exit(f"stamp: `{binary}` is not on PATH — cannot record a version.")
    if proc.returncode != 0:
        sys.exit(f"stamp: `{binary} --version` failed ({proc.returncode})\n{proc.stderr}")
    match = re.search(r"(\d+\.\d+\.\d+)", proc.stdout)
    if not match:
        sys.exit(f"stamp: could not parse a version from {proc.stdout!r}.")
    return match.group(1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--descriptor", required=True, help="datapackage.json to stamp")
    ap.add_argument("--binary", default="finetype", help="finetype binary (default: PATH)")
    ap.add_argument(
        "--expect",
        help="fail unless the resolved finetype reports exactly this version; "
        "use it to pin a run to a known release rather than to whatever PATH holds",
    )
    args = ap.parse_args()

    version = finetype_version(args.binary)
    if args.expect and version != args.expect:
        sys.exit(
            f"stamp: finetype on PATH reports {version}, but --expect {args.expect} "
            "was requested. Resolve which binary should produce this descriptor "
            "before stamping it."
        )

    with open(args.descriptor, encoding="utf-8") as fh:
        descriptor = json.load(fh)

    descriptor["x-finetype-version"] = version

    with open(args.descriptor, "w", encoding="utf-8") as fh:
        json.dump(descriptor, fh, indent=2, sort_keys=True, ensure_ascii=False)
        fh.write("\n")
    print(f"stamp: {args.descriptor} x-finetype-version={version}", file=sys.stderr)


if __name__ == "__main__":
    main()
