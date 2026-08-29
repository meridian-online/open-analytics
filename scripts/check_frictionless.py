#!/usr/bin/env python3
"""Put every published descriptor through the reference Frictionless implementation.

Every other check on this page reads a descriptor's declarations back against bytes,
a sibling descriptor, or a manifest. None of them asks the question a consumer's
toolchain asks first: does `frictionless` load this package at all. Data Package v2's
Table Schema field object is a fifteen-branch `oneOf`, one branch per type, each
giving `constraints` its own properties set, so a keyword beside the wrong `type` is
not surplus a reader skips — it is a whole-package refusal. Three of the four
descriptors this repository ships were refused that way and every check here passed.

    uv run --with frictionless==5.19.0 scripts/check_frictionless.py

The version is not baked into this file. `frictionless-rejections.json` states the
version its entries were measured against and this check holds the INSTALLED version
to it, because an entry measured against one implementation and read against another
reads exactly like one measured now. Bump the workflow's pin and the entries must be
re-measured before this goes green again.

WHICH FIELD, NOT ONLY WHICH MESSAGE. `frictionless` reports `constraint "pattern" is
not supported by type "date"` twice for `gleif` and names neither field, so an entry
pinned on the message alone would go on matching after the rejection moved to a
different field. Each field is therefore re-offered on its own, in a minimal package,
and the notes it draws are attributed to it by name. Notes the whole package raises
that no single field accounts for are reported against the package itself rather than
dropped — an unattributed refusal must not read as no refusal.

THE ENTRIES CLOSE NOTHING AND CANNOT BE WRITTEN TO SILENCE ONE. This works the same
way as `label-agreement.json`'s `corrections`, for the same structural reason: a descriptor
is stamped into the Parquet footer of the object it describes and declares that
object's sha256, so a declaration cannot be corrected here — only republished. An
entry records a refusal that is known, says which act lands the fix, and pins the
exact notes. It reddens when the refusal CHANGES, when the declaration MOVES, and
when the fix LANDS, so it can be deleted only by the thing being true. A refusal with
no entry is a failure; so is an entry naming a package that is now accepted.

    check_frictionless.py                report; exit 1 on an unaccounted or stale entry
    check_frictionless.py --json OUT     write the findings as JSON as well

Exit 0 every descriptor is accepted, or every refusal is accounted for and every
entry is still live. Exit 1 a refusal no entry accounts for, an entry whose notes no
longer match, an entry whose pointer names nothing, or an entry for a package the
reference implementation now accepts. Exit 2 a fault: `frictionless` not importable,
a version other than the one the entries were measured against, a descriptor that
cannot be read, a malformed rejections file, or no packages.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_label_agreement import (  # noqa: E402
    CheckError,
    Unresolved,
    parse_pointer,
    render_pointer,
    resolve,
)

EXIT_OK = 0
EXIT_DISAGREEMENT = 1
EXIT_ERROR = 2

# A field is re-offered inside this skeleton to find out which field a note belongs
# to. `path` names no file: the descriptor is never opened, only validated, so the
# refusal that comes back is about the field object and nothing else.
PROBE_RESOURCE_NAME = "r"


def probe_package(field: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": "p",
        "resources": [
            {
                "name": PROBE_RESOURCE_NAME,
                "path": "d.csv",
                "schema": {"fields": [field]},
            }
        ],
    }


def refusal_notes(package_cls, exception_cls, descriptor: dict[str, Any]) -> list[str]:
    """Every note the reference implementation raises for `descriptor`; empty if accepted.

    `FrictionlessException` carries the per-field reasons on `.reasons` and the
    package-level summary on `.error`. The reasons are what name the defect; the
    summary alone says only `descriptor is not valid`, so a check reading it would
    report the same string for every possible fault. When there are no reasons the
    summary is all there is, and it is used rather than reporting an empty refusal.
    """
    try:
        package_cls(descriptor)
    except exception_cls as exc:
        reasons = [
            reason.note
            for reason in getattr(exc, "reasons", [])
            if getattr(reason, "note", None)
        ]
        return reasons or [exc.error.note]
    return []


def read_descriptor(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CheckError(f"{path}: cannot be read as JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise CheckError(f"{path}: is not a JSON object")
    resources = document.get("resources")
    if not isinstance(resources, list) or not resources:
        raise CheckError(f"{path}: declares no `resources` list")
    return document


def measure(package_cls, exception_cls, path: Path) -> tuple[dict[str, list[str]], list[str]]:
    """Refusals for one descriptor, keyed by the pointer that names what drew them.

    Returns (by_pointer, order). `order` keeps the pointers in the order the
    descriptor declares them, so a report reads down the file rather than by hash.
    """
    document = read_descriptor(path)
    whole = Counter(refusal_notes(package_cls, exception_cls, document))
    by_pointer: dict[str, list[str]] = {}
    order: list[str] = []
    attributed: Counter[str] = Counter()
    if not whole:
        return by_pointer, order

    for index, resource in enumerate(document["resources"], start=1):
        if not isinstance(resource, dict):
            raise CheckError(f"{path}: resource {index} is not an object")
        resource_name = resource.get("name")
        if not isinstance(resource_name, str) or not resource_name.strip():
            raise CheckError(f"{path}: resource {index} declares no `name`")
        fields = (resource.get("schema") or {}).get("fields")
        if not isinstance(fields, list) or not fields:
            raise CheckError(f"{path}: resource {resource_name!r} declares no schema fields")
        for field in fields:
            if not isinstance(field, dict) or not isinstance(field.get("name"), str):
                raise CheckError(f"{path}: resource {resource_name!r} has a field with no `name`")
            notes = refusal_notes(package_cls, exception_cls, probe_package(field))
            if not notes:
                continue
            pointer = (
                f"resources[name={resource_name}]"
                f".schema.fields[name={field['name']}]"
            )
            by_pointer[pointer] = notes
            order.append(pointer)
            attributed.update(notes)

    remainder = whole - attributed
    if remainder:
        # A refusal the fields do not account for: package-level, resource-level, or
        # one that only appears in context. Reported against the package so it cannot
        # pass as silence.
        notes = sorted(remainder.elements())
        by_pointer[""] = notes
        order.append("")
    return by_pointer, order


def read_rejections(path: Path, installed_version: str) -> list[dict[str, Any]]:
    """The stated rejections, refused as a fault unless every entry can be checked."""
    if not path.exists():
        raise CheckError(
            f"{path}: does not exist. A check with no rejections file cannot tell an "
            f"accounted refusal from an unaccounted one"
        )
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CheckError(f"{path}: cannot be read as JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise CheckError(f"{path}: is not a JSON object")

    stated_version = document.get("frictionless_version")
    if not isinstance(stated_version, str) or not stated_version.strip():
        raise CheckError(f"{path}: states no `frictionless_version`")
    if stated_version != installed_version:
        raise CheckError(
            f"{path}: the entries were measured against frictionless {stated_version} and "
            f"frictionless {installed_version} is installed. A refusal measured against a "
            f"different implementation reads exactly like one measured now — re-measure the "
            f"entries against {installed_version}, or install {stated_version}"
        )

    entries = document.get("rejections")
    if not isinstance(entries, list):
        raise CheckError(f"{path}: `rejections` is not a list")

    seen: set[tuple[str, str]] = set()
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise CheckError(f"{path}: rejection {index} is not an object")
        for key in ("package", "pointer", "notes", "blocked_on", "reason"):
            if key not in entry:
                raise CheckError(f"{path}: rejection {index} states no `{key}`")
        for key in ("package", "blocked_on", "reason"):
            if not isinstance(entry[key], str) or not entry[key].strip():
                raise CheckError(f"{path}: rejection {index}: `{key}` is not a sentence")
        notes = entry["notes"]
        if not isinstance(notes, list) or not notes or not all(isinstance(n, str) for n in notes):
            raise CheckError(
                f"{path}: rejection {index}: `notes` is not a non-empty list of strings. An "
                f"entry that pins no note is closed by any refusal of that field"
            )
        pointer = entry["pointer"]
        if not isinstance(pointer, str):
            raise CheckError(f"{path}: rejection {index}: `pointer` is not a string")
        if pointer:
            # Refused here rather than at match time: a malformed pointer must not read
            # as a pointer that names nothing, which is a different verdict.
            parse_pointer(pointer)
        key = (entry["package"], pointer)
        if key in seen:
            raise CheckError(
                f"{path}: rejection {index}: {entry['package']}:{pointer or '(package)'} is "
                f"stated twice, and two entries for one refusal cannot both be checked"
            )
        seen.add(key)
    return entries


def check(
    package_cls,
    exception_cls,
    datasets_dir: Path,
    rejections_path: Path,
    installed_version: str,
) -> tuple[int, dict[str, Any]]:
    descriptors = sorted(datasets_dir.glob("*/datapackage.json"))
    if not descriptors:
        raise CheckError(
            f"no {datasets_dir}/*/datapackage.json — nothing was put through the "
            f"reference implementation, which is a fault and not a pass"
        )
    entries = read_rejections(rejections_path, installed_version)

    measured: dict[str, dict[str, list[str]]] = {}
    order: dict[str, list[str]] = {}
    documents: dict[str, dict[str, Any]] = {}
    for path in descriptors:
        slug = path.parent.name
        documents[slug] = read_descriptor(path)
        measured[slug], order[slug] = measure(package_cls, exception_cls, path)

    accepted = [slug for slug in sorted(measured) if not measured[slug]]
    refused = [slug for slug in sorted(measured) if measured[slug]]

    stated: dict[tuple[str, str], dict[str, Any]] = {
        (entry["package"], entry["pointer"]): entry for entry in entries
    }
    faults: list[str] = []

    # 1) Every refusal the reference implementation raises, held to an entry.
    for slug in refused:
        for pointer in order[slug]:
            notes = measured[slug][pointer]
            where = f"{slug}:{pointer}" if pointer else f"{slug}: (package)"
            entry = stated.get((slug, pointer))
            if entry is None:
                faults.append(
                    f"{where} — refused by the reference implementation and no entry in "
                    f"{rejections_path.name} accounts for it: "
                    + "; ".join(notes)
                )
                continue
            if list(entry["notes"]) != list(notes):
                faults.append(
                    f"{where} — the entry pins "
                    + "; ".join(entry["notes"])
                    + " and the reference implementation now says "
                    + "; ".join(notes)
                )

    # 2) Every entry, held to a refusal that is still there. Both directions matter:
    #    an entry outliving its refusal is how a fixed defect goes on reading as known.
    for entry in entries:
        slug = entry["package"]
        pointer = entry["pointer"]
        where = f"{slug}:{pointer}" if pointer else f"{slug}: (package)"
        if slug not in measured:
            faults.append(
                f"{where} — the entry names a package {datasets_dir}/ does not carry"
            )
            continue
        if slug in accepted:
            faults.append(
                f"{where} — the reference implementation ACCEPTS {slug} now. The refusal "
                f"this entry records is closed; delete the entry"
            )
            continue
        if pointer:
            try:
                resolve(documents[slug], parse_pointer(pointer))
            except Unresolved as exc:
                faults.append(
                    f"{where} — the entry's pointer names nothing in the descriptor: {exc}"
                )
                continue
        if pointer not in measured[slug]:
            faults.append(
                f"{where} — the entry records a refusal the reference implementation no "
                f"longer raises here, though {slug} is still refused elsewhere; delete it"
            )

    findings = {
        "frictionless_version": installed_version,
        "accepted": accepted,
        "refused": {slug: measured[slug] for slug in refused},
        "faults": faults,
    }

    for slug in sorted(measured):
        if slug in accepted:
            print(f"ACCEPTED  {slug}")
            continue
        print(f"REFUSED   {slug}")
        for pointer in order[slug]:
            where = pointer or "(package)"
            for note in measured[slug][pointer]:
                print(f"            {where}: {note}")
    for fault in faults:
        print(f"frictionless: {fault}")

    print(
        f"frictionless {installed_version}: {len(accepted)} of {len(measured)} package(s) "
        f"accepted; {len(entries)} stated rejection(s); {len(faults)} fault(s)"
    )
    return (EXIT_DISAGREEMENT if faults else EXIT_OK), findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datasets-dir", default="datasets", type=Path)
    parser.add_argument(
        "--rejections",
        type=Path,
        default=None,
        help="stated rejections (default: <datasets-dir>/frictionless-rejections.json)",
    )
    parser.add_argument("--json", dest="json_out", type=Path, default=None)
    args = parser.parse_args(argv)
    rejections_path = args.rejections or (args.datasets_dir / "frictionless-rejections.json")

    try:
        import frictionless
        from frictionless import Package
        from frictionless.exception import FrictionlessException
    except ImportError:
        print(
            "frictionless is not importable, so nothing was validated. Run this with:\n"
            "  uv run --with frictionless==<the version the rejections file states> "
            "scripts/check_frictionless.py",
            file=sys.stderr,
        )
        return EXIT_ERROR

    try:
        status, findings = check(
            Package,
            FrictionlessException,
            args.datasets_dir,
            rejections_path,
            frictionless.__version__,
        )
    except CheckError as exc:
        print(f"frictionless check: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if args.json_out:
        args.json_out.write_text(json.dumps(findings, indent=2, sort_keys=True) + "\n")
    return status


if __name__ == "__main__":
    sys.exit(main())
