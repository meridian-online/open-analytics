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
import hashlib
import json
import os
import re
import subprocess
import sys


def content_fingerprint(descriptor: dict) -> str:
    """Hash the descriptor with its own stamp removed.

    The stamp must not feed the hash, or every stamp changes the fingerprint and
    the guard below can never fire.
    """
    body = {k: v for k, v in descriptor.items() if k != "x-finetype-version"}
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def read_state(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


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
        "--state",
        help="where the content fingerprint is remembered between runs "
        "(default: <descriptor dir>/build/.finetype-stamp.json)",
    )
    ap.add_argument(
        "--allow-restamp",
        action="store_true",
        help="stamp even when the descriptor's content has not changed since it was "
        "stamped with a different version — only when you know the content is current",
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
    # every run where only the finetype BINARY moved, since no data file is hashed
    # on arcform's staleness path. Stamping unconditionally in that run writes the
    # NEW version onto content the OLD binary produced, and the one field whose
    # entire purpose is to say which engine ran would be the field that lies.
    #
    # So: fingerprint the descriptor with its own stamp excluded, and remember it.
    # If the fingerprint is unchanged since the last stamp while the resolved
    # version has moved, `describe` did not re-run and the new version is not this
    # content's. Refuse, and say which.
    #
    # THIS IS A GUARD, NOT A PROOF. It cannot see a describe that re-ran and
    # produced byte-identical output, and a wiped build/ resets it to first-run
    # behaviour. The guarantee belongs in `datapackage_describe` itself, which
    # knows what it ran; until it stamps its own output this step is the best
    # available approximation and should be read as one.
    state_path = args.state or os.path.join(
        os.path.dirname(os.path.abspath(args.descriptor)), "build", ".finetype-stamp.json"
    )
    fingerprint = content_fingerprint(descriptor)
    previous = read_state(state_path)
    recorded = descriptor.get("x-finetype-version")

    if (
        not args.allow_restamp
        and previous.get("fingerprint") == fingerprint
        and recorded is not None
        and recorded != version
    ):
        sys.exit(
            f"stamp: REFUSED. {args.descriptor} still carries the content stamped as "
            f"{recorded}, but the finetype on PATH is {version}. The descriptor has not "
            "been regenerated, so stamping it would attribute this content to an engine "
            "that did not produce it.\n"
            "  Re-run the pipeline so `describe` actually re-runs (it skips as "
            "hash_clean when only the binary moved), or pass --allow-restamp if you "
            "know the content is current."
        )

    descriptor["x-finetype-version"] = version

    with open(args.descriptor, "w", encoding="utf-8") as fh:
        json.dump(descriptor, fh, indent=2, sort_keys=True, ensure_ascii=False)
        fh.write("\n")

    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    with open(state_path, "w", encoding="utf-8") as fh:
        json.dump({"fingerprint": fingerprint, "version": version}, fh, indent=2)
        fh.write("\n")

    print(f"stamp: {args.descriptor} x-finetype-version={version}", file=sys.stderr)


if __name__ == "__main__":
    main()
