#!/usr/bin/env python3
"""Upload a dataset's Parquet and declare its size and hash in the same act.

`datasets/<slug>/datapackage.json` tells a consumer how many bytes to expect at
`resources[].path` and what SHA-256 they should hash to. Those two figures were
written by one act — describing a local build — and the object was moved by a
different one — uploading it. Nothing tied them together, so a republish moved
the served object out from under a descriptor that still described the previous
one. Both `bytes` and `hash` on `edgar` were stale that way.

This is the seam that would make them one act. `publish` runs the uploader, reads
the object back **from the URL the descriptor itself declares**, and writes
`bytes` and `hash` from that read. It cannot upload to one place and declare
another, because it never takes the URL from the caller.

The same act has a second half. `bytes` and `hash` are not the only things a
descriptor says about an object: the field list, the types and the constraints
were all derived from a built Parquet by the Protocol's `describe` step. A Run
that writes the Parquet and then stops before `describe` leaves a rebuilt
artefact on disk beside a descriptor measured against the bytes it replaced, and
nothing about the file says which. On 2026-08-12 that happened for real — a Run
rebuilt `edgar_gleif` to 206,993 rows, failed at the step between the export and
the describe, and left the descriptor still describing the published 207,099.

So `publish` will not upload bytes it cannot trace. Before the uploader runs it
reads the B4 Run records `arc run` leaves under the Protocol's
`build/.arcform/runs/`, and requires one Run to have produced BOTH the bytes at
`--file` and the descriptor being published, and to have finished. `provenance`
is that same check on its own, for a publish pipeline that does its own uploading.

Neither of those two paths is where the caller says they are, and assuming they
were made every publish in the pipeline exit 1 for a day. The pipeline hands
`--file` a WORK COPY of the artefact — it stages the Protocol's terminal Parquet
in a scratch directory and uploads from there — so a Run's asset is matched by
CONTENT DIGEST, never by path. And it hands `--datasets-dir` a scratch copy of
the descriptor tree, deliberately, so a stamp written here cannot reach the file
checked into this repository. `descriptor_path.parent` is therefore a temp
directory with no Protocol in it, and the Run records have to be found through
`resolve_protocol_dir` instead. See that function for how a relocated descriptor
is tied back to the Protocol it came from, and for the case where there is no
Protocol to consult.

The Run's asset graph alone cannot answer this. It lists `datapackage.json` with
`produced_by: describe` and a content hash whether or not `describe` ran — in the
failed Run above the hash recorded there is the STALE descriptor's, because the
graph is a plan of what each step owns rather than a log of what happened. Only
`steps[].status` says what ran, and arcform stamps an unreached step `skipped`
with a null `skip_reason` (a step that was skipped because its output was already
current carries a reason). That distinction is the whole guard.

The publishing pipeline that moves these objects lives outside this repository
and calls `publish` for every dataset a descriptor here describes. Of the
checked-in descriptors, only `edgar`'s `bytes` and `hash` were written by
`restamp`; the rest were emitted when their descriptors were last regenerated.

  publish     upload a local file, then declare what the endpoint serves back
  provenance  refuse bytes and a descriptor that no one finished Run produced
  restamp     declare what the endpoint already serves, for an object published
              out of band before this seam existed
  verify      refuse when the descriptor and the endpoint disagree

`bytes` and `hash` are measured from a single pass over one stream, so they
cannot describe different bytes from each other. A short read is a refusal
rather than a smaller number: `HTTPResponse.read()` returns what arrived and
closes when a body is truncated, so a declared size taken from an interrupted
download would look entirely plausible. Completeness is decided against the
announced `Content-Length`, so a response that announces none is refused too —
there is no length to check a truncation against.

The uploader is injected, never built in — this repository holds no credentials
and names no bucket. Pass a command carrying `{file}` and `{url}`:

    publish_dataset.py publish --dataset edgar --file build/edgar.parquet \
        --uploader 'my-object-put --local {file} --to {url}'

Exit codes are distinct on purpose, because a status alone cannot tell a refusal
apart from a crash:

  0  the descriptor declares what the endpoint serves
  1  a disagreement
  2  the operation could not be completed — unreachable endpoint, failed
     uploader, malformed descriptor. Never a verdict about the data.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Iterable

EXIT_OK = 0
EXIT_DISAGREEMENT = 1
EXIT_ERROR = 2

CHUNK = 1 << 20
USER_AGENT = "meridian-open-analytics publish (+https://github.com/meridian-online/open-analytics)"

# A readback straight after an upload can reach an edge that has not caught up.
# That is a wait, not a disagreement, so the refusal is only reported once the
# endpoint has had these attempts to agree.
READBACK_ATTEMPTS = 3
READBACK_DELAY = 2.0

# Where `arc run` leaves its Run records, relative to a dataset's Protocol
# directory, and the contract version this seam knows how to read. A record
# announcing any other version stops the publish rather than being guessed at.
RUNS_SUBPATH = ("build", ".arcform", "runs")
RUN_CONTRACT_VERSION = "b4/1"

# arcform's own state directory, and the thing that says `arc run` runs this
# Protocol HERE. Deliberately the parent of RUNS_SUBPATH rather than the records
# themselves: keying the check on the records would let deleting them delete the
# check, and an empty runs directory has to reach the refusal below.
ARCFORM_STATE_SUBPATH = ("build", ".arcform")

# What makes a directory a Protocol directory rather than somewhere a descriptor
# happens to sit, and where this repository keeps its own: `datasets/<slug>/`,
# resolved from this file so it holds however the caller set --datasets-dir.
PROTOCOL_MARKER = "arcform.yaml"
REPO_DATASETS = Path(__file__).resolve().parent.parent / "datasets"


class PublishError(Exception):
    """The operation could not be completed. Never a verdict about the data."""


class Refusal(Exception):
    """The descriptor and the object disagree, and the descriptor was not written."""


@dataclass(frozen=True)
class Measurement:
    """A size and a digest taken from the same pass over the same bytes."""

    size: int
    digest: str  # "sha256:<hex>"

    def describe(self) -> str:
        return f"{self.size:,} bytes, {self.digest}"


def measure_stream(stream: BinaryIO) -> Measurement:
    digest = hashlib.sha256()
    size = 0
    while True:
        chunk = stream.read(CHUNK)
        if not chunk:
            break
        digest.update(chunk)
        size += len(chunk)
    return Measurement(size, "sha256:" + digest.hexdigest())


def measure_file(path: Path) -> Measurement:
    try:
        with path.open("rb") as handle:
            return measure_stream(handle)
    except OSError as exc:
        raise PublishError(f"cannot read {path}: {exc}") from exc


def measure_url(url: str, *, timeout: float = 600.0) -> Measurement:
    """Size and SHA-256 of what the endpoint serves, from one streamed GET.

    A body that stops short of its announced `Content-Length` raises rather than
    returning the short measurement — see the module docstring.
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            announced = response.headers.get("Content-Length")
            measured = measure_stream(response)
    except http.client.HTTPException as exc:  # IncompleteRead and its siblings
        raise PublishError(f"{url} delivered an incomplete response: {exc!r}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise PublishError(f"GET {url} failed: {exc}") from exc

    if announced is None or not announced.strip().isdigit():
        raise PublishError(
            f"{url} announced no usable Content-Length ({announced!r}); a complete response "
            "cannot be told from a truncated one, so there is nothing safe to declare"
        )
    expected = int(announced.strip())
    if expected != measured.size:
        raise PublishError(
            f"{url} delivered an incomplete response: {measured.size:,} bytes "
            f"of an announced {expected:,}"
        )
    return measured


def load_descriptor(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise PublishError(f"{path}: cannot read descriptor: {exc}") from exc
    if not isinstance(document, dict):
        raise PublishError(f"{path}: descriptor is not a JSON object")
    return document


def save_descriptor(path: Path, document: dict[str, Any]) -> None:
    """Rewrite the descriptor in place, whole or not at all.

    2-space indent, unescaped non-ASCII and a trailing newline reproduce what the
    describe step emits, so a stamp shows as two changed lines rather than a
    reformat of the file.
    """
    body = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(path.parent), prefix=path.name + ".", suffix=".tmp", delete=False
    )
    try:
        with handle:
            handle.write(body)
        os.replace(handle.name, path)
    except OSError as exc:
        Path(handle.name).unlink(missing_ok=True)
        raise PublishError(f"{path}: cannot write descriptor: {exc}") from exc


def pick_resource(document: dict[str, Any], descriptor_path: Path, name: str | None) -> dict[str, Any]:
    resources = document.get("resources") or []
    if not resources:
        raise PublishError(f"{descriptor_path}: declares no resources")
    if name is None:
        if len(resources) > 1:
            names = ", ".join(sorted(str(r.get("name")) for r in resources))
            raise PublishError(f"{descriptor_path}: declares {len(resources)} resources ({names}); name one with --resource")
        return resources[0]
    for resource in resources:
        if resource.get("name") == name:
            return resource
    raise PublishError(f"{descriptor_path}: declares no resource named {name!r}")


def resource_url(resource: dict[str, Any], descriptor_path: Path) -> str:
    path = resource.get("path")
    if not path:
        raise PublishError(f"{descriptor_path}: resource {resource.get('name')!r} declares no path")
    if not str(path).lower().startswith(("http://", "https://")):
        raise PublishError(
            f"{descriptor_path}: resource {resource.get('name')!r} points at {path!r}, "
            "which is not an endpoint this seam can read back"
        )
    return str(path)


def declared_measurement(resource: dict[str, Any]) -> Measurement | None:
    size, digest = resource.get("bytes"), resource.get("hash")
    if size is None or digest is None:
        return None
    return Measurement(int(size), str(digest))


def stamp(resource: dict[str, Any], measured: Measurement) -> None:
    resource["bytes"] = measured.size
    resource["hash"] = measured.digest


def resolve_protocol_dir(slug: str, descriptor_path: Path, explicit: Path | None = None) -> Path | None:
    """The Protocol directory whose Runs govern this descriptor, or None.

    Three cases, in this order.

    `--protocol-dir` was given: use it, and fail loudly if it is not a Protocol.
    Nothing guesses when the caller has said.

    The descriptor sits in its own Protocol — `arcform.yaml` beside it. That is
    the checked-out tree, and the case `arc` and a human both see.

    The descriptor is a RELOCATED COPY of one of this repository's. The publishing
    pipeline copies `datasets/<slug>/datapackage.json` into a scratch tree before
    handing it over, so that a stamp written here cannot reach the checked-out
    file; the copy has no Protocol beside it and never will. A copy is tied back
    to its Protocol by being byte-identical to the descriptor committed at
    `datasets/<slug>/` — the same equality the pipeline itself checks afterwards,
    and the reason a stamp that changes nothing is a no-op. Bytes rather than
    slug, because a different tree may legitimately use the same slug for a
    different package; that is what the offline harness for the pipeline does,
    and its fabricated `edgar` is not this repository's `edgar`.

    None means no Protocol governs these bytes. `publish` then has no Run to
    require; `provenance` treats it as a question it cannot answer.
    """
    if explicit is not None:
        if not (explicit / PROTOCOL_MARKER).is_file():
            raise PublishError(f"--protocol-dir {explicit} holds no {PROTOCOL_MARKER}")
        return explicit

    here = descriptor_path.parent
    if (here / PROTOCOL_MARKER).is_file():
        return here

    committed = REPO_DATASETS / slug / descriptor_path.name
    if not committed.is_file() or not (REPO_DATASETS / slug / PROTOCOL_MARKER).is_file():
        return None
    try:
        if committed.read_bytes() != descriptor_path.read_bytes():
            return None
    except OSError as exc:
        raise PublishError(f"cannot read {committed}: {exc}") from exc
    return REPO_DATASETS / slug


def descriptor_for(datasets_dir: Path, slug: str) -> Path:
    path = datasets_dir / slug / "datapackage.json"
    if not path.is_file():
        raise PublishError(f"no descriptor at {path}")
    return path


# ------------------------------------------------------- which Run made these


@dataclass(frozen=True)
class Run:
    """One `arc run` record: what it produced, and which of its steps got there."""

    path: Path
    run_id: str
    outcome: str
    started_at: str
    assets: tuple[dict[str, Any], ...]
    steps: dict[str, dict[str, Any]]

    def file_asset(self, protocol_dir: Path, target: Path) -> dict[str, Any] | None:
        """The recorded file asset whose path is `target`, or None.

        Asset paths are relative to the Protocol directory, and some are globs
        that resolve to nothing on disk; both are handled by comparing resolved
        paths rather than strings.

        Used for the DESCRIPTOR, whose place in the Protocol is fixed. The
        artefact is matched by `produced_asset` instead — see there.
        """
        for asset in self.assets:
            if asset.get("kind") != "file" or not asset.get("path"):
                continue
            if (protocol_dir / str(asset["path"])).resolve() == target:
                return asset
        return None

    def produced_asset(self, digest: str) -> dict[str, Any] | None:
        """The recorded file asset this Run PRODUCED holding exactly these bytes.

        By content, not by path: the publishing pipeline stages the Protocol's
        terminal Parquet into a scratch working directory and uploads the copy,
        so the path it passes is one no Run has ever seen. Matching on it refused
        byte-identical bytes with "built or replaced outside a Run", which was not
        true of them. A digest is also the stronger claim — a path says where a
        file was, the digest says what it holds.

        `produced_by` must name a step this Run got to. Without that, an asset a
        Run only READ would license a publish (a glob input carries `produced_by:
        null`, and an unreached step's outputs are still listed by the graph),
        and the whole point is that some step wrote these bytes.
        """
        for asset in self.assets:
            if asset.get("kind") != "file" or recorded_digest(asset) != digest:
                continue
            producer = asset.get("produced_by")
            if producer and step_reached(self.steps.get(str(producer))):
                return asset
        return None

    def failed_steps(self) -> list[str]:
        return sorted(
            name for name, step in self.steps.items() if (step.get("status") or {}).get("state") == "failed"
        )


def recorded_digest(asset: dict[str, Any]) -> str:
    """The asset's content hash in this module's `sha256:<hex>` form."""
    raw = str(asset.get("content_hash") or "")
    if not raw or raw.startswith("sha256:"):
        return raw
    return "sha256:" + raw


def step_reached(step: dict[str, Any] | None) -> bool:
    """Whether arcform recorded this step as having got there.

    `success` ran. `skipped` WITH a `skip_reason` also got there — arcform skips a
    step whose output is already current (`hash_clean`, the precondition kinds) and
    says which. `skipped` with no reason is the placeholder arcform stamps on a step
    the Run never reached, and it is the state this whole check exists to catch: the
    asset graph still names that step as the producer of a file it did not write.
    """
    if step is None:
        return False
    status = step.get("status") or {}
    state = status.get("state")
    if state == "success":
        return True
    return state == "skipped" and status.get("skip_reason") is not None


def step_phrase(step: dict[str, Any] | None) -> str:
    if step is None:
        return "not recorded at all"
    status = step.get("status") or {}
    state = str(status.get("state"))
    reason = status.get("skip_reason")
    if state == "skipped" and reason is None:
        return "skipped with no reason, which is how a Run records a step it never reached"
    if state == "skipped":
        return f"skipped ({reason})"
    return state


def load_runs(runs_dir: Path) -> list[Run]:
    """Every Run record under `runs_dir`, newest first.

    An unreadable or unknown-shaped record is an error, never an absence: a
    publish must not proceed because the thing that would have refused it could
    not be parsed.
    """
    if not runs_dir.is_dir():
        return []
    runs: list[Run] = []
    for path in sorted(runs_dir.glob("*.json")):
        try:
            with path.open(encoding="utf-8") as handle:
                document = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise PublishError(f"{path}: cannot read the Run record: {exc}") from exc
        if not isinstance(document, dict):
            raise PublishError(f"{path}: Run record is not a JSON object")
        version = document.get("contract_version")
        if version != RUN_CONTRACT_VERSION:
            raise PublishError(
                f"{path}: Run record declares contract version {version!r}; this seam reads "
                f"{RUN_CONTRACT_VERSION!r} and will not guess at another"
            )
        run = document.get("run")
        if not isinstance(run, dict):
            raise PublishError(f"{path}: Run record declares no run block")
        steps = document.get("steps")
        assets = document.get("assets")
        if not isinstance(steps, list) or not isinstance(assets, list):
            raise PublishError(f"{path}: Run record declares no steps/assets")
        runs.append(
            Run(
                path=path,
                run_id=str(run.get("run_id") or path.stem),
                outcome=str(run.get("outcome")),
                started_at=str(run.get("started_at") or ""),
                assets=tuple(asset for asset in assets if isinstance(asset, dict)),
                steps={str(step.get("name")): step for step in steps if isinstance(step, dict)},
            )
        )
    runs.sort(key=lambda run: (run.started_at, run.path.name), reverse=True)
    return runs


def unpublishable_reason(run: Run, protocol_dir: Path, descriptor_path: Path, declared: Measurement) -> str | None:
    """Why this Run does not license publishing that descriptor, or None if it does.

    Ordered specific-to-general on purpose: a Run that stopped before `describe`
    also has a non-success outcome, and naming the step is what tells an operator
    what went wrong.
    """
    name = descriptor_path.name
    # Looked up at its place IN THE PROTOCOL, not at `descriptor_path`, which may be
    # a relocated copy in a directory no Run has ever written to.
    asset = run.file_asset(protocol_dir, (protocol_dir / name).resolve())
    if asset is None:
        return f"records no {name}, so nothing in it ties those bytes to a descriptor"

    producer = asset.get("produced_by")
    step = run.steps.get(str(producer)) if producer else None
    if not step_reached(step):
        return (
            f"never got to {str(producer)!r}, the step that writes {name} — it is {step_phrase(step)}. "
            f"The {name} on disk was written by an earlier Run, against other bytes"
        )

    recorded = recorded_digest(asset)
    if recorded != declared.digest:
        return (
            f"wrote a {name} of {recorded or 'no recorded hash'}, but the file on disk is "
            f"{declared.describe()} — it was edited or regenerated after the Run"
        )

    if run.outcome != "success":
        failed = ", ".join(run.failed_steps())
        return (
            f"finished {run.outcome!r}, not 'success'"
            + (f" — {failed} failed" if failed else "")
            + "; a Run that did not finish has not asserted what the Protocol asserts"
        )
    return None


def require_run_provenance(
    protocol_dir: Path, descriptor_path: Path, artefact: Path, measured: Measurement
) -> Run:
    """The finished Run that produced both `artefact` and the descriptor, or a Refusal."""
    runs_dir = protocol_dir.joinpath(*RUNS_SUBPATH)
    runs = load_runs(runs_dir)
    if not runs:
        raise Refusal(
            f"no Run record under {runs_dir}, so nothing says which Run produced {artefact}; "
            f"a descriptor is only true of the bytes the same Run described"
        )

    claimants = [run for run in runs if run.produced_asset(measured.digest) is not None]
    if not claimants:
        raise Refusal(
            f"no Run under {runs_dir} produced the bytes at {artefact} ({measured.describe()}); "
            f"{len(runs)} record(s) there produced other bytes. Those bytes were built or replaced "
            f"outside a Run"
        )

    declared = measure_file(descriptor_path)
    reasons: list[str] = []
    for run in claimants:
        reason = unpublishable_reason(run, protocol_dir, descriptor_path, declared)
        if reason is None:
            return run
        reasons.append(f"Run {run.run_id} {reason}")
    raise Refusal(
        f"{descriptor_path} was not regenerated by the Run that produced {artefact}:\n    "
        + "\n    ".join(reasons)
    )


def run_uploader(command: str, file: Path, url: str) -> None:
    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        raise PublishError(f"--uploader is not a parsable command line ({exc}): {command!r}") from exc
    if not tokens:
        raise PublishError("--uploader is empty")
    if not any("{file}" in token for token in tokens) or not any("{url}" in token for token in tokens):
        raise PublishError("--uploader must carry both {file} and {url}")
    argv = [token.replace("{file}", str(file)).replace("{url}", url) for token in tokens]

    print(f"publish: uploading {file} -> {url}")
    result = subprocess.run(argv, capture_output=True, text=True, check=False)
    if result.stdout.strip():
        print(result.stdout.rstrip())
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        raise PublishError(f"uploader exited {result.returncode}; the descriptor is unchanged")


def readback(url: str, *, expect: Measurement | None, attempts: int, delay: float) -> Measurement:
    """Measure the endpoint, allowing it to catch up with a just-completed upload."""
    last: Measurement | None = None
    for attempt in range(1, max(1, attempts) + 1):
        last = measure_url(url)
        if expect is None or last == expect:
            return last
        if attempt < attempts:
            print(f"publish: endpoint serves {last.describe()}, waiting for {expect.describe()}")
            time.sleep(delay)
    assert last is not None
    return last


# --------------------------------------------------------------------- commands


def command_publish(args: argparse.Namespace) -> int:
    descriptor_path = descriptor_for(args.datasets_dir, args.dataset)
    document = load_descriptor(descriptor_path)
    resource = pick_resource(document, descriptor_path, args.resource)
    url = resource_url(resource, descriptor_path)

    local_file = Path(args.file)
    if not local_file.is_file():
        raise PublishError(f"nothing to publish at {local_file}")
    local = measure_file(local_file)
    if local.size == 0:
        raise PublishError(f"{local_file} is empty; refusing to publish it")
    print(f"publish: {local_file} is {local.describe()}")

    protocol_dir = resolve_protocol_dir(args.dataset, descriptor_path, args.protocol_dir)
    if protocol_dir is None:
        # No Protocol governs this descriptor, so there is no Run to require and
        # nothing is being waved through: the failure this guard exists for is a
        # Run that stopped before `describe`, which needs a Run to have happened.
        print(f"publish: no Protocol for {args.dataset}; no Run record governs these bytes")
    elif not protocol_dir.joinpath(*ARCFORM_STATE_SUBPATH).is_dir():
        # A Protocol `arc run` has never run here, so no Run can be required of it.
        # The datasets whose Parquet the publishing pipeline builds itself, rather
        # than reading a Protocol's terminal output, are in this case. An EMPTY runs
        # directory is NOT — that reaches the refusal below.
        print(f"publish: {protocol_dir} has no arcform state; no Run governs these bytes")
    else:
        run = require_run_provenance(protocol_dir, descriptor_path, local_file, local)
        print(f"publish: Run {run.run_id} produced both {local_file} and {descriptor_path}")

    run_uploader(args.uploader, local_file, url)

    served = readback(url, expect=local, attempts=args.readback_attempts, delay=args.readback_delay)
    if served != local:
        raise Refusal(
            f"{url} serves {served.describe()} after uploading {local.describe()}; "
            "the descriptor is unchanged"
        )

    stamp(resource, served)
    save_descriptor(descriptor_path, document)
    print(f"publish: {descriptor_path} declares {served.describe()}")
    return EXIT_OK


def command_provenance(args: argparse.Namespace) -> int:
    descriptor_path = descriptor_for(args.datasets_dir, args.dataset)
    local_file = Path(args.file)
    if not local_file.is_file():
        raise PublishError(f"nothing to check at {local_file}")
    measured = measure_file(local_file)
    # Asked for on its own, this is a question rather than a precondition, so an
    # unanswerable one is an error and never a pass. `publish` proceeds where no
    # Protocol governs the descriptor; here the caller asked which Run produced
    # these bytes, and "there is no Protocol to ask" is not an answer.
    protocol_dir = resolve_protocol_dir(args.dataset, descriptor_path, args.protocol_dir)
    if protocol_dir is None:
        raise PublishError(
            f"no Protocol governs {descriptor_path}: no {PROTOCOL_MARKER} beside it, and it is not a "
            f"copy of {REPO_DATASETS / args.dataset / descriptor_path.name}. Pass --protocol-dir"
        )
    run = require_run_provenance(protocol_dir, descriptor_path, local_file, measured)
    print(
        f"provenance: Run {run.run_id} produced {local_file} ({measured.describe()}) "
        f"and {descriptor_path}, and finished {run.outcome}"
    )
    return EXIT_OK


def command_restamp(args: argparse.Namespace) -> int:
    descriptor_path = descriptor_for(args.datasets_dir, args.dataset)
    document = load_descriptor(descriptor_path)
    resource = pick_resource(document, descriptor_path, args.resource)
    url = resource_url(resource, descriptor_path)

    before = declared_measurement(resource)
    served = measure_url(url)
    stamp(resource, served)
    save_descriptor(descriptor_path, document)

    if before == served:
        print(f"restamp: {descriptor_path} already declared {served.describe()}")
    else:
        print(
            f"restamp: {descriptor_path} declared {before.describe() if before else 'nothing'}; "
            f"{url} serves {served.describe()}"
        )
    return EXIT_OK


def command_verify(args: argparse.Namespace) -> int:
    slugs = args.dataset or sorted(p.parent.name for p in args.datasets_dir.glob("*/datapackage.json"))
    if not slugs:
        raise PublishError(f"no */datapackage.json under {args.datasets_dir}")

    disagreements: list[str] = []
    for slug in slugs:
        descriptor_path = descriptor_for(args.datasets_dir, slug)
        document = load_descriptor(descriptor_path)
        for resource in document.get("resources") or []:
            name = resource.get("name") or slug
            url = resource_url(resource, descriptor_path)
            declared = declared_measurement(resource)
            if declared is None:
                disagreements.append(f"{slug} :: {name}\n    declares no bytes/hash for {url}")
                continue
            served = measure_url(url)
            if served == declared:
                print(f"verify: {slug} :: {name} — {served.describe()}")
                continue
            detail = [f"{slug} :: {name}"]
            if served.size != declared.size:
                detail.append(f"    bytes: descriptor declares {declared.size:,}; the endpoint serves {served.size:,}")
            if served.digest != declared.digest:
                detail.append(f"    hash: descriptor declares {declared.digest}; the endpoint serves {served.digest}")
            disagreements.append("\n".join(detail))

    if disagreements:
        print(f"\nverify: {len(disagreements)} disagreement(s) between descriptor and endpoint\n")
        for line in disagreements:
            print(line)
        print(f"\nverify: FAILED — {len(disagreements)} of {len(slugs)} dataset(s) describe an object that moved")
        return EXIT_DISAGREEMENT
    print(f"verify: {len(slugs)} dataset(s) declare what the endpoint serves ({', '.join(slugs)})")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--datasets-dir", default=Path("datasets"), type=Path)
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--resource", metavar="NAME", help="name the resource when the package declares several")

    def add_protocol(sub: argparse.ArgumentParser) -> None:
        sub.add_argument(
            "--protocol-dir",
            type=Path,
            metavar="DIR",
            help="the Protocol whose Runs govern this descriptor; inferred when not given",
        )

    publish = subparsers.add_parser("publish", help="upload a file, then declare what the endpoint serves back")
    publish.add_argument("--dataset", required=True, metavar="SLUG")
    publish.add_argument("--file", required=True, metavar="PATH", help="the built artefact to upload")
    publish.add_argument(
        "--uploader",
        default=os.environ.get("DATASET_UPLOAD_CMD"),
        metavar="CMD",
        help="command carrying {file} and {url}; defaults to $DATASET_UPLOAD_CMD",
    )
    publish.add_argument("--readback-attempts", type=int, default=READBACK_ATTEMPTS)
    publish.add_argument("--readback-delay", type=float, default=READBACK_DELAY)
    add_common(publish)
    add_protocol(publish)
    publish.set_defaults(handler=command_publish)

    provenance = subparsers.add_parser(
        "provenance", help="refuse bytes and a descriptor that no one finished Run produced"
    )
    provenance.add_argument("--dataset", required=True, metavar="SLUG")
    provenance.add_argument("--file", required=True, metavar="PATH", help="the built artefact about to be published")
    add_protocol(provenance)
    provenance.set_defaults(handler=command_provenance, resource=None)

    restamp = subparsers.add_parser(
        "restamp", help="declare what the endpoint already serves, for an object published before this seam"
    )
    restamp.add_argument("--dataset", required=True, metavar="SLUG")
    add_common(restamp)
    restamp.set_defaults(handler=command_restamp)

    verify = subparsers.add_parser("verify", help="refuse when a descriptor and its endpoint disagree")
    verify.add_argument("--dataset", action="append", metavar="SLUG", help="restrict to one dataset (repeatable)")
    verify.set_defaults(handler=command_verify, resource=None)

    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    if args.command == "publish" and not args.uploader:
        print("publish: no uploader — pass --uploader or set DATASET_UPLOAD_CMD", file=sys.stderr)
        return EXIT_ERROR
    try:
        return int(args.handler(args))
    except Refusal as exc:
        print(f"{args.command}: REFUSED — {exc}", file=sys.stderr)
        return EXIT_DISAGREEMENT
    except PublishError as exc:
        print(f"{args.command}: could not complete — {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
