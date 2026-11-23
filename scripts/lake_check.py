#!/usr/bin/env python3
"""Hold what the open lake stores to itself and to `main`, and sort what disagrees.

The lake workflow runs this weekly and on demand, never on a push, over a tree that
`emit_descriptor.py --all --tree DIR --bypass-edge` laid out: `DIR/datasets/` is the
repository's `datasets/` with every published table's descriptor written in from the lake.
It runs the checks that read the lake over that tree:

  descriptors   check_descriptors.py --require-self-description, with the README catalogue;
                then again over the per-table packages (see below), without it
  engine        check_engine_version.py
  labels        check_label_agreement.py --strict
  frictionless  check_frictionless.py, over the package view
  code_list     check_code_list.py, over the package view
  joins         crosswalk_join.py, once per package that declares `x-joins`
  ratchet       each stored descriptor against the constraints its committed overrides
                hold at HEAD (check_constraint_ratchet.py's `ignored` rule; there is no
                predecessor descriptor in git to hold it to any more)
  verify        every declared `bytes` and `hash` against the object served, a full
                download of each (publish_dataset.py verify, over per-table descriptors too)

THE PACKAGE VIEW. The descriptor check's rules 1 to 7 and licence rule, Frictionless
and the code-list check read only `*/datapackage.json`, and a Protocol that publishes
several tables stores each as `<slug>/<table>.datapackage.json`. So every stored
descriptor is also laid out as `packages/<package>/datapackage.json` in the scratch
directory, a per-table one as package `<table>`, as `check_before_publish.py` lays out a
build. Frictionless and the code-list check read the whole view; the descriptor check
reads it with `--only` each per-table package and `--no-catalogue`, and leaves its
licence notice findings to the first run, which already read those descriptors. A
finding about a package is attributed to the descriptor it was laid out from. Two
descriptors that would be one package stop the run (exit 2). Label agreement reads
`datasets/` alone.

and sorts every finding into one of three kinds. Only drift fails the run.

  drift             the lake contradicts itself, whatever `main` did since: a served
                    object whose bytes or hash differ from its stored descriptor, a
                    stored descriptor that disagrees with its object's bytes or with the
                    copy stamped in its footer, a footer written by an engine other than
                    the one its Protocol pinned when the table was published, a code list
                    that breaks the contract, a declared join that fails. Also a check
                    that crashed or reported what this script cannot read, since then
                    nothing says the lake is sound. A reader can see each of these, so
                    the run fails and the workflow raises an alert.
  awaiting_publish  a finding that compares the lake with a repository input, when that
                    input changed in git after the table's `created`, or a table that is
                    not published yet: the expected gap between a merge and its publish.
                    Reported; the run stays green.
  main_disagrees    the same comparison with the input unchanged since the publish, such
                    as a README row count a republish moved past its rounding, and a
                    finding about the repository alone (`examples/`, an unreadable
                    sidecar). `main` is behind the lake, or wrong on its own; a publish
                    did not cause it to be wrong to a reader of the lake. Reported in the
                    weekly report and fixed in a batch; the run stays green.

The repository inputs, each finding to its own:

  catalogue rows             README.md
  licence notices            datasets/licence-notices.json
  label agreement            datasets/label-agreement.json
  Frictionless refusals      datasets/frictionless-rejections.json
  overrides ratchet          the table's overrides sidecar (`descriptor.overrides.json`
                             or `<name>.overrides.json`)
  engine, no run record      the Protocol's `arcform.yaml` (see below)

An input's time is `git log -1 --format=%cI -- <path>`, so the checkout needs full
history. Two inputs are dated more narrowly, because both change for reasons that have
nothing to do with the table a finding is about, and a whole-file time would let any
such edit turn a real mismatch into "awaiting publish":

  catalogue rows    the last commit that touched the row's own line in README.md
                    (`git log -L<n>,<n>:README.md`). A published table with no row is
                    dated by the last commit whose added or removed lines name its
                    object (`git log -G`), which is a row's removal.
  label agreement   the last commit that touched the reason or correction entry the
                    finding is about (`git log -L` over the entry's lines in HEAD's
                    `datasets/label-agreement.json`). An open difference is dated by
                    the entries for its grouping and property, and by any commit whose
                    added or removed lines name its key, which is a reason's removal.
                    A difference no commit has reasoned about is dated never: main disagrees.

Line dating reads HEAD's lines, so a checkout whose README.md or reasons file differs
from HEAD, or a range git cannot follow, is dated by the whole file instead. The engine pin a table is held to is the one its stamp steps resolved in the
Run that published it, read from the lake's run record (`manifest.json`'s `run.record`,
a relative reference resolved against the manifest's URL); a mismatch is drift. A table
whose record is missing or unreadable is held to its Protocol's `arcform.yaml` at HEAD
instead, and a mismatch is sorted by when that file changed; the step summary names
each such table in its notes.

    lake_check.py --tree DIR --repo . --json FILE [--manifest URL] [--check NAME ...]
                  [--alert FILE] [--report FILE]

`--alert` writes the drift as Markdown, the body of the alert issue the workflow opens
when the run fails; `--report` writes the other two kinds, the body of the weekly report
issue. Each is written whether or not it has anything in it.

Exit codes: 0 no drift (the other two kinds may be listed); 1 drift; 2 this script
could not run (no tree, no git history).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
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
MAIN = "main_disagrees"
Kind = Literal["awaiting_publish", "drift", "main_disagrees"]


class Unrunnable(Exception):
    """This script cannot run at all; exit 2."""


def classify(slug: str | None, published_at: datetime | None, last_change_at: datetime) -> Kind:
    """`awaiting_publish` when the table is not published, or its inputs changed after `published_at`.

    Otherwise `main_disagrees`: the input was as it is now when the table was published.
    """
    if slug is None or published_at is None:
        return AWAITING
    return AWAITING if last_change_at > published_at else MAIN


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


def first_time(repo: Path, args: list[str]) -> datetime | None:
    """The commit time `git log -1 <args>` names first, or None when git names none or fails."""
    done = subprocess.run(
        ["git", "-C", str(repo), "log", "-1", "-s", "--format=%x00%cI", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        return None
    for line in done.stdout.splitlines():
        if line.startswith("\x00"):
            return parse_time(line[1:])
    return NEVER


def dated(repo: Path, finding: "Finding") -> datetime:
    """When the repository input a finding compares the lake with last changed."""
    if not (finding.lines or finding.mentions):
        return last_change(repo, finding.inputs) if finding.inputs else NEVER
    times = [NEVER]
    for path, first, last in finding.lines:
        # The finding's line numbers are the working tree's; -L reads HEAD's.
        dirty = subprocess.run(["git", "-C", str(repo), "diff", "--quiet", "HEAD", "--", path], check=False).returncode != 0
        moment = None if dirty else first_time(repo, [f"-L{first},{last}:{path}"])
        times.append(moment if moment is not None else last_change(repo, [path]))
    for path, pattern in finding.mentions:
        moment = first_time(repo, [f"-G{pattern}", "--", path])
        if moment is None:
            raise Unrunnable(f"git log -G over {path} in {repo} failed")
        times.append(moment)
    return max(times)


def literal_regex(text: str) -> str:
    """`text` as the extended regular expression `git log -G` compiles, matching it literally."""
    return "".join("\\" + ch if ch in ".[]*^$\\+?(){}|" else ch for ch in text)


def entry_spans(text: str) -> dict[str, list[tuple[int, int, Any]]]:
    """Each top-level list's elements in a JSON document, with the 1-based lines each spans."""
    decoder = json.JSONDecoder()

    def skip(at: int) -> int:
        while at < len(text) and text[at] in " \t\r\n":
            at += 1
        return at

    def line(at: int) -> int:
        return text.count("\n", 0, at) + 1

    spans: dict[str, list[tuple[int, int, Any]]] = {}
    at = skip(0)
    if text[at:at + 1] != "{":
        return spans
    at = skip(at + 1)
    while at < len(text) and text[at] != "}":
        key, at = decoder.raw_decode(text, at)
        at = skip(skip(at) + 1)  # past the colon
        if text[at] == "[":
            items = []
            at = skip(at + 1)
            while text[at] != "]":
                value, end = decoder.raw_decode(text, at)
                items.append((line(at), line(end - 1), value))
                at = skip(end)
                if text[at] == ",":
                    at = skip(at + 1)
            spans[key] = items
            at += 1
        else:
            _, at = decoder.raw_decode(text, at)
        at = skip(at)
        if text[at:at + 1] == ",":
            at = skip(at + 1)
    return spans


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
    lake_only: bool = False  # the lake against itself: drift whatever git says
    crash: bool = False  # a check that failed without a finding, or one this script cannot read: drift
    # When either is set, the finding is dated by these instead of by `inputs` whole:
    lines: list[tuple[str, int, int]] = field(default_factory=list)  # (path, first, last) for `git log -L`
    mentions: list[tuple[str, str]] = field(default_factory=list)  # (path, extended regex) for `git log -G`
    kind: str = ""
    published_at: str | None = None
    last_change_at: str | None = None


def sort_finding(repo: Path, tree: "Tree", finding: Finding) -> Kind:
    """Classify `finding`, recording the times it was judged on.

    The lake against itself is drift, and so is a crash. A finding with no table is a
    disagreement about the repository alone, which no publish can close: `main`
    disagrees. A finding that compares the lake with repository inputs is sorted by
    `classify`, and one about several tables is awaiting a publish when any one of them is.
    """
    if finding.lake_only or finding.crash:
        finding.kind = DRIFT
        return DRIFT
    if not finding.subjects:
        finding.kind = MAIN
        return MAIN
    kinds = []
    for subject in finding.subjects:
        published = tree.created(subject.descriptor)
        changed = dated(repo, finding)
        kinds.append(classify(subject.slug, published, changed))
        finding.published_at = published.isoformat() if published else None
        finding.last_change_at = changed.isoformat() if changed is not NEVER else None
        if kinds[-1] == AWAITING:
            break
    finding.kind = AWAITING if AWAITING in kinds else MAIN
    return finding.kind


class Tree:
    """The emitted descriptors: where each is, when its table was published, which object it names."""

    def __init__(self, datasets: Path) -> None:
        self.datasets = datasets
        self.documents: dict[str, dict[str, Any]] = {}
        self.packages: Path | None = None  # set by `lay_out_packages`
        self.by_package: dict[str, str] = {}
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

    def per_table(self) -> list[str]:
        """The per-table descriptors (`<slug>/<name>.datapackage.json`), as package names."""
        return sorted(package_name(rel) for rel in self.documents if not rel.endswith("/" + PACKAGE_FILE))

    def lay_out_packages(self, work: Path) -> Path:
        """`work/packages/`: one `<package>/datapackage.json` per descriptor; return the directory.

        The descriptor, Frictionless and code-list checks read only `*/datapackage.json`,
        so a Protocol's per-table `<name>.datapackage.json` is laid out as package
        `<name>`, as `check_before_publish.py` lays out a build. Every descriptor is
        there, so a foreign key resolves against the whole lake and Frictionless holds the
        whole rejections file. A relative resource path is linked beside the copy, so it
        reads the same file; the lake's own paths are URLs.
        """
        packages = work / "packages"
        if packages.exists():
            shutil.rmtree(packages)
        self.by_package = {}
        for rel in self.documents:
            name = package_name(rel)
            if name in self.by_package:
                raise Unrunnable(f"{rel} is checked as package {name!r}, and {self.by_package[name]} is already that package")
            self.by_package[name] = rel
        for name, rel in self.by_package.items():
            source = self.datasets / rel
            folder = packages / name
            folder.mkdir(parents=True)
            shutil.copyfile(source, folder / PACKAGE_FILE)
            for resource in self.documents[rel].get("resources") or []:
                raw = resource.get("path") if isinstance(resource, dict) else None
                if not isinstance(raw, str) or not raw or "://" in raw or Path(raw).is_absolute():
                    continue
                target = source.parent / raw
                link = folder / raw
                if target.exists() and not link.exists():
                    link.parent.mkdir(parents=True, exist_ok=True)
                    link.symlink_to(target.resolve())
        self.packages = packages
        return packages

    def subject_for_package(self, name: str) -> Subject | None:
        """The subject a package in the laid-out view names."""
        rel = self.by_package.get(name)
        return Subject(rel.split("/", 1)[0], rel) if rel else None

    def subject_for_checked_path(self, path: str) -> Subject | None:
        """`subject_for_path`, for a path in the tree or in the laid-out package view."""
        if self.packages is not None:
            try:
                name = Path(path).resolve().relative_to(self.packages.resolve()).parts[0]
            except (ValueError, IndexError):
                name = None
            if name is not None:
                return self.subject_for_package(name)
        return self.subject_for_path(path)


PACKAGE_FILE = "datapackage.json"
PER_TABLE_SUFFIX = ".datapackage.json"


def package_name(rel: str) -> str:
    """The package a descriptor is checked as: its Protocol's slug, or a per-table file's own name."""
    slug, filename = rel.split("/", 1)
    return slug if filename == PACKAGE_FILE else filename.removesuffix(PER_TABLE_SUFFIX)


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
    return [*parsed, Finding(ran.check, f"exit {ran.status} with no finding this script can attribute:\n{tail}", crash=True)]


def each(check: str, items: Any, convert: Any) -> list[Finding]:
    """`convert` over a check's reported items; one this script cannot read is kept, as drift.

    A shape this adapter does not expect must not drop a finding, least of all beside
    others it could read, which would let the run pass on the ones it understood.
    """
    found: list[Finding] = []
    for item in items if isinstance(items, list) else [items]:
        try:
            result = convert(item)
        except (KeyError, TypeError, ValueError, AttributeError, IndexError) as exc:
            text = json.dumps(item, ensure_ascii=False, default=str)
            found.append(Finding(check, f"a reported item this script cannot read ({exc!r}): {text[:500]}", crash=True))
            continue
        if result is not None:
            found.append(result)
    return found


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

    def convert(item: dict[str, Any]) -> Finding:
        rule = item["rule"]
        detail = f"{rule}: {item['detail']}"
        if rule.startswith("catalogue."):
            # The object a row links to names its table; a descriptor path (`catalogue.missing`) is one.
            subject = tree.subject_for_object(item["resource"]) or tree.subject_for_path(item["resource"])
            if subject is None:
                # A row linking an object the lake does not hold: awaiting its first publish
                # when a Protocol here publishes that address, otherwise a wrong link.
                address = item["resource"].split("?", 1)[0].split("://", 1)[-1].split("/", 1)[-1]
                slug = links.get(address.removesuffix(".parquet"))
                subject = Subject(slug, None) if slug else None
            found = Finding("descriptors", f"{item['resource']}: {detail}", [subject] if subject else [], ["README.md"])
            row = re.match(r"line (\d+) ", str(item["detail"]))
            if row:
                found.lines = [("README.md", int(row.group(1)), int(row.group(1)))]
            elif subject is not None and subject.descriptor in tree.documents:
                resources = tree.documents[subject.descriptor].get("resources") or [{}]
                link = str(resources[0].get("path", "")).split("?", 1)[0].split("://", 1)[-1].split("/", 1)[-1]
                found.mentions = [("README.md", literal_regex(link or subject.descriptor))]
            return found
        subject = tree.subject_for_path(item["package"])
        where = f"{item['package']} :: {item['resource']}: {detail}"
        if subject is None:
            return Finding("descriptors", where)  # examples/, which no publish can close
        if rule.startswith("licence.notice"):
            return Finding("descriptors", where, [subject], ["datasets/licence-notices.json"])
        return Finding("descriptors", where, [subject], lake_only=True)

    found = crashed(ran, each("descriptors", [*report.get("violations", []), *report.get("pending", [])], convert))
    names = tree.per_table()
    if not names:
        return ran, found
    per_table_ran, per_table_found = check_per_table_descriptors(tree, work, names)
    log = f"{ran.log}\n\n===== descriptors, per-table (exit {per_table_ran.status})\n{per_table_ran.log}"
    return Ran("descriptors", max(ran.status, per_table_ran.status), log), [*found, *per_table_found]


def check_per_table_descriptors(tree: Tree, work: Path, names: list[str]) -> tuple[Ran, list[Finding]]:
    """Rules 1 to 7 and the licence rule on each per-table descriptor, read as a package of its own.

    The run above reads only `*/datapackage.json` for those rules, so this one reads the
    package view with `--only` each per-table package. The catalogue and the licence
    notice rule already read the per-table descriptors in the run above, so the
    catalogue is skipped here and a notice finding is left to that run.
    """
    packages = packages_view(tree, work)
    out = work / "descriptors_per_table.json"
    ran = run(
        "descriptors",
        [
            str(SCRIPTS / "check_descriptors.py"),
            "--datasets-dir", str(packages),
            *(arg for name in names for arg in ("--only", name)),
            "--require-self-description",
            "--no-catalogue",
            "--notice-table", str(tree.datasets / "licence-notices.json"),
            "--json", str(out),
        ],
    )
    report = read_json(out)

    def notice(item: Any) -> bool:
        return isinstance(item, dict) and str(item.get("rule", "")).startswith("licence.notice")

    def convert(item: dict[str, Any]) -> Finding:
        subject = tree.subject_for_checked_path(item["package"])
        where = f"{subject.descriptor if subject else item['package']} :: {item['resource']}: {item['rule']}: {item['detail']}"
        return Finding("descriptors", where, [subject] if subject else [], lake_only=True)

    reported = [*report.get("violations", []), *report.get("pending", [])]
    found = each("descriptors", [item for item in reported if not notice(item)], convert)
    if ran.status == 1 and reported and not found:
        # Every finding was a licence notice, which the first run reports: not a crash.
        return ran, []
    return ran, crashed(ran, found)


def packages_view(tree: Tree, work: Path) -> Path:
    """The laid-out package view (`Tree.lay_out_packages`), made once per run."""
    return tree.packages if tree.packages is not None else tree.lay_out_packages(work)


PUBLIC_MANIFEST = "https://openlake.meridian.online/manifest.json"


def read_source(where: str) -> Any:
    """JSON from a URL or a local path."""
    import urllib.request

    if where.startswith(("http://", "https://")):
        request = urllib.request.Request(where, headers={"User-Agent": "open-analytics-lake-check"})
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    return json.loads(Path(where).read_text(encoding="utf-8"))


PUBLIC_ORIGIN = "https://openlake.meridian.online/"


def record_url(reference: str, manifest_source: str) -> str:
    """A run record's address: absolute as written, else against the manifest's URL.

    A relative reference is never read as a local path. A manifest read from a file has
    no URL to resolve against, so the public lake's origin stands in, as it does for
    `publish_dataset.py trace`.
    """
    from urllib.parse import urljoin

    if "://" in reference:
        return reference
    base = manifest_source if manifest_source.startswith(("http://", "https://")) else PUBLIC_ORIGIN
    return urljoin(base, reference)


@dataclass
class Snapshot:
    """The stamp pins of the Run that published a table, or why they are not known."""

    pins: list[str] | None
    where: str  # the phrase a finding or a note uses for where the pin came from


def snapshot_pins(manifest: dict[str, Any], plain_url: str, manifest_source: str) -> Snapshot:
    """The `duckdb==` deps the stamp steps resolved in the Run that published this object.

    Read from the lake's own record of that Run (`manifest.json`'s `run.record`), so the
    pin is the one that wrote the object, not `main`'s. `pins` is None when the manifest
    has no entry or names no record for it, the record cannot be read, or it names no
    stamp step's `duckdb==` pin.
    """
    for entry in (manifest.get("datasets") or {}).values():
        if str(entry.get("url", "")).split("?", 1)[0] != plain_url:
            continue
        run_ref = entry.get("run")
        run_ref = run_ref if isinstance(run_ref, dict) else {}  # as publish_dataset.py's trace_entry reads it
        reference = run_ref.get("record")
        if not isinstance(reference, str) or not reference.strip():
            return Snapshot(None, "the manifest names no run record for it")
        url = record_url(reference.strip(), manifest_source)
        try:
            record = read_source(url)
            steps = record.get("steps") or []
            pins = []
            for step in steps:
                resolved = step.get("resolved_with") or {}
                if "stamp_descriptor" in str(resolved.get("script", "")):
                    pins.extend(dep for dep in resolved.get("deps") or [] if str(dep).startswith("duckdb=="))
        except (OSError, ValueError, AttributeError, TypeError) as exc:
            return Snapshot(None, f"its run record {url} could not be read: {exc}")
        if not pins:
            return Snapshot(None, f"its run record {url} names no stamp step's duckdb pin")
        return Snapshot(pins, f"in run {run_ref.get('id') or url}")
    return Snapshot(None, "the manifest has no entry for it")


def check_engine(tree: Tree, work: Path, manifest_source: str) -> tuple[Ran, list[Finding], list[str]]:
    """Each footer against the DuckDB the Run that published it pinned for its stamp steps.

    Each descriptor is laid in a directory of its own beside an `arcform.yaml` holding that
    Run's stamp pins, as the lake's run record states them. A mismatch against those is
    the lake contradicting itself, and drift. A table whose record is missing or cannot be
    read is held to its Protocol's `arcform.yaml` at HEAD instead; a mismatch then compares
    the lake with the repository and is sorted by when that file changed, so a pin bump on
    `main` waits for the next publish. Every such table is also returned as a note, for the
    step summary, so a missing record is visible rather than silently downgraded.
    """
    try:
        manifest = read_source(manifest_source)
    except (OSError, ValueError) as exc:
        return Ran("engine", EXIT_ERROR, f"cannot read the manifest {manifest_source}: {exc}"), [
            Finding("engine", f"cannot read the manifest {manifest_source}: {exc}", crash=True)
        ], []
    engine = work / "engine"
    laid: dict[str, tuple[Subject, Snapshot]] = {}
    notes: list[str] = []
    for index, (rel, document) in enumerate(sorted(tree.documents.items())):
        slug, name = rel.split("/", 1)
        folder = engine / f"{index:03d}-{slug}"
        folder.mkdir(parents=True)
        relaid = json.loads(json.dumps(document))
        for resource in relaid.get("resources") or []:
            path = resource.get("path")
            if isinstance(path, str) and "://" not in path:
                resource["path"] = str((tree.datasets / slug / path).resolve())  # a local path moves with it
        (folder / name).write_text(json.dumps(relaid), encoding="utf-8")
        plain = str((document.get("resources") or [{}])[0].get("path", "")).split("?", 1)[0]
        snapshot = snapshot_pins(manifest, plain, manifest_source)
        if snapshot.pins is not None:
            steps = "".join(
                f"  - name: stamp_{i}\n    with:\n      script: stamp_descriptor.py\n      deps:\n        - {pin}\n"
                for i, pin in enumerate(snapshot.pins)
            )
            (folder / "arcform.yaml").write_text("steps:\n" + steps, encoding="utf-8")
        else:
            head = tree.datasets / slug / "arcform.yaml"
            if head.exists():
                shutil.copyfile(head, folder / "arcform.yaml")
            notes.append(f"engine: {rel} is held to its Protocol's pin at HEAD, because {snapshot.where}")
        laid[str((folder / name).resolve())] = (Subject(slug, rel), snapshot)
    for note in notes:
        print(note)
    out = work / "engine.json"
    ran = run("engine", [str(SCRIPTS / "check_engine_version.py"), "--datasets-dir", str(engine), "--json", str(out)])

    def convert(item: dict[str, Any]) -> Finding:
        subject, snapshot = laid[str(Path(item["package"]).resolve())]
        written = f"{subject.descriptor} :: {item['resource']}: written by {item['writer_version']}, pinned {item['reader_pin']} ({item['pin_source']}"
        if snapshot.pins is not None:
            return Finding("engine", f"{written}, {snapshot.where})", [subject], lake_only=True)
        detail = f"{written} at HEAD, because {snapshot.where})"
        return Finding("engine", detail, [subject], [f"datasets/{subject.slug}/arcform.yaml"])

    return ran, crashed(ran, each("engine", read_json(out).get("mismatches", []), convert)), notes


def check_labels(tree: Tree, work: Path, repo: Path = ROOT) -> tuple[Ran, list[Finding]]:
    out = work / "labels.json"
    ran = run("labels", [str(SCRIPTS / "check_label_agreement.py"), "--strict", "--datasets-dir", str(tree.datasets), "--json", str(out)])
    report = read_json(out)

    def subjects(values: dict[str, Any]) -> list[Subject]:
        return [tree.package(slug) for slug in sorted({ident.split(".", 1)[0] for ident in values})]

    path = "datasets/label-agreement.json"
    inputs = [path]
    shown = subprocess.run(["git", "-C", str(repo), "show", f"HEAD:{path}"], capture_output=True, text=True, check=False)
    try:
        spans = entry_spans(shown.stdout) if shown.returncode == 0 else {}
    except (ValueError, IndexError):
        spans = {}

    def grouping(kind: str) -> str:
        return "concept" if kind == "concept" else "field"

    def reason_lines(item: dict[str, Any], *, values: bool) -> list[tuple[str, int, int]]:
        group = grouping(item["kind"])
        return [
            (path, first, last)
            for first, last, entry in spans.get("reasons", [])
            if isinstance(entry, dict)
            and entry.get(group) == item["key"]
            and entry.get("property") == item["property"]
            and (not values or entry.get("values") == item["values"])
        ]

    def open_difference(item: dict[str, Any]) -> Finding | None:
        if item.get("reason"):
            return None
        found = Finding("labels", f"{item['kind']} {item['key']!r}, {item['property']}: no stated reason", subjects(item["values"]), inputs)
        found.lines = reason_lines(item, values=False)
        found.mentions = [(path, literal_regex(f'"{grouping(item["kind"])}": "{item["key"]}"'))]
        return found

    def stale(item: dict[str, Any]) -> Finding:
        found = Finding("labels", f"{item['kind']} {item['key']!r}, {item['property']}: a stated reason matches no difference", subjects(item["values"]), inputs)
        found.lines = reason_lines(item, values=True)  # none found at HEAD: the whole file dates it
        return found

    def lapsed(item: dict[str, Any]) -> Finding | None:
        if item["state"] == "declared":
            return None
        found = Finding("labels", f"{item['package']} {item['pointer']}: correction {item['state']}", [tree.package(item["package"])], inputs)
        found.lines = [
            (path, first, last)
            for first, last, entry in spans.get("corrections", [])
            if isinstance(entry, dict) and entry.get("package") == item["package"] and entry.get("pointer") == item["pointer"]
        ]
        return found

    found = [
        *each("labels", report.get("differences", []), open_difference),
        *each("labels", report.get("stale_reasons", []), stale),
        *each("labels", report.get("corrections", []), lapsed),
    ]
    return ran, crashed(ran, found)


def check_frictionless(tree: Tree, work: Path) -> tuple[Ran, list[Finding]]:
    """Every descriptor, per-table ones included, through the reference implementation."""
    out = work / "frictionless.json"
    packages = packages_view(tree, work)
    ran = run(
        "frictionless",
        [
            str(SCRIPTS / "check_frictionless.py"),
            "--datasets-dir", str(packages),
            "--rejections", str(tree.datasets / "frictionless-rejections.json"),
            "--json", str(out),
        ],
    )

    def convert(fault: str) -> Finding:
        subject = tree.subject_for_package(fault.split(":", 1)[0])
        return Finding("frictionless", fault, [subject] if subject else [], ["datasets/frictionless-rejections.json"])

    return ran, crashed(ran, each("frictionless", read_json(out).get("faults", []), convert))


def check_code_list(tree: Tree, work: Path) -> tuple[Ran, list[Finding]]:
    """Every descriptor declaring `x-meridian-code-list`, per-table ones included, against the contract."""
    out = work / "code_list.json"
    packages = packages_view(tree, work)
    ran = run("code_list", [str(SCRIPTS / "check_code_list.py"), "--datasets-dir", str(packages), "--json", str(out)])
    files = read_json(out).get("files") or {}
    pairs = [{"package": name, "line": line} for name, lines in files.items() for line in lines]

    def convert(pair: dict[str, Any]) -> Finding:
        subject = tree.subject_for_package(pair["package"])
        where = subject.descriptor if subject else pair["package"]
        return Finding("code_list", f"{where}: {pair['line']}", [subject] if subject else [], lake_only=True)

    return ran, crashed(ran, each("code_list", pairs, convert))


def check_joins(tree: Tree) -> tuple[list[Ran], list[Finding]]:
    declaring = sorted(rel.split("/", 1)[0] for rel, doc in tree.documents.items() if doc.get("x-joins") and rel.endswith("/datapackage.json"))
    runs, found = [], []
    for slug in declaring:
        ran = run("joins", [str(JOINS), "--datasets-dir", str(tree.datasets), "--package", slug])
        runs.append(ran)
        if ran.status == 1:
            tail = "\n".join(ran.log.splitlines()[-5:])
            found.append(Finding("joins", f"{slug}: a declared join fails against the published bytes:\n{tail}", [tree.package(slug)], lake_only=True))
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
            found.append(Finding("ratchet", item.line(), [Subject(slug, rel)], [f"datasets/{slug}/{sidecar}"]))
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
                found.append(Finding("verify", f"{rel} :: {name}: {exc}", [subject], lake_only=True))
                continue
            if served != declared:
                found.append(
                    Finding("verify", f"{rel} :: {name}: declares {declared.describe()}; {url} serves {served.describe()}", [subject], lake_only=True)
                )
            else:
                print(f"verify: {rel} :: {name} — {served.describe()}")
    return found


# ------------------------------------------------------------------ the report


DRIFT_TITLE = "Drift ({n}): the lake contradicts itself, and the run fails"
MAIN_TITLE = "Main disagrees with the lake ({n}): fixed on `main` in a batch, not failed on"
AWAITING_TITLE = "Awaiting publish ({n}): merged and not yet published, not failed on"

# What fixes a README row count that `main_disagrees`: the counts rewritten from the lake.
CATALOGUE_FIX = (
    "A README row count is rewritten from the lake's own count, not by hand:\n\n"
    "```sh\n"
    "python scripts/emit_descriptor.py --all --tree /tmp/oa-tree --bypass-edge\n"
    "python scripts/check_descriptors.py --datasets-dir /tmp/oa-tree/datasets --readme README.md --write-catalogue\n"
    "```\n"
)


def section(title: str, items: list[Finding]) -> list[str]:
    lines = ["", f"#### {title.format(n=len(items))}", ""]
    if not items:
        lines.append("None.")
    for item in items:
        when = f" (published {item.published_at}, inputs last changed {item.last_change_at})" if item.inputs and item.subjects else ""
        lines.append(f"- **{item.check}**{when}")
        lines.append("")
        lines.append("  ```")
        lines.extend(f"  {line}" for line in item.detail.splitlines())
        lines.append("  ```")
    return lines


def is_catalogue(finding: Finding) -> bool:
    return finding.check == "descriptors" and "README.md" in finding.inputs


def summary(
    drift: list[Finding],
    awaiting: list[Finding],
    statuses: dict[str, Any],
    notes: list[str] | None = None,
    main_disagrees: list[Finding] | None = None,
) -> str:
    lines = ["### Lake check", ""]
    lines.append("| check | exit |")
    lines.append("|---|---|")
    lines.extend(f"| {name} | {status} |" for name, status in statuses.items())
    if notes:
        lines += ["", f"#### Notes ({len(notes)}) — read these, they are not findings", ""]
        lines.extend(f"- {note}" for note in notes)
    lines += section(DRIFT_TITLE, drift)
    lines += section(MAIN_TITLE, main_disagrees or [])
    lines += section(AWAITING_TITLE, awaiting)
    return "\n".join(lines) + "\n"


# GitHub refuses an issue body over 65,536 characters; the workflow adds the run's link.
ISSUE_LIMIT = 60_000


def capped(text: str) -> str:
    """`text`, cut to fit an issue body, saying so: the full list is in the run's step summary."""
    if len(text) <= ISSUE_LIMIT:
        return text
    cut = text[:ISSUE_LIMIT].rsplit("\n", 1)[0]
    if cut.count("```") % 2:
        cut += "\n  ```"
    return cut + "\n\n(Cut to fit an issue. Every finding is in the run's step summary and its `lake-check` artifact.)\n"


def alert_text(drift: list[Finding]) -> str:
    """The alert issue's body: what a reader of the lake can see is wrong."""
    lines = [
        "The weekly lake check found the open lake contradicting itself. A reader of the lake can see each "
        "finding below: a served file that is not the one its descriptor declares, a descriptor that disagrees "
        "with its own bytes or footer, a footer from the wrong engine, a broken code list or join, or a check "
        "that could not run. Fix the lake, usually by republishing the table, then run `gh workflow run lake.yml`; "
        "a green run closes this issue.",
    ]
    lines += section(DRIFT_TITLE, drift)
    return capped("\n".join(lines) + "\n")


def report_text(main_disagrees: list[Finding], awaiting: list[Finding]) -> str:
    """The weekly report issue's body: where `main` and the lake differ, for a batch fix."""
    lines = [
        "Where `main` and the open lake disagree, as of the latest weekly lake check. None of these fails a run "
        "or blocks a merge: each is fixed on `main` in a batch, or closed by the publish it waits for. "
        "This issue is rewritten by each run and closed by a run that finds none.",
    ]
    if any(is_catalogue(f) for f in main_disagrees):
        lines += ["", CATALOGUE_FIX.rstrip("\n")]
    lines += section(MAIN_TITLE, main_disagrees)
    lines += section(AWAITING_TITLE, awaiting)
    return capped("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tree", type=Path, required=True, help="the directory emit_descriptor.py --tree wrote")
    ap.add_argument("--repo", type=Path, default=ROOT, help="the checkout: README.md, examples/, overrides and git history")
    ap.add_argument("--json", type=Path, help="write every finding, sorted, as JSON")
    ap.add_argument("--manifest", default=PUBLIC_MANIFEST, help="the lake's manifest.json, a URL or a path, for each table's run record")
    ap.add_argument("--check", action="append", choices=CHECKS, help="run only this check (repeatable; default every one)")
    ap.add_argument("--alert", type=Path, help="write the drift as Markdown, the alert issue's body")
    ap.add_argument("--report", type=Path, help="write the other findings as Markdown, the weekly report issue's body")
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
        notes: list[str] = []

        def engine_with_notes(result: tuple[Ran, list[Finding], list[str]]) -> tuple[Ran, list[Finding]]:
            notes.extend(result[2])
            return result[0], result[1]

        with tempfile.TemporaryDirectory(prefix="lake-check-") as tmp:
            work = Path(tmp)
            steps = {
                "descriptors": lambda: check_descriptors(tree, repo, work, protocol_tables(repo)),
                "engine": lambda: engine_with_notes(check_engine(tree, work, args.manifest)),
                "labels": lambda: check_labels(tree, work, repo),
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
    behind = [f for f in findings if f.kind == MAIN]
    text = summary(drift, awaiting, statuses, notes, behind)
    print("\n" + text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
            handle.write(text)
    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "checks": statuses,
                    DRIFT: [asdict(f) for f in drift],
                    MAIN: [asdict(f) for f in behind],
                    AWAITING: [asdict(f) for f in awaiting],
                    "notes": notes,
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
    if args.alert:
        args.alert.write_text(alert_text(drift), encoding="utf-8")
    if args.report:
        args.report.write_text(report_text(behind, awaiting), encoding="utf-8")
    print(f"lake check: {len(drift)} drift, {len(behind)} main disagrees, {len(awaiting)} awaiting publish")
    return EXIT_DRIFT if drift else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
