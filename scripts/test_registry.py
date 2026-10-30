#!/usr/bin/env python3
# /// script
# requires-python = "==3.12.*"
# dependencies = []
# ///
"""The registry's fields agree with each other, and the check can fail.

`datasets/registry.json` says in prose how a dataset is addressed, and it carries the same
rules as data: each entry's `moves_to` names a namespace, `namespaces` lists the namespaces,
and `reserved` lists the names no one can register. Prose cannot be held to the data it
describes, so a namespace added without being reserved, a `moves_to` that points at a
namespace the registry does not list, or two entries moving to one address would pass every
other job on this page. `problems()` reads those rules back out of the document and names
each one that fails.

It asserts structure and not today's values. An entry's `schema` and `name` are its live
address and change at the cutover, when the entry takes its `moves_to` address, so no
`schema`/`name` pair is written down here.

The public-repo rule is held too: the registry names no planning reference and no local
path. The words are written as one-letter character classes so that this file does not
contain them either.

Nothing here touches the network, and the shipped registry is only read.
"""

from __future__ import annotations

import copy
import json
import re
import unittest
from pathlib import Path
from typing import Any

REGISTRY = Path(__file__).resolve().parent.parent / "datasets" / "registry.json"

ADDRESS = re.compile(r"^[a-z][a-z0-9_]*/[a-z][a-z0-9_]*$")
# `<schema>.<name>`: what an entry's old address looks like once it joins `aliases`.
ALIAS = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
KINDS = ("held", "registered", "library")
LEAKS = re.compile(r"bearin[g]|j6d[m]|kat[a]|/Volume[s]/|/User[s]/", re.IGNORECASE)


# A registry that breaks no rule, with invented names: the cases below each break one rule in
# a copy of it, so that a failure names that rule alone and not whatever the shipped registry
# happens to hold.
GOOD: dict[str, Any] = {
    "namespaces": {
        "acme": {"kind": "held", "issuer": "Acme Institute", "claimed": False},
        "library": {"kind": "library"},
    },
    "reserved": ["library", "meridian", "acme", "new"],
    "datasets": [
        {"uid": "u-1", "schema": "source", "name": "alpha", "aliases": [], "moves_to": "acme/alpha_2022"},
        {"uid": "u-2", "schema": "crosswalk", "name": "beta", "aliases": [], "moves_to": "library/beta"},
    ],
}


def problems(registry: dict[str, Any], text: str | None = None) -> list[str]:
    """Every rule the registry breaks, each as one sentence. Empty when it breaks none."""
    found: list[str] = []
    datasets = registry.get("datasets") or []
    namespaces = registry.get("namespaces")
    reserved = registry.get("reserved")

    if not isinstance(namespaces, dict) or not namespaces:
        found.append("`namespaces` is missing or empty")
        namespaces = {}
    if not isinstance(reserved, list) or not reserved:
        found.append("`reserved` is missing or empty")
        reserved = []

    uids = [entry.get("uid") for entry in datasets]
    for uid in sorted({str(u) for u in uids if uids.count(u) > 1}):
        found.append(f"uid {uid} belongs to more than one entry")

    targets = [entry.get("moves_to") for entry in datasets]
    for entry in datasets:
        label = f"{entry.get('schema')}.{entry.get('name')}"
        target = entry.get("moves_to")
        if not isinstance(target, str) or not ADDRESS.match(target):
            found.append(f"{label}: moves_to {target!r} is not `<namespace>/<name>`")
            continue
        namespace = target.split("/")[0]
        if namespace not in namespaces:
            found.append(f"{label}: moves_to namespace {namespace!r} is not a key of `namespaces`")
        if targets.count(target) > 1:
            found.append(f"{label}: moves_to {target!r} is claimed by more than one entry")
        for alias in entry.get("aliases") or []:
            if not isinstance(alias, str) or not ALIAS.match(alias):
                found.append(f"{label}: alias {alias!r} is not `<schema>.<name>`")

    for name in sorted(reserved):
        if reserved.count(name) > 1:
            found.append(f"reserved lists {name!r} more than once")
    for name, space in namespaces.items():
        if name not in reserved:
            found.append(f"namespace {name!r} is not in `reserved`")
        kind = space.get("kind")
        if kind not in KINDS:
            found.append(f"namespace {name!r}: kind {kind!r} is not one of {', '.join(KINDS)}")
        if kind == "held":
            if not isinstance(space.get("claimed"), bool):
                found.append(f"namespace {name!r}: a held namespace carries `claimed`, true or false")
            if not space.get("issuer"):
                found.append(f"namespace {name!r}: a held namespace names its `issuer`")

    leaked = LEAKS.findall(text if text is not None else json.dumps(registry, ensure_ascii=False))
    if leaked:
        found.append(f"the registry is public and names {sorted(set(map(str.lower, leaked)))}")
    return found


class RegistryAgreesWithItself(unittest.TestCase):
    def test_the_shipped_registry_breaks_no_rule(self) -> None:
        text = REGISTRY.read_text(encoding="utf-8")
        self.assertEqual(problems(json.loads(text), text), [])

    def test_the_fixture_the_cases_start_from_breaks_no_rule(self) -> None:
        self.assertEqual(problems(self.good()), [])

    # ------------------------------------------------- the check can fail, and on what

    def good(self) -> dict[str, Any]:
        return copy.deepcopy(GOOD)

    def test_a_repeated_uid_is_named(self) -> None:
        registry = self.good()
        registry["datasets"][1]["uid"] = registry["datasets"][0]["uid"]
        self.assertEqual(
            problems(registry),
            [f"uid {registry['datasets'][0]['uid']} belongs to more than one entry"],
        )

    def test_a_moves_to_that_is_not_an_address_is_named(self) -> None:
        for bad in ("alpha", "Acme/alpha", "acme/alpha/extra", "acme.alpha", "acme/2alpha", ""):
            with self.subTest(bad=bad):
                registry = self.good()
                registry["datasets"][0]["moves_to"] = bad
                self.assertEqual(len(problems(registry)), 1)
                self.assertIn("is not `<namespace>/<name>`", problems(registry)[0])

    def test_a_moves_to_into_an_unlisted_namespace_is_named(self) -> None:
        registry = self.good()
        registry["datasets"][0]["moves_to"] = "nowhere/alpha"
        self.assertEqual(len(problems(registry)), 1)
        self.assertIn("'nowhere' is not a key of `namespaces`", problems(registry)[0])

    def test_two_entries_moving_to_one_address_are_named(self) -> None:
        registry = self.good()
        registry["datasets"][1]["moves_to"] = registry["datasets"][0]["moves_to"]
        self.assertEqual(len(problems(registry)), 2)
        self.assertTrue(all("claimed by more than one entry" in line for line in problems(registry)))

    def test_a_namespace_that_is_not_reserved_is_named(self) -> None:
        registry = self.good()
        registry["reserved"].remove("acme")
        self.assertEqual(problems(registry), ["namespace 'acme' is not in `reserved`"])

    def test_a_held_namespace_without_a_claim_state_or_issuer_is_named(self) -> None:
        for field in ("claimed", "issuer"):
            with self.subTest(field=field):
                registry = self.good()
                del registry["namespaces"]["acme"][field]
                self.assertEqual(len(problems(registry)), 1)
                self.assertIn("'acme'", problems(registry)[0])

    def test_a_namespace_of_no_known_kind_is_named(self) -> None:
        registry = self.good()
        registry["namespaces"]["library"]["kind"] = "curated"
        self.assertEqual(len(problems(registry)), 1)
        self.assertIn("kind 'curated'", problems(registry)[0])

    def test_a_missing_namespaces_object_is_named_not_crashed_on(self) -> None:
        registry = self.good()
        del registry["namespaces"]
        self.assertEqual(problems(registry)[0], "`namespaces` is missing or empty")

    def test_an_alias_that_is_not_an_old_address_is_named(self) -> None:
        registry = self.good()
        registry["datasets"][0]["aliases"] = ["source.alpha", "alpha"]
        self.assertEqual(len(problems(registry)), 1)
        self.assertIn("alias 'alpha' is not `<schema>.<name>`", problems(registry)[0])

    def test_a_planning_reference_or_local_path_is_named(self) -> None:
        for leak in ("see bear" + "ing", "j6" + "dm", "Ka" + "ta", "/Vol" + "umes/Dev", "/Us" + "ers/me"):
            with self.subTest(leak=leak):
                registry = self.good()
                registry["note"] = "an invented note " + leak
                self.assertEqual(len(problems(registry)), 1)
                self.assertIn("is public and names", problems(registry)[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
