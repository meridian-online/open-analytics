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

  drift             the lake contradicts itself, whatever `main` did since: a served
                    object whose bytes or hash differ from its stored descriptor, a
                    stored descriptor that disagrees with its object's bytes or with the
                    copy stamped in its footer, a footer written by an engine other than
                    the one its Protocol pinned when the table was published, a code list
                    that breaks the contract, a declared join that fails. Also a check
                    that crashed, and a finding about `examples/`. The run fails.
  awaiting_publish  a finding that compares the lake with a repository input, when that
                    input changed in git after the table's `created`, or a table that is
                    not published yet. Reported; the run stays green. This is the
                    expected gap between a merge and its publish. The same finding with
                    the input unchanged since the publish is drift.

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
                    A difference no commit has reasoned about is dated never: drift.

A line range git cannot follow, as in a checkout whose README.md differs from HEAD, is
dated by the whole file instead. The engine pin a table is held to is the one its stamp steps resolved in the
Run that published it, read from the lake's run record (`manifest.json`'s `run.record`,
a relative reference resolved against the manifest's URL); a mismatch is drift. A table
whose record is missing or unreadable is held to its Protocol's `arcform.yaml` at HEAD
instead, and a mismatch is sorted by when that file changed; the step summary names
each such table in its notes.

    lake_check.py --tree DIR --repo . --json FILE [--manifest URL] [--check NAME ...]

Exit codes: 0 no drift (awaiting-publish findings may be listed); 1 drift; 2 this
script could not run (no tree, no git history).
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
        moment = first_time(repo, [f"-L{first},{last}:{path}"])
        times.append(moment if moment is not None else last_change(repo, [path]))
    for path, pattern in finding.mentions:
        moment = first_time(repo, [f"-G{pattern}", "--", path])
        if moment is None:
            raise Unrunnable(f"git log -G over {path} in {repo} failed")
        times.append(moment)
    return max(times)


def basic_regex(text: str) -> str:
    """`text` as a POSIX basic regular expression matching it literally."""
    return "".join("\\" + ch if ch in ".[]*^$\\" else ch for ch in text)


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
    # When either is set, the finding is dated by these instead of by `inputs` whole:
    lines: list[tuple[str, int, int]] = field(default_factory=list)  # (path, first, last) for `git log -L`
    mentions: list[tuple[str, str]] = field(default_factory=list)  # (path, basic regex) for `git log -G`
    kind: str = ""
    published_at: str | None = None
    last_change_at: str | None = None


def sort_finding(repo: Path, tree: "Tree", finding: Finding) -> Kind:
    """Classify `finding`, recording the times it was judged on.

    The lake against itself is drift. So is a finding with no table: a crash, or a
    disagreement about the repository alone, which no publish can close. A finding that
    compares the lake with repository inputs is sorted by `classify`, and one about
    several tables is awaiting a publish when any one of them is.
    """
    if finding.lake_only or not finding.subjects:
        finding.kind = DRIFT
        return DRIFT
    kinds = []
    for subject in finding.subjects:
        published = tree.created(subject.descriptor)
        changed = dated(repo, finding)
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
            found.append(Finding(check, f"a reported item this script cannot read ({exc!r}): {text[:500]}"))
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
                found.mentions = [("README.md", basic_regex(link or subject.descriptor))]
            return found
        subject = tree.subject_for_path(item["package"])
        where = f"{item['package']} :: {item['resource']}: {detail}"
        if subject is None:
            return Finding("descriptors", where)  # examples/, which no publish can close
        if rule.startswith("licence.notice"):
            return Finding("descriptors", where, [subject], ["datasets/licence-notices.json"])
        return Finding("descriptors", where, [subject], lake_only=True)

    found = each("descriptors", [*report.get("violations", []), *report.get("pending", [])], convert)
    return ran, crashed(ran, found)


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
            Finding("engine", f"cannot read the manifest {manifest_source}: {exc}")
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
        found.mentions = [(path, basic_regex(f'"{grouping(item["kind"])}": "{item["key"]}"'))]
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
    out = work / "frictionless.json"
    ran = run("frictionless", [str(SCRIPTS / "check_frictionless.py"), "--datasets-dir", str(tree.datasets), "--json", str(out)])

    def convert(fault: str) -> Finding:
        slug = fault.split(":", 1)[0]
        subject = tree.package(slug) if (tree.datasets / slug).is_dir() else None
        return Finding("frictionless", fault, [subject] if subject else [], ["datasets/frictionless-rejections.json"])

    return ran, crashed(ran, each("frictionless", read_json(out).get("faults", []), convert))


def check_code_list(tree: Tree, work: Path) -> tuple[Ran, list[Finding]]:
    out = work / "code_list.json"
    ran = run("code_list", [str(SCRIPTS / "check_code_list.py"), "--datasets-dir", str(tree.datasets), "--json", str(out)])
    files = read_json(out).get("files") or {}
    pairs = [{"slug": slug, "line": line} for slug, lines in files.items() for line in lines]

    def convert(pair: dict[str, Any]) -> Finding:
        return Finding("code_list", f"{pair['slug']}: {pair['line']}", [tree.package(pair["slug"])], lake_only=True)

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


def summary(drift: list[Finding], awaiting: list[Finding], statuses: dict[str, Any], notes: list[str] | None = None) -> str:
    lines = ["### Lake check", ""]
    lines.append("| check | exit |")
    lines.append("|---|---|")
    lines.extend(f"| {name} | {status} |" for name, status in statuses.items())
    if notes:
        lines += ["", f"#### Notes ({len(notes)}) — read these, they are not findings", ""]
        lines.extend(f"- {note}" for note in notes)
    for title, items in ((f"Drift ({len(drift)}) — the run fails", drift), (f"Awaiting publish ({len(awaiting)}) — reported, not failed on", awaiting)):
        lines += ["", f"#### {title}", ""]
        if not items:
            lines.append("None.")
        for item in items:
            when = f" (published {item.published_at}, inputs last changed {item.last_change_at})" if item.inputs and item.subjects else ""
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
    ap.add_argument("--manifest", default=PUBLIC_MANIFEST, help="the lake's manifest.json, a URL or a path, for each table's run record")
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
    text = summary(drift, awaiting, statuses, notes)
    print("\n" + text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
            handle.write(text)
    if args.json:
        args.json.write_text(
            json.dumps(
                {"checks": statuses, DRIFT: [asdict(f) for f in drift], AWAITING: [asdict(f) for f in awaiting], "notes": notes},
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
