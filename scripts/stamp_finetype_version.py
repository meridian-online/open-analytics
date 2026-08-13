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
import os
import time
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
    ap.add_argument(
        "--max-age-sec",
        type=int,
        default=900,
        help="how recently the descriptor must have been written for this step to "
        "believe `describe` produced it (default: 900)",
    )
    ap.add_argument(
        "--allow-stale",
        action="store_true",
        help="stamp even when the descriptor is older than the freshness window — "
        "only when you know the content is current",
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

    # ── The guard, and the reason this step is not a one-liner ──────────────────
    # `describe` skips as hash_clean whenever its op_config is unchanged — which is
    # every run where only the finetype BINARY moved, because arcform hashes the
    # operator ref and its `with:` block and nothing else. Stamping in that run
    # writes the NEW version onto content the OLD binary produced, and the one field
    # whose entire purpose is to say which engine ran becomes the field that lies.
    #
    # The question this step actually needs answered is "did describe just write
    # this file?", and the file's own mtime answers it directly. describe runs
    # immediately before this step, so a descriptor it produced is seconds old; one
    # it skipped is as old as the last real Run. No fingerprint, no state file, and
    # no flag for the caller to assert something they may not know.
    #
    # THIS IS A GUARD, NOT A PROOF — a `touch` defeats it, and a clock skewed
    # backwards trips it. The guarantee belongs in `datapackage_describe`, which
    # knows what it ran; until it stamps its own output this is the honest
    # approximation and the comment says so rather than implying more.
    age = time.time() - os.path.getmtime(args.descriptor)
    if not args.allow_stale and age > args.max_age_sec:
        sys.exit(
            f"stamp: REFUSED. {args.descriptor} was last written {int(age)}s ago, past "
            f"the {args.max_age_sec}s freshness window, so `describe` did not produce it "
            "in this Run — it skipped as hash_clean and the content is from an earlier "
            f"engine.\n  Stamping it {version} would attribute that content to a binary "
            "that did not generate it.\n  Re-run with `arc run --force` so describe "
            "genuinely re-runs, or pass --allow-stale if you know the content is current."
        )

    descriptor["x-finetype-version"] = version

    with open(args.descriptor, "w", encoding="utf-8") as fh:
        json.dump(descriptor, fh, indent=2, sort_keys=True, ensure_ascii=False)
        fh.write("\n")

    print(f"stamp: {args.descriptor} x-finetype-version={version}", file=sys.stderr)


if __name__ == "__main__":
    main()
