#!/usr/bin/env python3
"""Hold what the open lake stores to itself and to `main`, and sort what disagrees.

The lake workflow runs this on a push to `main`, weekly, and on demand, over a tree that
`emit_descriptor.py --all --tree DIR --bypass-edge` laid out: `DIR/datasets/` is the
repository's `datasets/` with every published table's descriptor written in from the lake.
It runs the checks that read the lake over that tree:

  descriptors   check_descriptors.py --require-self-description, with the README catalogue
  engine        check_engine_version.py
  labels        check_label_agreement.py --strict
  frictionless  check_frictionless.py
  code_list     check_code_list.py
  joins         crosswalk_join.py, once per package that declares `x-joins`
  ratchet       each stored descriptor against the constraints its committed overrides
                hold at HEAD (check_constraint_ratchet.py's `ignored` rule; there is no
                predecessor descriptor in git to hold it to any more)
  verify        every declared `bytes` and `hash` against the object served, a full
                download of each (publish_dataset.py verify, over per-table descriptors too)

and sorts every finding into one of two kinds.

  awaiting_publish  `main` is ahead of the lake: the table is not published yet, or the
                    repository inputs the finding compares against changed in git after
                    the table's `created`. Reported; the run stays green. This is the
                    expected gap between a merge and its publish.
  drift             the lake contradicts itself, or `main` was not changed since the
                    publish and still disagrees. The run fails.

The inputs a finding compares against are `datasets/<slug>/` for the table's own
Protocol, `README.md` alone for a catalogue finding, `datasets/label-agreement.json`
as well for a label finding, and `datasets/frictionless-rejections.json` as well for a
Frictionless one. Their time is `git log -1 --format=%cI -- <paths>`, so the checkout
needs full history. A check that crashes (exit 2, or a failure no finding explains)
is drift: a check that did not run has not passed.

    lake_check.py --tree DIR --repo . --json FILE [--check NAME ...]

Exit codes: 0 no drift (awaiting-publish findings may be listed); 1 drift; 2 this
script could not run (no tree, no git history).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
JOINS = ROOT / "datasets" / "edgar_gleif" / "scripts" / "crosswalk_join.py"

sys.path.insert(0, str(SCRIPTS))

EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_ERROR = 2

CHECKS = ("descriptors", "engine", "labels", "frictionless", "code_list", "joins", "ratchet", "verify")
AWAITING = "awaiting_publish"
DRIFT = "drift"
Kind = Literal["awaiting_publish", "drift"]


class Unrunnable(Exception):
    """This script cannot run at all; exit 2."""


def classify(slug: str | None, published_at: datetime | None, last_change_at: datetime) -> Kind:
    """`awaiting_publish` when the table is not published, or its inputs changed after `published_at`."""
    if slug is None or published_at is None:
        return AWAITING
    return AWAITING if last_change_at > published_at else DRIFT


def parse_time(text: str | None) -> datetime | None:
    if not text:
        return None
    moment = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


# The time a path last changed when git has no commit for it: never, so a finding about it
# is compared against the publish alone.
NEVER = datetime.min.replace(tzinfo=timezone.utc)


def last_change(repo: Path, paths: list[str]) -> datetime:
    done = subprocess.run(
        ["git", "-C", str(repo), "log", "-1", "--format=%cI", "--", *paths],
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        raise Unrunnable(f"git log in {repo} failed: {done.stderr.strip()}")
    return parse_time(done.stdout) or NEVER


@dataclass
class Subject:
    """A table a finding is about: its Protocol's directory and its descriptor in the tree."""

    slug: str
    descriptor: str | None  # relative to the tree's datasets/, e.g. soc_2018/soc_2018.datapackage.json


@dataclass
class Finding:
    check: str
    detail: str
    subjects: list[Subject] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)  # repository paths beyond datasets/<slug>/
    own_dir: bool = True  # whether datasets/<slug>/ is an input; not for a catalogue finding
    kind: str = ""
    published_at: str | None = None
    last_change_at: str | None = None


def sort_finding(repo: Path, tree: "Tree", finding: Finding) -> Kind:
    """Classify `finding`, recording the times it was judged on.

    A finding with no subject is drift: a crash, or a disagreement about the repository
    alone (an `examples/` licence), which no publish can close. A finding about several
    tables is awaiting a publish when any one of them is.
    """
    if not finding.subjects:
        finding.kind = DRIFT
        return DRIFT
    kinds = []
    for subject in finding.subjects:
        published = tree.created(subject.descriptor)
        paths = [*([f"datasets/{subject.slug}/"] if finding.own_dir else []), *finding.inputs]
        changed = last_change(repo, paths)
        kinds.append(classify(subject.slug, published, changed))
        finding.published_at = published.isoformat() if published else None
        finding.last_change_at = changed.isoformat() if changed is not NEVER else None
        if kinds[-1] == AWAITING:
            break
    finding.kind = AWAITING if AWAITING in kinds else DRIFT
    return finding.kind


class Tree:
    """The emitted descriptors: where each is, when its table was published, which object it names."""

    def __init__(self, datasets: Path) -> None:
        self.datasets = datasets
        self.documents: dict[str, dict[str, Any]] = {}
        for path in sorted({*datasets.glob("*/datapackage.json"), *datasets.glob("*/*.datapackage.json")}):
            self.documents[path.relative_to(datasets).as_posix()] = json.loads(path.read_text(encoding="utf-8"))

    def created(self, rel: str | None) -> datetime | None:
        document = self.documents.get(rel) if rel else None
        return parse_time(document.get("created")) if document else None

    def subject_for_path(self, path: str) -> Subject | None:
        """The subject a check's reported descriptor path names, when it is one in this tree."""
        try:
            rel = Path(path).resolve().relative_to(self.datasets.resolve()).as_posix()
        except ValueError:
            return None
        return Subject(rel.split("/", 1)[0], rel if rel in self.documents else None)

    def subject_for_object(self, link: str) -> Subject | None:
        """The subject whose resource is the object `link` names, query string aside."""
        plain = link.split("#", 1)[0].split("?", 1)[0]
        for rel, document in self.documents.items():
            for resource in document.get("resources") or []:
                if str(resource.get("path", "")).split("?", 1)[0] == plain:
                    return Subject(rel.split("/", 1)[0], rel)
        return None

    def package(self, slug: str) -> Subject:
        rel = f"{slug}/datapackage.json"
        return Subject(slug, rel if rel in self.documents else None)


def protocol_tables(repo: Path) -> dict[str, str]:
    """`<namespace>/<name>` to the Protocol slug that publishes it, from each `arcform.yaml`."""
    import emit_descriptor

    return {table.replace(".", "/", 1): slug for slug, table, _ in emit_descriptor.protocol_tables(repo / "datasets")}


# ------------------------------------------------------------------ the checks


@dataclass
class Ran:
    check: str
    status: int
    log: str


def run(check: str, argv: list[str]) -> Ran:
    done = subprocess.run([sys.executable, *argv], capture_output=True, text=True, check=False)
    return Ran(check, done.returncode, (done.stdout + done.stderr).rstrip())


def crashed(ran: Ran, parsed: list[Finding]) -> list[Finding]:
    """A failure no finding explains is a crash, and a crash is drift."""
    if ran.status == 0 or (ran.status == 1 and parsed):
        return parsed
    tail = "\n".join(ran.log.splitlines()[-5:])
    return [*parsed, Finding(ran.check, f"exit {ran.status} with no finding this script can attribute:\n{tail}")]


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def check_descriptors(tree: Tree, repo: Path, work: Path, links: dict[str, str]) -> tuple[Ran, list[Finding]]:
    out = work / "descriptors.json"
    ran = run(
        "descriptors",
        [
            str(SCRIPTS / "check_descriptors.py"),
            "--datasets-dir", str(tree.datasets),
            "--examples-dir", str(repo / "examples"),
            "--readme", str(repo / "README.md"),
            "--require-self-description",
            "--json", str(out),
        ],
    )
    report = read_json(out)
    found = []
    for item in [*report.get("violations", []), *report.get("pending", [])]:
        detail = f"{item['rule']}: {item['detail']}"
        if item["rule"].startswith("catalogue."):
            subject = tree.subject_for_path(item["resource"]) or tree.subject_for_object(item["resource"])
            if subject is None:
                # A row linking an object the lake does not hold: awaiting its first publish
                # when a Protocol here publishes that address, otherwise a wrong link.
                address = item["resource"].split("?", 1)[0].split("://", 1)[-1].split("/", 1)[-1]
                slug = links.get(address.removesuffix(".parquet"))
                subject = Subject(slug, None) if slug else None
            found.append(
                Finding("descriptors", f"{item['resource']}: {detail}", [subject] if subject else [], ["README.md"], own_dir=False)
            )
            continue
        subject = tree.subject_for_path(item["package"])
        found.append(Finding("descriptors", f"{item['package']} :: {item['resource']}: {detail}", [subject] if subject else []))
    return ran, crashed(ran, found)


def check_engine(tree: Tree, work: Path) -> tuple[Ran, list[Finding]]:
    out = work / "engine.json"
    ran = run("engine", [str(SCRIPTS / "check_engine_version.py"), "--datasets-dir", str(tree.datasets), "--json", str(out)])
    found = []
    for item in read_json(out).get("mismatches", []):
        subject = tree.subject_for_path(item["package"])
        detail = f"{item['package']} :: {item['resource']}: written by {item['writer_version']}, pinned {item['reader_pin']} ({item['pin_source']})"
        found.append(Finding("engine", detail, [subject] if subject else []))
    return ran, crashed(ran, found)


def check_labels(tree: Tree, work: Path) -> tuple[Ran, list[Finding]]:
    out = work / "labels.json"
    ran = run("labels", [str(SCRIPTS / "check_label_agreement.py"), "--strict", "--datasets-dir", str(tree.datasets), "--json", str(out)])
    report = read_json(out)

    def subjects(values: dict[str, Any]) -> list[Subject]:
        return [tree.package(slug) for slug in sorted({ident.split(".", 1)[0] for ident in values})]

    found = []
    for item in report.get("differences", []):
        if not item.get("reason"):
            found.append(Finding("labels", f"{item['kind']} {item['key']!r}, {item['property']}: no stated reason", subjects(item["values"])))
    for item in report.get("stale_reasons", []):
        found.append(Finding("labels", f"{item['kind']} {item['key']!r}, {item['property']}: a stated reason matches no difference", subjects(item["values"])))
    for item in report.get("corrections", []):
        if item.get("state") != "declared":
            found.append(Finding("labels", f"{item['package']} {item['pointer']}: correction {item['state']}", [tree.package(item["package"])]))
    for finding in found:
        finding.inputs = ["datasets/label-agreement.json"]
    return ran, crashed(ran, found)


def check_frictionless(tree: Tree, work: Path) -> tuple[Ran, list[Finding]]:
    out = work / "frictionless.json"
    ran = run("frictionless", [str(SCRIPTS / "check_frictionless.py"), "--datasets-dir", str(tree.datasets), "--json", str(out)])
    found = []
    for fault in read_json(out).get("faults", []):
        slug = fault.split(":", 1)[0]
        subject = tree.package(slug) if (tree.datasets / slug).is_dir() else None
        found.append(Finding("frictionless", fault, [subject] if subject else [], ["datasets/frictionless-rejections.json"]))
    return ran, crashed(ran, found)


def check_code_list(tree: Tree, work: Path) -> tuple[Ran, list[Finding]]:
    out = work / "code_list.json"
    ran = run("code_list", [str(SCRIPTS / "check_code_list.py"), "--datasets-dir", str(tree.datasets), "--json", str(out)])
    found = []
    for slug, lines in (read_json(out).get("files") or {}).items():
        for line in lines:
            found.append(Finding("code_list", f"{slug}: {line}", [tree.package(slug)]))
    return ran, crashed(ran, found)


def check_joins(tree: Tree) -> tuple[list[Ran], list[Finding]]:
    declaring = sorted(rel.split("/", 1)[0] for rel, doc in tree.documents.items() if doc.get("x-joins") and rel.endswith("/datapackage.json"))
    runs, found = [], []
    for slug in declaring:
        ran = run("joins", [str(JOINS), "--datasets-dir", str(tree.datasets), "--package", slug])
        runs.append(ran)
        if ran.status == 1:
            tail = "\n".join(ran.log.splitlines()[-5:])
            found.append(Finding("joins", f"{slug}: a declared join fails against the published bytes:\n{tail}", [tree.package(slug)]))
        else:
            found.extend(crashed(ran, []))
    return runs, found


def check_ratchet(tree: Tree, repo: Path) -> list[Finding]:
    """Each stored descriptor against the constraints its committed overrides hold at HEAD."""
    import check_constraint_ratchet as ratchet

    found = []
    for rel, document in tree.documents.items():
        slug, name = rel.split("/", 1)
        sidecar = ratchet.SIDECAR if name == "datapackage.json" else name.replace(".datapackage.json", ".overrides.json")
        shown = subprocess.run(
            ["git", "-C", str(repo), "show", f"HEAD:datasets/{slug}/{sidecar}"], capture_output=True, text=True, check=False
        )
        if shown.returncode != 0:
            continue  # no overrides committed for this table, so nothing is held by hand
        try:
            held = ratchet.held_by(ratchet.parse_json(shown.stdout, f"{sidecar} at HEAD"), sidecar)
            fields = ratchet.fields_of(document, rel)
        except ratchet.CheckError as exc:
            found.append(Finding("ratchet", f"{rel}: {exc}"))
            continue
        refused, _ = ratchet.refusals(rel, None, fields, held)
        for item in refused:
            found.append(Finding("ratchet", item.line(), [Subject(slug, rel)]))
    return found


def check_verify(tree: Tree) -> list[Finding]:
    """Every declared `bytes` and `hash` against a full download of the object served."""
    import publish_dataset as pd

    found = []
    for rel, document in tree.documents.items():
        subject = Subject(rel.split("/", 1)[0], rel)
        for resource in document.get("resources") or []:
            name = resource.get("name") or rel
            declared = pd.declared_measurement(resource)
            try:
                url = pd.resource_url(resource, tree.datasets / rel)
                if declared is None:
                    raise pd.PublishError(f"declares no bytes/hash for {url}")
                served = pd.measure_url(url)
            except pd.PublishError as exc:
                found.append(Finding("verify", f"{rel} :: {name}: {exc}", [subject]))
                continue
            if served != declared:
                found.append(
                    Finding("verify", f"{rel} :: {name}: declares {declared.describe()}; {url} serves {served.describe()}", [subject])
                )
            else:
                print(f"verify: {rel} :: {name} — {served.describe()}")
    return found


# ------------------------------------------------------------------ the report


def summary(drift: list[Finding], awaiting: list[Finding], statuses: dict[str, Any]) -> str:
    lines = ["### Lake check", ""]
    lines.append("| check | exit |")
    lines.append("|---|---|")
    lines.extend(f"| {name} | {status} |" for name, status in statuses.items())
    for title, items in ((f"Drift ({len(drift)}) — the run fails", drift), (f"Awaiting publish ({len(awaiting)}) — reported, not failed on", awaiting)):
        lines += ["", f"#### {title}", ""]
        if not items:
            lines.append("None.")
        for item in items:
            when = f" (published {item.published_at}, inputs last changed {item.last_change_at})" if item.subjects else ""
            lines.append(f"- **{item.check}**{when}")
            lines.append("")
            lines.append("  ```")
            lines.extend(f"  {line}" for line in item.detail.splitlines())
            lines.append("  ```")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tree", type=Path, required=True, help="the directory emit_descriptor.py --tree wrote")
    ap.add_argument("--repo", type=Path, default=ROOT, help="the checkout: README.md, examples/, overrides and git history")
    ap.add_argument("--json", type=Path, help="write every finding, sorted, as JSON")
    ap.add_argument("--check", action="append", choices=CHECKS, help="run only this check (repeatable; default every one)")
    args = ap.parse_args(argv)
    wanted = args.check or list(CHECKS)
    repo = args.repo.resolve()

    try:
        datasets = args.tree / "datasets"
        if not datasets.is_dir():
            raise Unrunnable(f"no datasets/ under {args.tree}; run emit_descriptor.py --all --tree first")
        tree = Tree(datasets)
        if not tree.documents:
            raise Unrunnable(f"{datasets} holds no descriptor, so the lake check would read nothing")
        shallow = subprocess.run(["git", "-C", str(repo), "rev-parse", "--is-shallow-repository"], capture_output=True, text=True, check=False)
        if shallow.returncode != 0 or shallow.stdout.strip() != "false":
            raise Unrunnable(f"{repo} is not a full git checkout, so no input's change time can be read")

        findings: list[Finding] = []
        statuses: dict[str, Any] = {}
        with tempfile.TemporaryDirectory(prefix="lake-check-") as tmp:
            work = Path(tmp)
            steps = {
                "descriptors": lambda: check_descriptors(tree, repo, work, protocol_tables(repo)),
                "engine": lambda: check_engine(tree, work),
                "labels": lambda: check_labels(tree, work),
                "frictionless": lambda: check_frictionless(tree, work),
                "code_list": lambda: check_code_list(tree, work),
            }
            for name in wanted:
                if name in steps:
                    ran, found = steps[name]()
                    print(f"\n===== {name} (exit {ran.status})\n{ran.log}")
                    statuses[name] = ran.status
                elif name == "joins":
                    runs, found = check_joins(tree)
                    for ran in runs:
                        print(f"\n===== joins (exit {ran.status})\n{ran.log}")
                    statuses[name] = max((r.status for r in runs), default=0)
                elif name == "ratchet":
                    print("\n===== ratchet")
                    found = check_ratchet(tree, repo)
                    statuses[name] = 1 if found else 0
                else:
                    print("\n===== verify")
                    found = check_verify(tree)
                    statuses[name] = 1 if found else 0
                findings.extend(found)

        for finding in findings:
            sort_finding(repo, tree, finding)
    except Unrunnable as exc:
        print(f"lake check: {exc}", file=sys.stderr)
        return EXIT_ERROR

    drift = [f for f in findings if f.kind == DRIFT]
    awaiting = [f for f in findings if f.kind == AWAITING]
    text = summary(drift, awaiting, statuses)
    print("\n" + text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
            handle.write(text)
    if args.json:
        args.json.write_text(
            json.dumps(
                {"checks": statuses, DRIFT: [asdict(f) for f in drift], AWAITING: [asdict(f) for f in awaiting]},
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
    print(f"lake check: {len(drift)} drift, {len(awaiting)} awaiting publish")
    return EXIT_DRIFT if drift else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
