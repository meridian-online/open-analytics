#!/usr/bin/env python3
"""Hold a regenerated descriptor to the hand-held constraints its predecessor carried.

A published `datasets/<slug>/datapackage.json` is written by a describe step: finetype
emits a `type` and `constraints` for every column, and `descriptor.overrides.json` beside
it overlays the values a person decided. Overrides win, so a hand-held constraint
survives a regeneration only while that overlay is applied. When it is not — the sidecar
entry is deleted, a column is renamed past its entry, the describe step changes how it
merges — the descriptor quietly carries finetype's own constraint instead, and the
package is looser than it was.

Nothing else reads that. `check_descriptors.py` holds the descriptor to its bytes, and a
looser constraint still fits the bytes. `check_label_agreement.py` compares fields that
share a label, and a field that is the only one carrying its label has nothing to be
compared with. The self-description rule in `check_descriptors.py` does redden on a
loosened descriptor, but only because the published object's footer still carries the
old one: it fires on any difference at all and goes quiet as soon as the loosened
descriptor is stamped into the object, which is the publish step. A baseline that a
restamp moves cannot ratchet anything. This one is read from git, so it does not move.

What it asserts, per package, for the fields in `datasets/<slug>/datapackage.json`:

  1. replaced — a constraint the sidecar held at `--base` is no longer what the
                descriptor carries, and the sidecar does not declare the new value
                either. An emitted value has replaced a hand-held one. A person who
                means to move a constraint moves it in the sidecar, where the diff
                shows it, and the descriptor follows; that is reported, not refused.
                A column dropped together with its sidecar entry is reported, not
                refused. A column dropped while its entry stays is refused.
  2. ignored  — a constraint the sidecar declares now is not what the descriptor
                carries. Needs no `--base`: the regeneration did not apply the overlay.

And it lists every field that changed against `--base`, whether or not it is refused:
added and removed columns, and every property of a surviving field that moved — `type`,
`x-finetype-label`, `x-finetype-confidence`, each `constraints` key. The last
regeneration moved four confidences and added a column and nobody noticed until a
reviewer read the diff. A listed change is information; only 1 and 2 set the exit code.

SCOPE, STATED. Only `constraints` are held. A hand-held `type` or `description` in the
sidecar is not. A person who removes a constraint from the sidecar while the descriptor
still carries it is listed as released and not refused, because the descriptor has not
moved yet; the regeneration that moves it is then a change against a sidecar that no
longer holds the value, and is only listed. There is no way to delete a hand-held
constraint outright and pass: declaring the looser value in the sidecar is the way to
move one, and a pure deletion has no route yet. Only the working tree is read at HEAD,
so a regeneration is checked before it is committed.

Exit codes, matching check_descriptors.py's convention:

  0  no hand-held constraint was replaced or ignored
  1  at least one was, each named with package, field, key and both values
  2  the check could not run — `--base` does not resolve to a commit, the tree is not a
     git work tree, a descriptor or sidecar is unreadable, or no datasets were found.
     A base that cannot be read is never a pass: a shallow clone has no `origin/main`.

    check_constraint_ratchet.py                    against origin/main
    check_constraint_ratchet.py --base HEAD~1      against the previous commit
    check_constraint_ratchet.py --json OUT         write the findings as JSON as well
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

EXIT_OK = 0
EXIT_REGRESSION = 1
EXIT_ERROR = 2

DEFAULT_BASE = "origin/main"
SIDECAR = "descriptor.overrides.json"

# Properties of a field that are not compared one by one: `constraints` is expanded to its
# keys, `name` is the join key. `description` is compared, but its prose is not printed.
EXPANDED = ("constraints", "name")
PROSE = "description"


class CheckError(Exception):
    """The check could not run. Distinct from a finding, and never a pass."""


class _Absent:
    def __repr__(self) -> str:
        return "(absent)"


ABSENT = _Absent()


def same(left: Any, right: Any) -> bool:
    """JSON equality that does not call `true` and `1` the same value.

    Python's `True == 1` would let `required: true` be replaced by `required: 1` unseen.
    """
    if left is ABSENT or right is ABSENT:
        return left is right
    return json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)


def render(value: Any) -> str:
    if value is ABSENT:
        return "(absent)"
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return text if len(text) <= 200 else text[:197] + "..."


@dataclass
class Finding:
    package: str
    field: str
    key: str
    kind: str  # "replaced" | "ignored"
    was: str  # what was held by hand
    now: str  # what the descriptor carries
    declared: str  # what the sidecar declares now

    def line(self) -> str:
        where = f"{self.package}.{self.field}: constraints.{self.key}"
        if self.kind == "replaced":
            return (
                f"    {where}: the predecessor held {self.was} by hand; the descriptor now carries "
                f"{self.now} and the sidecar declares {self.declared} — an emitted value has "
                "replaced a hand-held one"
            )
        return (
            f"    {where}: the sidecar declares {self.declared}; the descriptor carries {self.now} — "
            "the regeneration did not apply the override"
        )


@dataclass
class Change:
    package: str
    field: str
    what: str
    was: str
    now: str

    def line(self) -> str:
        return f"    {self.package}.{self.field}: {self.what}: {self.was} → {self.now}"


def run_git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)
    except OSError as exc:
        raise CheckError(f"cannot run git: {exc}") from exc


def repo_root(datasets_dir: Path) -> Path:
    done = run_git(datasets_dir, "rev-parse", "--show-toplevel")
    if done.returncode != 0:
        raise CheckError(
            f"{datasets_dir} is not inside a git work tree, so there is no predecessor to compare "
            f"against: {done.stderr.strip()}"
        )
    return Path(done.stdout.strip()).resolve()


def resolve_base(root: Path, base: str) -> str:
    done = run_git(root, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}")
    if done.returncode != 0:
        raise CheckError(
            f"--base {base!r} does not resolve to a commit in {root}; a shallow clone has no "
            "`origin/main` until it is fetched, and a check with no predecessor to read must not pass"
        )
    return done.stdout.strip()


def parse_json(text: str, where: str) -> dict[str, Any]:
    try:
        document = json.loads(text)
    except ValueError as exc:
        raise CheckError(f"{where}: not valid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise CheckError(f"{where}: expected a JSON object")
    return document


def read_working(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return parse_json(path.read_text(encoding="utf-8"), str(path))


def read_at(root: Path, sha: str, path: Path) -> dict[str, Any] | None:
    """The file as `sha` had it, or None when `sha` had no such file."""
    rel = path.resolve().relative_to(root).as_posix()
    listed = run_git(root, "ls-tree", "--name-only", sha, "--", rel)
    if listed.returncode != 0:
        raise CheckError(f"git ls-tree {sha[:12]} -- {rel} failed: {listed.stderr.strip()}")
    if not listed.stdout.strip():
        return None
    shown = run_git(root, "show", f"{sha}:{rel}")
    if shown.returncode != 0:
        raise CheckError(f"git show {sha[:12]}:{rel} failed: {shown.stderr.strip()}")
    return parse_json(shown.stdout, f"{rel} at {sha[:12]}")


def discover(datasets_dir: Path, only: Iterable[str] | None) -> list[Path]:
    paths = sorted(datasets_dir.glob("*/datapackage.json"))
    if only:
        wanted = set(only)
        paths = [path for path in paths if path.parent.name in wanted]
        missing = wanted - {path.parent.name for path in paths}
        if missing:
            raise CheckError(f"no descriptor for dataset(s): {', '.join(sorted(missing))}")
    if not paths:
        raise CheckError(f"no datasets/*/datapackage.json under {datasets_dir}")
    return paths


def fields_of(descriptor: dict[str, Any], where: str) -> dict[str, dict[str, Any]]:
    """Every field, by name. The sidecar addresses a field by name alone, so a name that two
    resources share cannot be addressed and is refused rather than merged."""
    fields: dict[str, dict[str, Any]] = {}
    for resource in descriptor.get("resources") or []:
        for field in (resource.get("schema") or {}).get("fields") or []:
            name = field.get("name")
            if not isinstance(name, str):
                raise CheckError(f"{where}: a field declares no name")
            if name in fields:
                raise CheckError(f"{where}: field {name!r} appears in more than one resource")
            fields[name] = field
    return fields


def held_by(sidecar: dict[str, Any] | None, where: str) -> dict[str, dict[str, Any]]:
    """The constraints the sidecar declares, per field name."""
    entries = (sidecar or {}).get("fields") or {}
    if not isinstance(entries, dict):
        raise CheckError(f"{where}: `fields` is not an object keyed by field name")
    held: dict[str, dict[str, Any]] = {}
    for name, entry in entries.items():
        constraints = (entry or {}).get("constraints") if isinstance(entry, dict) else None
        if constraints:
            held[name] = dict(constraints)
    return held


def carried(fields: dict[str, dict[str, Any]], name: str, key: str) -> Any:
    return ((fields.get(name) or {}).get("constraints") or {}).get(key, ABSENT)


def refusals(
    package: str,
    base_held: dict[str, dict[str, Any]] | None,
    head_fields: dict[str, dict[str, Any]],
    head_held: dict[str, dict[str, Any]],
) -> tuple[list[Finding], list[Change]]:
    findings: list[Finding] = []
    released: list[Change] = []
    reported: set[tuple[str, str]] = set()

    for name, constraints in (base_held or {}).items():
        for key, was in constraints.items():
            now = carried(head_fields, name, key)
            declared = head_held.get(name, {}).get(key, ABSENT)
            if same(now, was):
                if declared is ABSENT:
                    released.append(
                        Change(package, name, f"constraints.{key}", render(was), "released by the sidecar")
                    )
                continue
            if declared is not ABSENT and same(declared, now):
                continue  # moved in the sidecar, where the diff shows it; listed as a change
            if name not in head_fields and name not in head_held:
                continue  # the column went with its entry; listed as a change
            findings.append(Finding(package, name, key, "replaced", render(was), render(now), render(declared)))
            reported.add((name, key))

    for name, constraints in head_held.items():
        for key, declared in constraints.items():
            now = carried(head_fields, name, key)
            if (name, key) not in reported and not same(now, declared):
                findings.append(Finding(package, name, key, "ignored", render(declared), render(now), render(declared)))
    return findings, released


def changes_between(
    package: str, base_fields: dict[str, dict[str, Any]], head_fields: dict[str, dict[str, Any]]
) -> list[Change]:
    """Every field that differs from its predecessor, one entry per property that moved."""
    changes: list[Change] = []
    for name, field in head_fields.items():
        if name not in base_fields:
            summary = {k: v for k, v in field.items() if k != PROSE}
            changes.append(Change(package, name, "added", "(absent)", render(summary)))
            continue
        before = base_fields[name]
        for key in sorted((set(before) | set(field)) - set(EXPANDED)):
            old, new = before.get(key, ABSENT), field.get(key, ABSENT)
            if same(old, new):
                continue
            if key == PROSE:
                changes.append(Change(package, name, key, "(prose)", "(prose changed)"))
            else:
                changes.append(Change(package, name, key, render(old), render(new)))
        old_constraints, new_constraints = before.get("constraints") or {}, field.get("constraints") or {}
        for key in sorted(set(old_constraints) | set(new_constraints)):
            old, new = old_constraints.get(key, ABSENT), new_constraints.get(key, ABSENT)
            if not same(old, new):
                changes.append(Change(package, name, f"constraints.{key}", render(old), render(new)))
    for name, field in base_fields.items():
        if name not in head_fields:
            summary = {k: v for k, v in field.items() if k != PROSE}
            changes.append(Change(package, name, "removed", render(summary), "(absent)"))
    return changes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--datasets-dir", default="datasets", type=Path)
    parser.add_argument("--only", action="append", metavar="SLUG", help="restrict to one dataset slug (repeatable)")
    parser.add_argument("--base", default=DEFAULT_BASE, help=f"the predecessor to hold the tree to (default: {DEFAULT_BASE})")
    parser.add_argument("--json", metavar="FILE", type=Path, help="also write the findings as JSON")
    args = parser.parse_args(argv)

    findings: list[Finding] = []
    changes: list[Change] = []
    new_packages: list[str] = []
    held_count = 0
    try:
        descriptor_paths = discover(args.datasets_dir, args.only)
        root = repo_root(args.datasets_dir)
        sha = resolve_base(root, args.base)
        for descriptor_path in descriptor_paths:
            package = descriptor_path.parent.name
            sidecar_path = descriptor_path.with_name(SIDECAR)
            head_fields = fields_of(read_working(descriptor_path) or {}, str(descriptor_path))
            head_held = held_by(read_working(sidecar_path), str(sidecar_path))
            held_count += sum(len(keys) for keys in head_held.values())
            base_descriptor = read_at(root, sha, descriptor_path)
            if base_descriptor is None:
                new_packages.append(package)
                base_held = None
            else:
                base_held = held_by(read_at(root, sha, sidecar_path), f"{SIDECAR} at {sha[:12]}")
                changes.extend(changes_between(package, fields_of(base_descriptor, f"{package} at {sha[:12]}"), head_fields))
            found, released = refusals(package, base_held, head_fields, head_held)
            findings.extend(found)
            changes.extend(released)
    except CheckError as exc:
        print(f"constraint ratchet: {exc}", file=sys.stderr)
        return EXIT_ERROR

    checked = ", ".join(path.parent.name for path in descriptor_paths)
    against = f"{args.base} ({sha[:12]})"
    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "base": args.base,
                    "base_sha": sha,
                    "checked": checked,
                    "new_packages": new_packages,
                    "violations": [asdict(f) for f in findings],
                    "changes": [asdict(c) for c in changes],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    if changes:
        print(f"constraint ratchet: {len(changes)} field change(s) against {against}, listed whether or not refused\n")
        for change in changes:
            print(change.line())
        print()
    if new_packages:
        print(f"constraint ratchet: new since {against}, nothing to hold them to: {', '.join(new_packages)}\n")

    if not findings:
        print(
            f"constraint ratchet: {len(descriptor_paths)} descriptor(s) ({checked}) held to {against}; "
            f"{held_count} hand-held constraint(s) carried, none replaced or ignored"
        )
        return EXIT_OK

    print(f"constraint ratchet: {len(findings)} hand-held constraint(s) replaced or ignored\n")
    for finding in findings:
        print(finding.line())
    print(
        f"\nconstraint ratchet: FAILED — {len(findings)} hand-held constraint(s) replaced or ignored across "
        f"{len({f.package for f in findings})} of {len(descriptor_paths)} descriptor(s) ({checked}) against "
        f"{against}; declare the value you mean in {SIDECAR} so the descriptor follows it, or restore it"
    )
    return EXIT_REGRESSION


if __name__ == "__main__":
    sys.exit(main())
