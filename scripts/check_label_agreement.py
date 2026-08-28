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
of the disagreement this check exists for and cannot see. Neither are `x-finetype-confidence`,
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

A CORRECTION IS THE OPPOSITE OF A REASON AND CLOSES NOTHING. There were two states for
three. A difference was OPEN — nobody has looked — or closed by a reason saying it is
legitimate, and a difference that is a DEFECT is neither: someone has looked, knows
which side is wrong and what it must become, and cannot land the fix here. A descriptor
is stamped into the Parquet footer of the object it describes and declares that object's
sha256, so correcting a published declaration is a republish and not an edit — which
makes that third state the standing condition of every difference this check will ever
find, not a property of the ones open today. With two states to choose from, all of them
read as unexamined, and the only alternative was a reason written for a bug.

So `corrections` in the same file records the verdict and quietens nothing: the
difference goes on being reported, goes on being counted, and `--strict` goes on failing
on it. What it buys is that the answer is held to the packages. An entry pins a
declaration by a `pointer` addressed by the `name` its element carries — never by index,
because a field inserted above the target moves every index below it and a pin that
starts naming its neighbour reads exactly like a live one — and it reddens when the
declaration MOVES and when the fix LANDS, so it can be deleted only by the thing being
true. Pointers reach declarations the comparison never sees, which is what the instances
require: a description corrected in one descriptor and copy-pasted uncorrected into its
sibling, and a join's cardinality that is not a field property at all.

    check_label_agreement.py              report; exit 1 on a stale reason or correction
    check_label_agreement.py --strict     also exit 1 on a difference with no reason
    check_label_agreement.py --json OUT   write the findings as JSON as well

Exit 0 agreement, or differences that are all closed by a reason. Exit 1 a stale or
unused reason, a correction whose declaration moved or whose fix landed, or — under
--strict — a difference with no reason, INCLUDING one a correction adjudicated. Exit 2
a fault: a descriptor that cannot be read, a malformed agreement file, or no packages.
"""

from __future__ import annotations

import argparse
import json
import re
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
    # Corrections that judge one of the values above. They are printed, they never fill
    # in `reason`, and `--strict` reads `reason` — so a correction cannot close anything.
    corrections: list["Correction"] = dataclass_field(default_factory=list)

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


class Unresolved(Exception):
    """A pointer that names nothing in the descriptor it was written against."""


@dataclass(frozen=True)
class Step:
    key: str
    select: str | None


POINTER_STEP = re.compile(r"^([^.\[\]]+)(?:\[name=([^.\[\]]+)\])?$")


def parse_pointer(pointer: str) -> list[Step]:
    """Parse `resources[name=gleif].schema.fields[name=legal_name].constraints.maxLength`.

    A list step is addressed by the `name` its element carries and never by index. A
    field inserted above `legal_name` moves every index below it, and a pin that
    silently starts naming its neighbour reads exactly like a live one — which is the
    whole failure this file exists to make impossible. There is no index form to fall
    back to, so a pointer either names a thing by its name or is refused here.
    """
    if not pointer.strip():
        raise CheckError("a pointer must name at least one step")
    steps: list[Step] = []
    for raw in pointer.split("."):
        match = POINTER_STEP.match(raw)
        if match is None:
            raise CheckError(
                f"{pointer!r}: step {raw!r} is not `key` or `key[name=value]`. A list step "
                f"is addressed by the `name` its element carries; there is no index form"
            )
        steps.append(Step(match.group(1), match.group(2)))
    return steps


def render_pointer(steps: list[Step]) -> str:
    return ".".join(
        step.key if step.select is None else f"{step.key}[name={step.select}]" for step in steps
    )


def resolve(document: Any, steps: list[Step]) -> Any:
    """The one value `steps` names, or `Unresolved` — never a guess and never a first match."""
    here: Any = document
    walked: list[Step] = []
    for step in steps:
        walked.append(step)
        trail = render_pointer(walked)
        if not isinstance(here, dict) or step.key not in here:
            raise Unresolved(f"nothing is declared at {trail}")
        here = here[step.key]
        if step.select is None:
            continue
        if not isinstance(here, list):
            raise Unresolved(f"{trail}: {step.key} is a {type(here).__name__}, not a list")
        matched = [
            entry for entry in here if isinstance(entry, dict) and entry.get("name") == step.select
        ]
        if len(matched) != 1:
            raise Unresolved(
                f"{trail}: {len(matched)} element(s) carry that name, and a pointer that "
                f"matches other than exactly one names nothing"
            )
        here = matched[0]
    return here


def read_package(path: Path) -> tuple[dict[str, Any], list[Field]]:
    """The descriptor at `path`, and every field it declares as comparable properties.

    The whole document comes back as well as the fields: a correction points at any
    declaration in it, including the ones this check deliberately does not compare.
    """
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
    return document, fields


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


def read_agreement(path: Path) -> dict[str, Any]:
    """`datasets/label-agreement.json`, or an empty document when there is none."""
    if not path.exists():
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CheckError(f"{path}: cannot be read as JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise CheckError(f"{path}: is not a JSON object")
    return document


def load_reasons(path: Path, document: dict[str, Any]) -> list[Reason]:
    """The stated reasons, each pinned to the exact values it excuses.

    A reason is refused unless it names one grouping, one property, at least two
    values and a non-empty reason. The pinning is the mechanism: an entry excuses a
    difference between THESE values, so a package moving one of them makes the entry
    stop matching, and `report` calls that out instead of letting the old sentence go
    on standing for a difference nobody has looked at since.
    """
    if not document:
        return []
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


@dataclass
class Correction:
    """A verdict on one declaration: which side is wrong, and what act lands the fix.

    A REASON AND A CORRECTION ARE OPPOSITES AND THE FILE HOLDS BOTH. A reason says a
    difference is legitimate and closes it. A correction says it is a defect, names the
    side that is wrong and the value it must become — and closes NOTHING: the difference
    goes on being reported, `--strict` goes on failing on it, and the only thing that has
    changed is that the answer is written down where this check can hold it to the
    packages. That asymmetry is the point. A correction that closed a difference would be
    a reason with a different word on it, and a bug excused by a sentence.
    """

    package: str
    pointer: str
    steps: list[Step]
    verdict: str  # "wrong" — this declaration is a defect; "right" — it is not, and is pinned
    pinned: Any  # the exact value `declares` names
    contains: str | None  # or the substring `declares_contains` names, for prose
    becomes: Any
    has_becomes: bool
    disagrees: str
    blocked_on: str
    text: str
    where: str
    state: str = "declared"  # "declared" | "landed" | "moved" | "missing"
    found: Any = None
    detail: str = ""

    @property
    def ident(self) -> str | None:
        """`<package>.<field>`, when the pointer names a property of a schema field.

        Derived from the steps rather than declared beside them: an entry that named its
        own field could name a field the pointer does not reach, and then the correction
        would print against a difference it has nothing to do with.
        """
        steps = self.steps
        if len(steps) < 4:
            return None
        if steps[0].key != "resources" or steps[0].select is None:
            return None
        if steps[1].key != "schema" or steps[2].key != "fields" or steps[2].select is None:
            return None
        return f"{self.package}.{steps[2].select}"

    @property
    def prop(self) -> str | None:
        """The property name a difference would carry for this pointer, or None."""
        # 0..2 are `resources[..].schema.fields[..]`; the property is whatever follows.
        rest = self.steps[3:] if self.ident else []
        if len(rest) == 1 and rest[0].select is None:
            return rest[0].key
        if len(rest) == 2 and rest[0].key == "constraints" and rest[1].select is None:
            return f"constraints.{rest[1].key}"
        return None

    def pin_text(self) -> str:
        if self.contains is not None:
            return f"text containing {self.contains!r}"
        return render_value(self.pinned)


def load_corrections(path: Path, document: dict[str, Any]) -> list[Correction]:
    """The stated corrections, each pinned to the exact declaration it judges.

    Refused unless it names a package, a resolvable-shaped pointer, exactly one of
    `declares`/`declares_contains`, a verdict, a `blocked_on` naming the act that lands
    the fix, and a `reason`. `verdict: wrong` must name what the value `becomes` — except
    in the `declares_contains` form, where the pinned phrase going away IS the landing and
    a `becomes` would be a second, unpinned claim about prose. `verdict: right` must name
    who `disagrees`, because a declaration nobody disputes needs no entry at all.
    """
    entries = document.get("corrections", [])
    if not isinstance(entries, list):
        raise CheckError(f"{path}: `corrections` is a {type(entries).__name__}, not a list")

    corrections: list[Correction] = []
    for index, entry in enumerate(entries, start=1):
        where = f"{path} correction {index}"
        if not isinstance(entry, dict):
            raise CheckError(f"{where}: is not an object")
        package = entry.get("package")
        pointer = entry.get("pointer")
        verdict = entry.get("verdict")
        blocked_on = entry.get("blocked_on")
        text = entry.get("reason")
        if not isinstance(package, str) or not package.strip():
            raise CheckError(f"{where}: `package:` is not a non-empty string")
        if not isinstance(pointer, str):
            raise CheckError(f"{where}: `pointer:` is not a string")
        try:
            steps = parse_pointer(pointer)
        except CheckError as exc:
            raise CheckError(f"{where}: {exc}") from exc
        if verdict not in ("wrong", "right"):
            raise CheckError(
                f"{where}: `verdict:` is {verdict!r}; it is `wrong` (this declaration is a "
                f"defect) or `right` (it is not, and something outside it disagrees)"
            )
        named = [key for key in ("declares", "declares_contains") if key in entry]
        if len(named) != 1:
            raise CheckError(
                f"{where}: declares {named or 'neither'} of `declares:`/`declares_contains:`; "
                f"exactly one pins what the package says today"
            )
        contains = None
        pinned = None
        if named[0] == "declares_contains":
            contains = entry["declares_contains"]
            if not isinstance(contains, str) or not contains.strip():
                raise CheckError(f"{where}: `declares_contains:` is not a non-empty string")
        else:
            pinned = entry["declares"]
        has_becomes = "becomes" in entry
        becomes = entry.get("becomes")
        if verdict == "wrong" and contains is None:
            if not has_becomes:
                raise CheckError(
                    f"{where}: `verdict: wrong` names no `becomes:`. A correction that does not "
                    f"say what the value must become is a complaint, and nothing can tell when "
                    f"it has been acted on"
                )
            if becomes == pinned:
                raise CheckError(
                    f"{where}: `becomes:` is the value already declared, so nothing would change "
                    f"and the entry could never go stale"
                )
        elif has_becomes:
            reason = (
                "the pinned phrase going away is what landing looks like"
                if contains is not None
                else "`verdict: right` says the declaration stands"
            )
            raise CheckError(f"{where}: names a `becomes:` and must not — {reason}")
        disagrees = entry.get("disagrees", "")
        if verdict == "right":
            if not isinstance(disagrees, str) or not disagrees.strip():
                raise CheckError(
                    f"{where}: `verdict: right` names no `disagrees:`. A declaration nothing "
                    f"disputes needs no entry, so an entry that names no disputant pins nothing"
                )
        elif disagrees:
            raise CheckError(f"{where}: names `disagrees:` on a `verdict: wrong` entry")
        if not isinstance(blocked_on, str) or not blocked_on.strip():
            raise CheckError(
                f"{where}: carries no `blocked_on:`. A correction that does not name the act "
                f"that lands it is indistinguishable from one nobody intends to land"
            )
        if not isinstance(text, str) or not text.strip():
            raise CheckError(
                f"{where}: carries no `reason:`. A verdict with no sentence behind it is an "
                f"assertion, not a correction"
            )
        corrections.append(
            Correction(
                package=package.strip(),
                pointer=pointer,
                steps=steps,
                verdict=verdict,
                pinned=pinned,
                contains=contains,
                becomes=becomes,
                has_becomes=has_becomes,
                disagrees=disagrees.strip(),
                blocked_on=blocked_on.strip(),
                text=text.strip(),
                where=where,
            )
        )
    return corrections


def judge(corrections: list[Correction], documents: dict[str, dict[str, Any]]) -> None:
    """Hold every correction to what its package declares right now.

    Three ways an entry stops being live, and each one has to redden. The declaration
    MOVED, so the verdict is about a value nothing carries any more. The correction
    LANDED, so the defect is gone and the entry would otherwise sit there naming a bug
    that no longer exists. Or the pointer resolves to nothing at all. A correction that
    survived any of these would be exactly the standing licence the reasons file refuses.
    """
    for correction in corrections:
        document = documents.get(correction.package)
        if document is None:
            correction.state = "missing"
            correction.detail = (
                f"no datasets/{correction.package}/datapackage.json was read by this run"
            )
            continue
        try:
            value = resolve(document, correction.steps)
        except Unresolved as exc:
            correction.state = "missing"
            correction.detail = str(exc)
            continue
        correction.found = value
        if correction.contains is not None:
            if isinstance(value, str) and correction.contains in value:
                correction.state = "declared"
            elif correction.verdict == "wrong":
                correction.state = "landed"
                correction.detail = "the pinned phrase is gone — the correction is in place"
            else:
                correction.state = "moved"
                correction.detail = "the pinned phrase is gone from a declaration pinned as right"
            continue
        if value == correction.pinned:
            correction.state = "declared"
        elif correction.has_becomes and value == correction.becomes:
            correction.state = "landed"
            correction.detail = "the package now declares what this entry asked for"
        else:
            correction.state = "moved"
            correction.detail = "the package declares neither the pinned value nor the corrected one"


def attach_corrections(differences: list[Difference], corrections: list[Correction]) -> None:
    """Show a live correction beside the difference it bears on. It closes nothing."""
    for difference in differences:
        for correction in corrections:
            if correction.state != "declared":
                continue
            ident = correction.ident
            if ident is None or correction.prop != difference.prop:
                continue
            if ident in difference.values:
                difference.corrections.append(correction)


def report(differences: list[Difference]) -> list[str]:
    lines: list[str] = []
    for difference in differences:
        grouping = "label" if difference.kind == "concept" else "field name"
        if difference.reason:
            state = "accounted for"
        elif difference.corrections:
            state = "correction declared"
        else:
            state = "OPEN"
        lines.append(
            f"  {grouping} {difference.key!r} — {difference.prop} differs "
            f"[{state}]"
        )
        for ident, value in difference.values.items():
            lines.append(f"      {ident}: {render_value(value)}")
        if difference.reason:
            lines.append(f"      reason: {difference.reason}")
            continue
        for correction in difference.corrections:
            if correction.verdict == "wrong":
                target = (
                    f" and must become {render_value(correction.becomes)}"
                    if correction.has_becomes
                    else " and must lose that wording"
                )
                lines.append(
                    f"      correction: {correction.ident} declares "
                    f"{correction.pin_text()}{target}"
                )
            else:
                lines.append(
                    f"      correction: {correction.ident} declares "
                    f"{correction.pin_text()} and is RIGHT — {correction.disagrees}"
                )
            lines.append(f"          blocked on: {correction.blocked_on}")
        if difference.corrections:
            # Said at the difference, not only in the file's comment: the reader who
            # sees a verdict beside a disagreement is the one who has to be told that
            # the disagreement is still here.
            lines.append(
                "      a correction states what is wrong; it does not make the packages agree, "
                "so this difference is still counted and --strict still fails on it"
            )
            continue
        stanza = json.dumps(difference.stanza(), indent=2, ensure_ascii=False)
        lines.append("      to close it, decide it is legitimate and add to the reasons file:")
        lines.extend(f"      {line}" for line in stanza.splitlines())
    return lines


def report_corrections(corrections: list[Correction]) -> list[str]:
    lines: list[str] = []
    for correction in corrections:
        lines.append(f"  [{correction.verdict}] {correction.package} · {correction.pointer}")
        if correction.verdict == "wrong" and correction.has_becomes:
            lines.append(
                f"      declares {correction.pin_text()}, and must become "
                f"{render_value(correction.becomes)}"
            )
        else:
            lines.append(f"      declares {correction.pin_text()}")
        if correction.verdict == "right":
            lines.append(f"      disagrees: {correction.disagrees}")
        lines.append(f"      blocked on: {correction.blocked_on}")
        lines.append(f"      why: {correction.text}")
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
    documents: dict[str, dict[str, Any]] = {}
    faults: list[str] = []
    examined = 0
    for path in descriptors:
        try:
            document, package_fields = read_package(path)
        except CheckError as exc:
            # Collected, not returned: four unreadable descriptors name four, and the
            # `examined N of M` line below can only be worth printing if the two
            # numbers are able to differ.
            faults.append(str(exc))
            continue
        documents[path.parent.name] = document
        fields.extend(package_fields)
        examined += 1

    try:
        agreement = read_agreement(reasons_path)
        reasons = load_reasons(reasons_path, agreement)
    except CheckError as exc:
        faults.append(str(exc))
        agreement, reasons = {}, []
    try:
        corrections = load_corrections(reasons_path, agreement)
    except CheckError as exc:
        faults.append(str(exc))
        corrections = []
    judge(corrections, documents)

    concepts, concept_groups = concept_differences(fields)
    names, name_groups = name_differences(fields)
    differences = concepts + names
    attach(differences, reasons)
    attach_corrections(differences, corrections)
    stale = [reason for reason in reasons if not reason.used]
    live = [item for item in corrections if item.state == "declared"]
    lapsed = [item for item in corrections if item.state != "declared"]
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
                    "corrections": [
                        {
                            "package": item.package,
                            "pointer": item.pointer,
                            "verdict": item.verdict,
                            "declares": item.contains if item.contains is not None else item.pinned,
                            "declares_is_substring": item.contains is not None,
                            "becomes": item.becomes if item.has_becomes else None,
                            "blocked_on": item.blocked_on,
                            "disagrees": item.disagrees or None,
                            "reason": item.text,
                            "state": item.state,
                        }
                        for item in corrections
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
    corrected = [item for item in open_differences if item.corrections]
    unlooked = [item for item in open_differences if not item.corrections]
    if differences:
        print(
            f"\nlabel agreement: {len(differences)} difference(s) between packages "
            f"describing the same thing — {len(differences) - len(open_differences)} closed by a "
            f"stated reason, {len(corrected)} carrying a declared correction and still "
            f"disagreeing, {len(unlooked)} with no stated reason\n"
        )
        for line in report(differences):
            print(line)

    if live:
        print(
            f"\nlabel agreement: {len(live)} declaration(s) adjudicated in {reasons_path}. A "
            f"correction is NOT a reason: it names the side that is wrong and the act that "
            f"lands the fix, the difference goes on being reported, and --strict goes on "
            f"failing on it. It reddens when the value moves and when the fix arrives.\n"
        )
        for line in report_corrections(live):
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

    if lapsed:
        print(
            f"\nlabel agreement: {len(lapsed)} declared correction(s) no longer describe what "
            f"the package declares. A correction is pinned to the value it judges, so one that "
            f"matches nothing has either LANDED — delete it, the defect is gone — or the "
            f"declaration MOVED and the verdict is about a value nothing carries any more:\n",
            file=sys.stderr,
        )
        for item in lapsed:
            print(f"  {item.where}: {item.package} · {item.pointer}", file=sys.stderr)
            print(f"      it pins {item.pin_text()}", file=sys.stderr)
            if item.state != "missing":
                print(
                    f"      the package declares {render_value(item.found)}", file=sys.stderr
                )
            print(f"      {item.state}: {item.detail}", file=sys.stderr)
        return EXIT_DISAGREEMENT

    if args.strict and open_differences:
        print(
            f"\nlabel agreement: FAILED under --strict — {len(open_differences)} "
            f"difference(s) carry no stated reason, {len(corrected)} of them adjudicated by a "
            f"correction that names the defect without fixing it",
            file=sys.stderr,
        )
        return EXIT_DISAGREEMENT

    if not differences:
        print("\nlabel agreement: every concept is described the same way by every package")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
