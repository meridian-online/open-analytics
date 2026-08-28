#!/usr/bin/env python3
# /// script
# requires-python = "==3.12.*"
# dependencies = []
# ///
"""Hold the published packages to each other, not just each to its own bytes.

Every other check in this repository judges one package against the object it
describes. Each can pass while the four contradict one another, and three of them
did: `gleif.jurisdiction` declared `^[A-Z]{2}$` against `edgar_gleif`'s
`^[A-Z]{2}(-[A-Z0-9]{1,3})?$` and failed 371,995 of 3,377,394 rows; `legal_name`
carries one length envelope in one package and another in the other under the same
`x-finetype-label`; the ISO 3166 caveat was corrected in one descriptor and not its
sibling. An analyst who downloads two datasets and writes one validation routine
against a concept gets a failure on one of them, and nothing in either package
acknowledges the other. The disagreement lives in the space between the packages,
which nothing read until this.

TWO GROUPINGS, AND THE SECOND EXISTS BECAUSE THE FIRST HAS AN ESCAPE HATCH.

  * BY CONCEPT — fields declaring the same `x-finetype-label` are compared on
    `type`, `format` and every `constraints` key either of them declares. The label
    is already in every descriptor, so the grouping key needs no new metadata.

  * BY FIELD NAME — fields with the same name in two or more packages are compared
    on their `x-finetype-label`. Grouping by label makes a label disagreement
    invisible BY CONSTRUCTION: relabel one of two disagreeing fields and they stop
    being in the same group, and the concept check goes quiet without a single
    constraint moving. The name grouping is what makes relabelling a change the
    check can see rather than a way to silence it.

`unknown` IS NOT A CONCEPT. finetype returns `unknown` when it cannot name a type,
so two fields both labelled `unknown` share nothing and are not compared as a group.
They are NAMED in the output rather than dropped silently — an exclusion nobody can
see is indistinguishable from a field the sweep never reached.

DESCRIPTIONS ARE NOT COMPARED. Each package's prose names its own register and its
own provenance, so two descriptions of one concept are supposed to differ and a
textual comparison would be all noise. The consequence is stated rather than hidden:
a copy-pasted caveat corrected in one descriptor and not the other is a real instance
of this card's class that this check cannot see. Neither are `x-finetype-confidence`,
`x-finetype-pattern-fit` or `x-finetype-enum-domain`, which are measurements OF a
column rather than declarations ABOUT a concept, and differ by construction.

IT REPORTS; IT DOES NOT BLOCK ON A DIFFERENCE. Two packages may legitimately differ
where their populations differ, and no check can know which. So a difference is
printed, never fatal. What IS fatal is a STALE REASON: `datasets/label-agreement.json`
records why a difference is legitimate, and each entry pins the exact values it
excuses. Move `gleif.legal_name`'s maxLength and the entry that named 500 no longer
describes anything, this exits 1, and the difference is reported again. That is the
whole point of pinning the values: a reason closes ONE difference between THESE
values, and can never become a standing licence for a label.

A reason is never written by this script. `--json` and the printed stanza make the
entry obvious to paste, with the reason left as a TODO — because a check that could
write its own exemptions is a check that can be silenced by running it.

    check_label_agreement.py              report; exit 1 only on a stale reason
    check_label_agreement.py --strict     also exit 1 on a difference with no reason
    check_label_agreement.py --json OUT   write the findings as JSON as well

Exit 0 agreement, or differences that are all accounted for. Exit 1 a stale or
unused reason (or, under --strict, an unaccounted difference). Exit 2 a fault: a
descriptor that cannot be read, a malformed reasons file, or no packages at all.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path
from typing import Any

EXIT_OK = 0
EXIT_DISAGREEMENT = 1
EXIT_ERROR = 2

LABEL_KEY = "x-finetype-label"

# finetype's sentinel for "no type was named", not a concept two fields can share.
NOT_A_CONCEPT = "unknown"

# Declared ABOUT the concept, so two fields carrying one label should agree on them.
DECLARED_KEYS = ("type", "format")

# Measured FROM the column, so two fields carrying one label are expected to differ
# on them and comparing them would report a difference on every group.
MEASURED_KEYS = (
    "description",
    "x-finetype-confidence",
    "x-finetype-pattern-fit",
    "x-finetype-enum-domain",
    "title",
    "example",
)

# What an absent property is, in a difference's `values` and in the reasons file.
# Frictionless never declares a null constraint, so the JSON literal is unambiguous
# here and reads as `(absent)` in the printed report.
ABSENT = None


class CheckError(Exception):
    """A fault in the inputs — not a disagreement between them."""


@dataclass(frozen=True)
class Field:
    package: str
    resource: str
    name: str
    label: str | None
    properties: dict[str, Any]

    @property
    def ident(self) -> str:
        return f"{self.package}.{self.name}"


@dataclass
class Difference:
    kind: str  # "concept" (grouped by label) or "field" (grouped by field name)
    key: str  # the label, or the field name
    prop: str  # "type", "format", "constraints.<name>", or LABEL_KEY
    values: dict[str, Any]  # package.field -> declared value (ABSENT if undeclared)
    reason: str | None = None  # filled in from the reasons file when one matches

    def stanza(self) -> dict[str, Any]:
        """The entry that would close this difference, minus the reason itself."""
        return {
            ("concept" if self.kind == "concept" else "field"): self.key,
            "property": self.prop,
            "values": self.values,
            "reason": "TODO — why this difference is legitimate, or change a package",
        }


def render_value(value: Any) -> str:
    return "(absent)" if value is ABSENT else json.dumps(value, ensure_ascii=False)


def read_package(path: Path) -> list[Field]:
    """Every field the descriptor at `path` declares, as comparable properties."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CheckError(f"{path}: cannot be read as JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise CheckError(f"{path}: is not a JSON object")
    package = path.parent.name
    resources = document.get("resources")
    if not isinstance(resources, list) or not resources:
        raise CheckError(f"{path}: declares no `resources` list")

    fields: list[Field] = []
    seen: set[str] = set()
    for index, resource in enumerate(resources, start=1):
        if not isinstance(resource, dict):
            raise CheckError(f"{path}: resource {index} is not an object")
        resource_name = resource.get("name")
        if not isinstance(resource_name, str) or not resource_name.strip():
            raise CheckError(f"{path}: resource {index} declares no `name`")
        declared = (resource.get("schema") or {}).get("fields")
        if not isinstance(declared, list) or not declared:
            raise CheckError(f"{path}: resource {resource_name!r} declares no schema fields")
        for position, entry in enumerate(declared, start=1):
            if not isinstance(entry, dict):
                raise CheckError(
                    f"{path}: resource {resource_name!r} field {position} is not an object"
                )
            name = entry.get("name")
            if not isinstance(name, str) or not name.strip():
                raise CheckError(
                    f"{path}: resource {resource_name!r} field {position} declares no `name`"
                )
            name = name.strip()
            if name in seen:
                # The identifier in a reason is `<package>.<field>`. Two resources in
                # one package sharing a field name would make that ambiguous, so it is
                # refused here rather than resolved by picking one — a reason pinned to
                # an identifier naming two fields excuses whichever one it is read as.
                raise CheckError(
                    f"{path}: declares two fields named {name!r} across its resources, and "
                    f"`{package}.{name}` cannot name both"
                )
            seen.add(name)
            label = entry.get(LABEL_KEY)
            properties: dict[str, Any] = {
                key: entry[key] for key in DECLARED_KEYS if key in entry
            }
            constraints = entry.get("constraints")
            if constraints is not None and not isinstance(constraints, dict):
                raise CheckError(
                    f"{path}: {name!r} declares `constraints` as a "
                    f"{type(constraints).__name__}, not an object"
                )
            for key, value in (constraints or {}).items():
                properties[f"constraints.{key}"] = value
            fields.append(
                Field(
                    package=package,
                    resource=resource_name.strip(),
                    name=name,
                    label=label if isinstance(label, str) else None,
                    properties=properties,
                )
            )
    return fields


def compare(members: list[Field], props: list[str]) -> list[tuple[str, dict[str, Any]]]:
    """(property, {ident: value}) for every property the members do not all declare alike.

    A member that declares nothing for a property is not skipped: `(absent)` is one of
    the values, because one package bounding a field and another leaving it unbounded
    is exactly the disagreement this exists to find. `edgar_gleif.company_name` reached
    its 1..65536 by being left at the describe default, not by anyone deciding it.
    """
    out: list[tuple[str, dict[str, Any]]] = []
    for prop in props:
        values = {member.ident: member.properties.get(prop, ABSENT) for member in members}
        distinct = {json.dumps(value, sort_keys=True) for value in values.values()}
        if len(distinct) > 1:
            out.append((prop, dict(sorted(values.items()))))
    return out


def concept_differences(fields: list[Field]) -> tuple[list[Difference], dict[str, list[Field]]]:
    """Fields sharing an `x-finetype-label`, compared on what they declare."""
    groups: dict[str, list[Field]] = {}
    for item in fields:
        if item.label is None or item.label == NOT_A_CONCEPT:
            continue
        groups.setdefault(item.label, []).append(item)

    compared = {label: members for label, members in groups.items() if len(members) > 1}
    differences: list[Difference] = []
    for label in sorted(compared):
        members = sorted(compared[label], key=lambda f: f.ident)
        props = sorted({prop for member in members for prop in member.properties})
        for prop, values in compare(members, props):
            differences.append(Difference("concept", label, prop, values))
    return differences, compared


def name_differences(fields: list[Field]) -> tuple[list[Difference], dict[str, list[Field]]]:
    """Fields sharing a name across packages, compared on the label they carry.

    Only across packages: two fields in one package with one name are refused upstream,
    and there is no third case. `corpus` is the same derived construct in all four
    descriptors by each of their own descriptions, so its label is a claim four
    packages make about one concept.
    """
    groups: dict[str, list[Field]] = {}
    for item in fields:
        groups.setdefault(item.name, []).append(item)

    compared = {
        name: members
        for name, members in groups.items()
        if len({member.package for member in members}) > 1
    }
    differences: list[Difference] = []
    for name in sorted(compared):
        members = sorted(compared[name], key=lambda f: f.ident)
        values = {
            member.ident: member.label if member.label is not None else ABSENT
            for member in members
        }
        if len({json.dumps(value, sort_keys=True) for value in values.values()}) > 1:
            differences.append(
                Difference("field", name, LABEL_KEY, dict(sorted(values.items())))
            )
    return differences, compared


@dataclass
class Reason:
    kind: str
    key: str
    prop: str
    values: dict[str, Any]
    text: str
    used: bool = False
    where: str = ""


def load_reasons(path: Path) -> list[Reason]:
    """The stated reasons, each pinned to the exact values it excuses.

    A reason is refused unless it names one grouping, one property, at least two
    values and a non-empty reason. The pinning is the mechanism: an entry excuses a
    difference between THESE values, so a package moving one of them makes the entry
    stop matching, and `report` calls that out instead of letting the old sentence go
    on standing for a difference nobody has looked at since.
    """
    if not path.exists():
        return []
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CheckError(f"{path}: cannot be read as JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise CheckError(f"{path}: is not a JSON object")
    entries = document.get("reasons")
    if not isinstance(entries, list):
        raise CheckError(f"{path}: declares no `reasons` list")

    reasons: list[Reason] = []
    for index, entry in enumerate(entries, start=1):
        where = f"{path} entry {index}"
        if not isinstance(entry, dict):
            raise CheckError(f"{where}: is not an object")
        named = [key for key in ("concept", "field") if key in entry]
        if len(named) != 1:
            raise CheckError(
                f"{where}: declares {named or 'neither'} of `concept:`/`field:`; exactly "
                f"one says which grouping the difference was found by"
            )
        kind = "concept" if named[0] == "concept" else "field"
        key = entry[named[0]]
        prop = entry.get("property")
        values = entry.get("values")
        text = entry.get("reason")
        if not isinstance(key, str) or not key.strip():
            raise CheckError(f"{where}: `{named[0]}:` is not a non-empty string")
        if not isinstance(prop, str) or not prop.strip():
            raise CheckError(f"{where}: `property:` is not a non-empty string")
        if not isinstance(values, dict) or len(values) < 2:
            raise CheckError(
                f"{where}: `values:` must name at least two `<package>.<field>` "
                f"identifiers and what each declares"
            )
        if not isinstance(text, str) or not text.strip():
            raise CheckError(
                f"{where}: carries no `reason:`. A difference is closed by saying why it "
                f"is legitimate; an entry with no sentence is silencing, not a reason"
            )
        reasons.append(
            Reason(kind, key.strip(), prop.strip(), dict(values), text.strip(), where=where)
        )
    return reasons


def attach(differences: list[Difference], reasons: list[Reason]) -> None:
    """Match each reason to at most one difference, by grouping, property AND values.

    Values are compared as whole dictionaries. A member joining or leaving the group,
    or any declared value moving, makes the entry stop matching — which is what turns
    it stale rather than letting it keep excusing a difference it no longer describes.
    """
    for difference in differences:
        for reason in reasons:
            if reason.used:
                continue
            if (
                reason.kind == difference.kind
                and reason.key == difference.key
                and reason.prop == difference.prop
                and reason.values == difference.values
            ):
                reason.used = True
                difference.reason = reason.text
                break


def report(differences: list[Difference]) -> list[str]:
    lines: list[str] = []
    for difference in differences:
        grouping = "label" if difference.kind == "concept" else "field name"
        state = "accounted for" if difference.reason else "OPEN"
        lines.append(
            f"  {grouping} {difference.key!r} — {difference.prop} differs "
            f"[{state}]"
        )
        for ident, value in difference.values.items():
            lines.append(f"      {ident}: {render_value(value)}")
        if difference.reason:
            lines.append(f"      reason: {difference.reason}")
        else:
            stanza = json.dumps(difference.stanza(), indent=2, ensure_ascii=False)
            lines.append("      to close it, decide it is legitimate and add to the reasons file:")
            lines.extend(f"      {line}" for line in stanza.splitlines())
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--datasets-dir", default="datasets", type=Path)
    parser.add_argument(
        "--reasons",
        type=Path,
        default=None,
        help="stated reasons (default: <datasets-dir>/label-agreement.json)",
    )
    parser.add_argument("--json", dest="json_out", type=Path, default=None)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 on a difference with no stated reason, not only on a stale one",
    )
    args = parser.parse_args(argv)
    reasons_path = args.reasons or (args.datasets_dir / "label-agreement.json")

    descriptors = sorted(args.datasets_dir.glob("*/datapackage.json"))
    if not descriptors:
        print(
            f"label agreement: no {args.datasets_dir}/*/datapackage.json — nothing was "
            f"compared, which is a fault and not a pass",
            file=sys.stderr,
        )
        return EXIT_ERROR

    fields: list[Field] = []
    faults: list[str] = []
    examined = 0
    for path in descriptors:
        try:
            fields.extend(read_package(path))
        except CheckError as exc:
            # Collected, not returned: four unreadable descriptors name four, and the
            # `examined N of M` line below can only be worth printing if the two
            # numbers are able to differ.
            faults.append(str(exc))
            continue
        examined += 1

    try:
        reasons = load_reasons(reasons_path)
    except CheckError as exc:
        faults.append(str(exc))
        reasons = []

    concepts, concept_groups = concept_differences(fields)
    names, name_groups = name_differences(fields)
    differences = concepts + names
    attach(differences, reasons)
    stale = [reason for reason in reasons if not reason.used]
    ungrouped = sorted(
        item.ident for item in fields if item.label is None or item.label == NOT_A_CONCEPT
    )

    print(
        f"label agreement: examined {examined} package(s) of {len(descriptors)} found, "
        f"{len(fields)} field(s)"
    )
    for path in descriptors:
        print(f"  {path}")
    print(
        f"  compared {len(concept_groups)} concept(s) carried by more than one field, "
        f"and {len(name_groups)} field name(s) carried by more than one package"
    )
    # Named, never silently dropped. `unknown` is finetype declining to name a type, so
    # two fields wearing it share nothing — but a reader has to be able to see which
    # fields this run therefore did not compare as a concept.
    if ungrouped:
        print(
            f"  {len(ungrouped)} field(s) carry no concept to group by "
            f"(`{NOT_A_CONCEPT}` or no {LABEL_KEY}) and were not compared as one:"
        )
        for ident in ungrouped:
            print(f"      {ident}")

    if args.json_out:
        args.json_out.write_text(
            json.dumps(
                {
                    "packages": [str(path) for path in descriptors],
                    "examined": examined,
                    "fields": len(fields),
                    "concepts_compared": sorted(concept_groups),
                    "field_names_compared": sorted(name_groups),
                    "ungrouped": ungrouped,
                    "differences": [
                        {
                            "kind": difference.kind,
                            "key": difference.key,
                            "property": difference.prop,
                            "values": difference.values,
                            "reason": difference.reason,
                        }
                        for difference in differences
                    ],
                    "stale_reasons": [
                        {
                            "kind": reason.kind,
                            "key": reason.key,
                            "property": reason.prop,
                            "values": reason.values,
                            "reason": reason.text,
                        }
                        for reason in stale
                    ],
                    "faults": faults,
                },
                indent=2,
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    if faults:
        print(
            f"\nlabel agreement: {len(faults)} input(s) could not be read at all\n",
            file=sys.stderr,
        )
        for fault in faults:
            print(f"  {fault}", file=sys.stderr)
        return EXIT_ERROR

    open_differences = [difference for difference in differences if not difference.reason]
    if differences:
        print(
            f"\nlabel agreement: {len(differences)} difference(s) between packages "
            f"describing the same thing — {len(open_differences)} with no stated reason\n"
        )
        for line in report(differences):
            print(line)

    if stale:
        print(
            f"\nlabel agreement: {len(stale)} stated reason(s) no longer describe any "
            f"difference these packages have. A reason is pinned to the values it "
            f"excuses, so one that matches nothing has either been fixed — delete it — "
            f"or the values moved and it is now excusing a difference nobody has looked "
            f"at:\n",
            file=sys.stderr,
        )
        for reason in stale:
            named = "concept" if reason.kind == "concept" else "field"
            print(f"  {reason.where}: {named} {reason.key!r}, {reason.prop}", file=sys.stderr)
            for ident, value in sorted(reason.values.items()):
                print(f"      it pins {ident}: {render_value(value)}", file=sys.stderr)
        return EXIT_DISAGREEMENT

    if args.strict and open_differences:
        print(
            f"\nlabel agreement: FAILED under --strict — {len(open_differences)} "
            f"difference(s) carry no stated reason",
            file=sys.stderr,
        )
        return EXIT_DISAGREEMENT

    if not differences:
        print("\nlabel agreement: every concept is described the same way by every package")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
