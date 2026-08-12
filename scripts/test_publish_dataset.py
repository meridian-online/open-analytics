#!/usr/bin/env python3
"""Self-test for publish_dataset.py — proves the seam can fail, and on what.

Every case runs a real HTTP object store on localhost. `publish` uploads through
a real subprocess uploader that really PUTs, and the assertions compare the
descriptor on disk against the bytes the server is actually holding — not
against anything the tool reported. A tool that says it stamped and did not is
therefore reddened by the comparison rather than believed.

The case this exists for is `test_publish_declares_what_the_endpoint_serves`:
delete the stamp from `command_publish` and it goes red, because the descriptor
still declares the object the endpoint no longer serves. That is the failure
that shipped — an upload and a declaration as two separate acts.

The Run-provenance cases below cover the second failure that shipped, on
2026-08-12: a Run wrote the terminal Parquet, failed at the step between the
export and the describe, and left a rebuilt artefact beside a descriptor
measured against the bytes it replaced. Their fixtures are B4 Run records of the
shape `arc run` writes, run against the shipped `publish_dataset.py` rather than
a copy of its logic. Two of them are load-bearing against a guard that merely
distrusts every skip: `test_a_clean_skip_of_describe_still_publishes` and
`test_an_older_finished_run_licenses_bytes_a_later_one_only_reproduced` both go
red on a guard that refuses whenever `describe` did not execute, which would
make every re-run of an unchanged Protocol unpublishable.

No case reaches the network. The store is bound to 127.0.0.1 on an ephemeral
port, so the suite is offline and cannot be flaky for a reason that has nothing
to do with the seam.

Coverage is not complete, and the gaps are named rather than implied: the
readback retry loop, the multi-resource `--resource` selector and the
`x-` passthrough of unrelated package keys are exercised only incidentally, and
deleting the retry loop leaves this suite green. On the provenance side, no case
covers a Run record whose artefact asset is a glob, and none covers a Protocol
with more than one terminal artefact.
"""

from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

PUBLISHER = Path(__file__).with_name("publish_dataset.py")

sys.path.insert(0, str(Path(__file__).parent))
from publish_dataset import CHUNK  # noqa: E402 - the size of one read, so a case can span several

EXIT_OK = 0
EXIT_DISAGREEMENT = 1
EXIT_ERROR = 2

# A faithful uploader: PUT the file at the URL. Written to disk rather than
# passed as `python -c` so the --uploader template stays a plain argv line.
PUT_SOURCE = '''\
import pathlib, sys, urllib.request

source, url = sys.argv[1], sys.argv[2]
body = pathlib.Path(source).read_bytes()
if len(sys.argv) > 3:                      # upload something other than the file
    body = sys.argv[3].encode()
request = urllib.request.Request(url, data=body, method="PUT")
with urllib.request.urlopen(request) as response:
    sys.exit(0 if response.status < 300 else 1)
'''

# An uploader that reports success and moves nothing — the shape that leaves an
# endpoint serving the previous object while the caller believes it published.
NO_OP_SOURCE = "import sys\nsys.exit(0)\n"

# An uploader that fails outright.
FAILING_SOURCE = "import sys\nsys.stderr.write('the bucket said no\\n')\nsys.exit(3)\n"


def sha256_of(body: bytes) -> str:
    return "sha256:" + hashlib.sha256(body).hexdigest()


def sha256_hex(body: bytes) -> str:
    """Bare hex — the form arcform writes into a Run record's `content_hash`."""
    return hashlib.sha256(body).hexdigest()


# arcform's own encoding of a step that never got there: `skipped` with no reason.
# A step skipped because its output was already current carries one.
NEVER_REACHED = {"state": "skipped", "skip_reason": None}
CLEAN_SKIP = {"state": "skipped", "skip_reason": "hash_clean"}
RAN = {"state": "success", "skip_reason": None}
FAILED = {"state": "failed", "skip_reason": None}


class ObjectStoreHandler(BaseHTTPRequestHandler):
    """GET/PUT over an in-memory object store, with a truncating mode."""

    def log_message(self, *_args: Any) -> None:  # keep the suite's output readable
        return

    def do_PUT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler naming
        length = int(self.headers.get("Content-Length") or 0)
        self.server.objects[self.path] = self.rfile.read(length)  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        body = self.server.objects.get(self.path)  # type: ignore[attr-defined]
        if body is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        withheld = self.server.withhold  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        if not self.server.omit_length:  # type: ignore[attr-defined]
            self.send_header("Content-Length", str(len(body)))  # announce the whole object
        self.end_headers()
        self.wfile.write(body[: len(body) - withheld] if withheld else body)


class ObjectStore:
    def __init__(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), ObjectStoreHandler)
        self.server.objects = {}  # type: ignore[attr-defined]
        self.server.withhold = 0  # type: ignore[attr-defined]
        self.server.omit_length = False  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def origin(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    def url(self, name: str) -> str:
        return f"{self.origin}/{name}"

    def put(self, name: str, body: bytes) -> None:
        self.server.objects[f"/{name}"] = body  # type: ignore[attr-defined]

    def get(self, name: str) -> bytes | None:
        return self.server.objects.get(f"/{name}")  # type: ignore[attr-defined]

    def truncate_by(self, count: int) -> None:
        self.server.withhold = count  # type: ignore[attr-defined]

    def stop_announcing_length(self) -> None:
        self.server.omit_length = True  # type: ignore[attr-defined]

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class PublishSeamSelfTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="publish-seam-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.datasets = self.root / "datasets"
        self.datasets.mkdir()

        self.store = ObjectStore()
        self.addCleanup(self.store.close)

        self.put_script = self.root / "put.py"
        self.put_script.write_text(PUT_SOURCE, encoding="utf-8")
        self.no_op_script = self.root / "no_op_upload.py"
        self.no_op_script.write_text(NO_OP_SOURCE, encoding="utf-8")
        self.failing_script = self.root / "failing_upload.py"
        self.failing_script.write_text(FAILING_SOURCE, encoding="utf-8")

    # ------------------------------------------------------------- fixtures

    def write_dataset(
        self,
        slug: str = "widgets",
        *,
        declared_bytes: int | None = 1,
        declared_hash: str | None = "sha256:" + "0" * 64,
        url: str | None = None,
    ) -> Path:
        package_dir = self.datasets / slug
        package_dir.mkdir(parents=True)
        resource: dict[str, Any] = {
            "name": slug,
            "format": "parquet",
            "mediatype": "application/vnd.apache.parquet",
            "path": url if url is not None else self.store.url(f"{slug}.parquet"),
            "schema": {"fields": [{"name": "code", "type": "string", "description": "a coded façade"}]},
        }
        if declared_bytes is not None:
            resource["bytes"] = declared_bytes
        if declared_hash is not None:
            resource["hash"] = declared_hash
        descriptor = package_dir / "datapackage.json"
        descriptor.write_text(
            json.dumps(
                {
                    "$schema": "https://datapackage.org/profiles/2.0/datapackage.json",
                    "created": "2026-08-11T00:00:00Z",
                    "name": slug,
                    "title": "Widgets — a façade",
                    "resources": [resource],
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        return descriptor

    def artefact(self, body: bytes, name: str = "widgets.parquet", slug: str = "widgets") -> Path:
        """A built artefact where a Protocol puts one: <dataset>/build/<name>."""
        path = self.datasets / slug / "build" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return path

    def write_run_record(
        self,
        artefact: Path,
        *,
        slug: str = "widgets",
        run_id: str = "20260812-000000-aaaaaaaa",
        started_at: str = "2026-08-12T00:00:00Z",
        outcome: str = "success",
        export_status: dict[str, Any] | None = None,
        describe_status: dict[str, Any] | None = None,
        describe_producer: str = "describe",
        artefact_hash: str | None = None,
        descriptor_hash: str | None = None,
        record_descriptor: bool = True,
        contract_version: str = "b4/1",
        raw: str | None = None,
    ) -> Path:
        """A B4 Run record of the shape `arc run` leaves under build/.arcform/runs/.

        Defaults describe the good case: one Run exported the artefact, described it,
        and finished. Every argument above exists so a case can move exactly one thing.
        """
        protocol_dir = self.datasets / slug
        descriptor = protocol_dir / "datapackage.json"
        runs_dir = protocol_dir / "build" / ".arcform" / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        path = runs_dir / f"{run_id}.json"
        if raw is not None:
            path.write_text(raw, encoding="utf-8")
            return path

        relative = artefact.relative_to(protocol_dir).as_posix()
        assets: list[dict[str, Any]] = [
            {
                "id": f"file:{relative}",
                "kind": "file",
                "name": relative,
                "path": relative,
                "bytes": artefact.stat().st_size,
                "row_count": None,
                "content_hash": artefact_hash or sha256_hex(artefact.read_bytes()),
                "produced_by": "export",
                "consumed_by": [describe_producer],
            }
        ]
        if record_descriptor:
            assets.append(
                {
                    "id": "file:datapackage.json",
                    "kind": "file",
                    "name": "datapackage.json",
                    "path": "datapackage.json",
                    "bytes": descriptor.stat().st_size,
                    "row_count": None,
                    "content_hash": descriptor_hash or sha256_hex(descriptor.read_bytes()),
                    "produced_by": describe_producer,
                    "consumed_by": [],
                }
            )
        steps = [
            {"name": "export", "kind": "op", "status": dict(export_status or RAN)},
            {"name": describe_producer, "kind": "op", "status": dict(describe_status or RAN)},
        ]
        path.write_text(
            json.dumps(
                {
                    "contract_version": contract_version,
                    "run": {
                        "run_id": run_id,
                        "started_at": started_at,
                        "finished_at": started_at,
                        "outcome": outcome,
                    },
                    "assets": assets,
                    "steps": steps,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return path

    def publishable(self, body: bytes, *, slug: str = "widgets", name: str = "widgets.parquet", **record: Any) -> Path:
        """An artefact AND the Run record that says a finished Run produced it.

        The record is written after the descriptor is on disk, so it carries the
        descriptor as it stands — which is what a Run that described these bytes
        would have recorded.
        """
        path = self.artefact(body, name=name, slug=slug)
        self.write_run_record(path, slug=slug, **record)
        return path

    def uploader(self, *extra: str, script: Path | None = None) -> str:
        parts = [sys.executable, str(script or self.put_script), "{file}", "{url}"]
        return " ".join(shlex.quote(part) for part in parts + list(extra))

    # -------------------------------------------------------------- running

    def run_seam(self, *argv: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(PUBLISHER), "--datasets-dir", str(self.datasets), *argv],
            capture_output=True,
            text=True,
            check=False,
        )

    def publish(self, artefact: Path, *extra: str, slug: str = "widgets", upload: tuple[str, ...] = ()):
        return self.run_seam(
            "publish",
            "--dataset",
            slug,
            "--file",
            str(artefact),
            "--uploader",
            self.uploader(*upload),
            "--readback-attempts",
            "1",
            "--readback-delay",
            "0",
            *extra,
        )

    def assertOutcome(  # noqa: N802 - unittest naming
        self,
        result: subprocess.CompletedProcess[str],
        expected_exit: int,
        *needles: str,
    ) -> None:
        output = result.stdout + result.stderr
        self.assertEqual(
            result.returncode,
            expected_exit,
            f"expected exit {expected_exit}, got {result.returncode}\n--- output ---\n{output}",
        )
        for needle in needles:
            self.assertIn(needle, output, f"message did not name {needle!r}\n--- output ---\n{output}")

    def resource_of(self, descriptor: Path) -> dict[str, Any]:
        return json.loads(descriptor.read_text(encoding="utf-8"))["resources"][0]

    def assertDeclaresWhatIsServed(self, descriptor: Path, name: str = "widgets.parquet") -> None:  # noqa: N802
        """Compare the descriptor against the store's own copy, not against the tool's word."""
        served = self.store.get(name)
        self.assertIsNotNone(served, f"the store holds no object at /{name}")
        resource = self.resource_of(descriptor)
        self.assertEqual(resource.get("bytes"), len(served), "declared bytes is not what the endpoint holds")
        self.assertEqual(resource.get("hash"), sha256_of(served), "declared hash is not what the endpoint holds")

    # ------------------------------------------------- the act this seam joins

    def test_publish_declares_what_the_endpoint_serves(self) -> None:
        """An upload refreshes the declared size and hash, or it is not a publish."""
        descriptor = self.write_dataset()
        body = b"PAR1" + b"widget rows" * 40
        self.assertOutcome(self.publish(self.publishable(body)), EXIT_OK)
        self.assertDeclaresWhatIsServed(descriptor)
        self.assertEqual(self.resource_of(descriptor)["bytes"], len(body))

    def test_a_second_publish_moves_the_declaration_with_the_object(self) -> None:
        """The 2026-07-19 shape: a republish must not leave the first size behind."""
        descriptor = self.write_dataset()
        self.assertOutcome(self.publish(self.publishable(b"PAR1" + b"a" * 100)), EXIT_OK)
        first = self.resource_of(descriptor)["bytes"]
        self.assertOutcome(self.publish(self.publishable(b"PAR1" + b"b" * 500)), EXIT_OK)
        self.assertDeclaresWhatIsServed(descriptor)
        self.assertNotEqual(self.resource_of(descriptor)["bytes"], first)

    def test_a_body_longer_than_one_read_is_measured_whole(self) -> None:
        """Every other case fits in a single read(); the published objects do not.

        gleif is ~123 of these chunks. A size or digest accumulated from only the
        last one would be wrong for every large dataset and right for every fixture
        above, so this is the case that exercises the loop rather than one pass of it.
        """
        descriptor = self.write_dataset()
        body = (b"PAR1" + b"long" * 64) * (CHUNK // 100) + b"tail"
        self.assertGreater(len(body), 2 * CHUNK, "the fixture must span more than two reads")
        self.assertOutcome(self.publish(self.publishable(body)), EXIT_OK)
        self.assertEqual(self.resource_of(descriptor)["bytes"], len(body))
        self.assertEqual(self.resource_of(descriptor)["hash"], sha256_of(body))
        self.assertDeclaresWhatIsServed(descriptor)

    def test_publish_refreshes_the_hash_when_the_size_does_not_move(self) -> None:
        """`hash` is stamped by the same act as `bytes`, not carried over beside it."""
        descriptor = self.write_dataset()
        self.assertOutcome(self.publish(self.publishable(b"PAR1" + b"a" * 100)), EXIT_OK)
        first = self.resource_of(descriptor)
        self.assertOutcome(self.publish(self.publishable(b"PAR1" + b"b" * 100)), EXIT_OK)
        second = self.resource_of(descriptor)
        self.assertEqual(first["bytes"], second["bytes"], "the fixture must hold the size still")
        self.assertNotEqual(first["hash"], second["hash"], "the hash was not refreshed with the object")
        self.assertDeclaresWhatIsServed(descriptor)

    def test_stamping_rewrites_only_bytes_and_hash(self) -> None:
        descriptor = self.write_dataset()
        before = descriptor.read_text(encoding="utf-8")
        self.assertOutcome(self.publish(self.publishable(b"PAR1" + b"x" * 64)), EXIT_OK)
        after = descriptor.read_text(encoding="utf-8")

        changed = [
            (left, right)
            for left, right in zip(before.splitlines(), after.splitlines())
            if left != right
        ]
        self.assertEqual(len(before.splitlines()), len(after.splitlines()), "the stamp reflowed the file")
        self.assertEqual(len(changed), 2, f"expected 2 changed lines, got {changed}")
        self.assertTrue(all(('"bytes"' in left or '"hash"' in left) for left, _ in changed), changed)
        self.assertIn("façade", after, "the stamp escaped non-ASCII the describe step leaves alone")
        self.assertTrue(after.endswith("}\n"), "the stamp dropped the trailing newline")

    # --------------------------------------------- a half-done publish refuses

    def test_publish_refuses_when_the_endpoint_serves_something_else(self) -> None:
        descriptor = self.write_dataset()
        before = descriptor.read_text(encoding="utf-8")
        result = self.publish(self.publishable(b"PAR1" + b"x" * 64), upload=("a different object entirely",))
        self.assertOutcome(result, EXIT_DISAGREEMENT, "REFUSED", "the descriptor is unchanged")
        self.assertEqual(descriptor.read_text(encoding="utf-8"), before)

    def test_an_uploader_that_reports_success_and_moves_nothing_refuses(self) -> None:
        """The endpoint still holds the previous object, so nothing may be declared."""
        descriptor = self.write_dataset()
        self.store.put("widgets.parquet", b"PAR1" + b"the object from last time" * 3)
        before = descriptor.read_text(encoding="utf-8")
        result = self.run_seam(
            "publish", "--dataset", "widgets", "--file", str(self.publishable(b"PAR1" + b"never sent" * 20)),
            "--uploader", self.uploader(script=self.no_op_script),
            "--readback-attempts", "1", "--readback-delay", "0",
        )
        self.assertOutcome(result, EXIT_DISAGREEMENT, "REFUSED", "the descriptor is unchanged")
        self.assertEqual(descriptor.read_text(encoding="utf-8"), before)

    def test_publish_refuses_a_different_object_of_exactly_the_same_size(self) -> None:
        """Sizes agreeing is not the upload landing — only the digest says which object it is."""
        descriptor = self.write_dataset()
        before = descriptor.read_text(encoding="utf-8")
        mine = b"PAR1" + b"a" * 100
        theirs = "P" + "b" * (len(mine) - 1)  # same length, different bytes
        result = self.publish(self.publishable(mine), upload=(theirs,))
        self.assertOutcome(result, EXIT_DISAGREEMENT, "REFUSED", "the descriptor is unchanged")
        self.assertIn(f"{len(mine)} bytes", result.stdout + result.stderr)
        self.assertEqual(descriptor.read_text(encoding="utf-8"), before)

    def test_an_endpoint_that_announces_no_length_is_not_measured(self) -> None:
        """With no announced length there is nothing to check a truncation against."""
        descriptor = self.write_dataset()
        before = descriptor.read_text(encoding="utf-8")
        self.store.put("widgets.parquet", b"PAR1" + b"unmeasurable" * 9)
        self.store.stop_announcing_length()
        self.assertOutcome(
            self.run_seam("restamp", "--dataset", "widgets"), EXIT_ERROR, "announced no usable Content-Length"
        )
        self.assertEqual(descriptor.read_text(encoding="utf-8"), before)

    def test_publish_reports_an_error_when_the_upload_never_landed(self) -> None:
        descriptor = self.write_dataset()
        before = descriptor.read_text(encoding="utf-8")
        result = self.run_seam(
            "publish", "--dataset", "widgets", "--file", str(self.publishable(b"PAR1")),
            "--uploader", self.uploader(script=self.no_op_script),
            "--readback-attempts", "1", "--readback-delay", "0",
        )
        self.assertOutcome(result, EXIT_ERROR, "could not complete", "404")
        self.assertEqual(descriptor.read_text(encoding="utf-8"), before)

    def test_a_failing_uploader_leaves_the_descriptor_alone(self) -> None:
        descriptor = self.write_dataset()
        before = descriptor.read_text(encoding="utf-8")
        result = self.run_seam(
            "publish", "--dataset", "widgets", "--file", str(self.publishable(b"PAR1")),
            "--uploader", self.uploader(script=self.failing_script),
            "--readback-attempts", "1", "--readback-delay", "0",
        )
        self.assertOutcome(result, EXIT_ERROR, "uploader exited 3", "the descriptor is unchanged")
        self.assertEqual(descriptor.read_text(encoding="utf-8"), before)

    def test_a_truncated_readback_is_never_declared(self) -> None:
        """A short body is a refusal, not a smaller number: read() returns what arrived."""
        descriptor = self.write_dataset()
        before = descriptor.read_text(encoding="utf-8")
        self.store.truncate_by(5)
        result = self.publish(self.publishable(b"PAR1" + b"y" * 200))
        self.assertOutcome(result, EXIT_ERROR, "delivered an incomplete response")
        self.assertEqual(descriptor.read_text(encoding="utf-8"), before)

    def test_an_empty_artefact_is_not_published(self) -> None:
        descriptor = self.write_dataset()
        before = descriptor.read_text(encoding="utf-8")
        self.assertOutcome(self.publish(self.publishable(b"")), EXIT_ERROR, "refusing to publish it")
        self.assertEqual(descriptor.read_text(encoding="utf-8"), before)
        self.assertIsNone(self.store.get("widgets.parquet"), "an empty artefact reached the endpoint")

    def test_an_uploader_without_both_placeholders_is_rejected(self) -> None:
        self.write_dataset()
        result = self.run_seam(
            "publish", "--dataset", "widgets", "--file", str(self.publishable(b"PAR1")),
            "--uploader", "true {file}",
        )
        self.assertOutcome(result, EXIT_ERROR, "must carry both {file} and {url}")

    # ------------------------------- the descriptor came out of the same Run

    def assertNothingReachedTheEndpoint(self, descriptor: Path, before: str) -> None:  # noqa: N802
        """A refusal before the upload: the object never moved and nothing was declared."""
        self.assertIsNone(self.store.get("widgets.parquet"), "the artefact was uploaded anyway")
        self.assertEqual(descriptor.read_text(encoding="utf-8"), before, "the descriptor was rewritten anyway")

    def test_publish_refuses_when_the_run_never_reached_describe(self) -> None:
        """2026-08-12: export wrote the Parquet, the step after it failed, describe never ran.

        The Run's asset graph still lists datapackage.json as `produced_by: describe`
        with a content hash — the STALE descriptor's, because the graph says who owns
        a file, not who wrote it this time. Only `steps[].status` tells them apart.
        """
        descriptor = self.write_dataset()
        before = descriptor.read_text(encoding="utf-8")
        artefact = self.publishable(
            b"PAR1" + b"rebuilt rows" * 40,
            describe_status=NEVER_REACHED,
            outcome="partial",
        )
        result = self.publish(artefact)
        self.assertOutcome(result, EXIT_DISAGREEMENT, "REFUSED", "never got to 'describe'", "never reached")
        self.assertNothingReachedTheEndpoint(descriptor, before)

    def test_a_clean_skip_of_describe_still_publishes(self) -> None:
        """A re-run of an unchanged Protocol skips describe WITH a reason, and is fine.

        Without this case a guard that refuses every skip passes the whole suite while
        making every unchanged republish impossible.
        """
        descriptor = self.write_dataset()
        artefact = self.publishable(b"PAR1" + b"unchanged" * 30, describe_status=CLEAN_SKIP, export_status=CLEAN_SKIP)
        self.assertOutcome(self.publish(artefact), EXIT_OK)
        self.assertDeclaresWhatIsServed(descriptor)

    def test_publish_refuses_bytes_no_run_produced(self) -> None:
        descriptor = self.write_dataset()
        before = descriptor.read_text(encoding="utf-8")
        result = self.publish(self.artefact(b"PAR1" + b"from nowhere" * 10))
        self.assertOutcome(result, EXIT_DISAGREEMENT, "REFUSED", "no Run record under")
        self.assertNothingReachedTheEndpoint(descriptor, before)

    def test_publish_refuses_an_artefact_rebuilt_since_the_run(self) -> None:
        """The Run record names the file; these are not the bytes it recorded."""
        descriptor = self.write_dataset()
        before = descriptor.read_text(encoding="utf-8")
        artefact = self.publishable(b"PAR1" + b"as described" * 20)
        artefact.write_bytes(b"PAR1" + b"rebuilt behind the record" * 20)
        result = self.publish(artefact)
        self.assertOutcome(result, EXIT_DISAGREEMENT, "REFUSED", "produced the bytes at", "outside a Run")
        self.assertNothingReachedTheEndpoint(descriptor, before)

    def test_publish_refuses_a_descriptor_edited_since_the_run(self) -> None:
        descriptor = self.write_dataset()
        artefact = self.publishable(b"PAR1" + b"described once" * 20)
        document = json.loads(descriptor.read_text(encoding="utf-8"))
        document["title"] = "Widgets — retitled by hand after the Run"
        descriptor.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        before = descriptor.read_text(encoding="utf-8")
        result = self.publish(artefact)
        self.assertOutcome(result, EXIT_DISAGREEMENT, "REFUSED", "edited or regenerated after the Run")
        self.assertNothingReachedTheEndpoint(descriptor, before)

    def test_publish_refuses_a_run_that_did_not_finish(self) -> None:
        """describe ran, and a later step still failed — the Protocol did not hold."""
        descriptor = self.write_dataset()
        before = descriptor.read_text(encoding="utf-8")
        artefact = self.publishable(
            b"PAR1" + b"described then failed" * 10,
            outcome="partial",
            export_status=FAILED,
        )
        result = self.publish(artefact)
        self.assertOutcome(result, EXIT_DISAGREEMENT, "REFUSED", "finished 'partial'", "export failed")
        self.assertNothingReachedTheEndpoint(descriptor, before)

    def test_publish_refuses_a_run_that_records_no_descriptor(self) -> None:
        descriptor = self.write_dataset()
        before = descriptor.read_text(encoding="utf-8")
        artefact = self.publishable(b"PAR1" + b"undescribed" * 10, record_descriptor=False)
        result = self.publish(artefact)
        self.assertOutcome(result, EXIT_DISAGREEMENT, "REFUSED", "records no datapackage.json")
        self.assertNothingReachedTheEndpoint(descriptor, before)

    def test_an_older_finished_run_licenses_bytes_a_later_one_only_reproduced(self) -> None:
        """Two Runs claim the same bytes; one of them finished and described them.

        A re-run that rebuilds an identical Parquet and then fails has not made the
        descriptor untrue — it is still the descriptor of exactly these bytes. The
        check is existential over the records, not a verdict on the newest one.
        """
        descriptor = self.write_dataset()
        artefact = self.publishable(
            b"PAR1" + b"identical either way" * 20,
            run_id="20260812-010000-finished",
            started_at="2026-08-12T01:00:00Z",
        )
        self.write_run_record(
            artefact,
            run_id="20260812-020000-partial0",
            started_at="2026-08-12T02:00:00Z",
            outcome="partial",
            describe_status=NEVER_REACHED,
        )
        self.assertOutcome(self.publish(artefact), EXIT_OK)
        self.assertDeclaresWhatIsServed(descriptor)

    def test_a_run_record_of_an_unknown_contract_version_is_an_error_not_a_pass(self) -> None:
        descriptor = self.write_dataset()
        before = descriptor.read_text(encoding="utf-8")
        artefact = self.publishable(b"PAR1" + b"future shape" * 10, contract_version="b5/1")
        result = self.publish(artefact)
        self.assertOutcome(result, EXIT_ERROR, "could not complete", "'b5/1'", "will not guess")
        self.assertNothingReachedTheEndpoint(descriptor, before)

    def test_an_unreadable_run_record_is_an_error_not_a_pass(self) -> None:
        """The thing that would have refused could not be read, so nothing is published."""
        descriptor = self.write_dataset()
        before = descriptor.read_text(encoding="utf-8")
        artefact = self.publishable(b"PAR1" + b"unparsable" * 10, raw="{ not json")
        result = self.publish(artefact)
        self.assertOutcome(result, EXIT_ERROR, "could not complete", "cannot read the Run record")
        self.assertNothingReachedTheEndpoint(descriptor, before)

    def test_provenance_reports_on_its_own_without_uploading(self) -> None:
        """The seam a publish pipeline that does its own uploading can call."""
        self.write_dataset()
        good = self.publishable(b"PAR1" + b"traceable" * 30)
        self.assertOutcome(
            self.run_seam("provenance", "--dataset", "widgets", "--file", str(good)),
            EXIT_OK,
            "finished success",
        )
        self.assertIsNone(self.store.get("widgets.parquet"), "provenance uploaded something")

        stale = self.publishable(b"PAR1" + b"outran its descriptor" * 30, describe_status=NEVER_REACHED)
        self.assertOutcome(
            self.run_seam("provenance", "--dataset", "widgets", "--file", str(stale)),
            EXIT_DISAGREEMENT,
            "REFUSED",
            "never got to 'describe'",
        )

    # --------------------------------------- a move behind the descriptor's back

    def test_verify_catches_a_republish_that_moved_the_object(self) -> None:
        descriptor = self.write_dataset()
        self.assertOutcome(self.publish(self.publishable(b"PAR1" + b"z" * 300)), EXIT_OK)
        self.store.put("widgets.parquet", b"PAR1" + b"z" * 900)  # republished out of band
        self.assertOutcome(
            self.run_seam("verify", "--dataset", "widgets"),
            EXIT_DISAGREEMENT,
            "descriptor declares 304",
            "the endpoint serves 904",
            "hash: descriptor declares",
        )
        self.assertEqual(self.resource_of(descriptor)["bytes"], 304, "verify must not repair what it reports")

    def test_verify_catches_a_hash_that_moved_while_the_size_held_still(self) -> None:
        self.write_dataset()
        self.assertOutcome(self.publish(self.publishable(b"PAR1" + b"m" * 300)), EXIT_OK)
        self.store.put("widgets.parquet", b"PAR1" + b"n" * 300)
        result = self.run_seam("verify", "--dataset", "widgets")
        self.assertOutcome(result, EXIT_DISAGREEMENT, "hash: descriptor declares")
        self.assertNotIn("bytes: descriptor declares", result.stdout + result.stderr)

    def test_verify_passes_when_the_descriptor_matches(self) -> None:
        self.write_dataset()
        self.assertOutcome(self.publish(self.publishable(b"PAR1" + b"q" * 111)), EXIT_OK)
        self.assertOutcome(
            self.run_seam("verify", "--dataset", "widgets"), EXIT_OK, "declare what the endpoint serves"
        )

    def test_verify_refuses_a_descriptor_that_declares_nothing(self) -> None:
        self.write_dataset(declared_bytes=None, declared_hash=None)
        self.store.put("widgets.parquet", b"PAR1")
        self.assertOutcome(self.run_seam("verify", "--dataset", "widgets"), EXIT_DISAGREEMENT, "declares no bytes/hash")

    def test_verify_of_an_absent_object_is_an_error_not_a_pass(self) -> None:
        self.write_dataset()
        self.assertOutcome(self.run_seam("verify", "--dataset", "widgets"), EXIT_ERROR, "could not complete", "404")

    # ------------------------------------------- adopting an object already out

    def test_restamp_adopts_the_object_the_endpoint_already_serves(self) -> None:
        descriptor = self.write_dataset(declared_bytes=278865, declared_hash="sha256:" + "7" * 64)
        body = b"PAR1" + b"already published" * 11
        self.store.put("widgets.parquet", body)
        self.assertOutcome(
            self.run_seam("restamp", "--dataset", "widgets"), EXIT_OK, "declared 278,865 bytes"
        )
        self.assertDeclaresWhatIsServed(descriptor)

    def test_restamp_refuses_a_truncated_readback(self) -> None:
        """restamp has no local artefact to disagree with, so the read is the only guard."""
        descriptor = self.write_dataset()
        before = descriptor.read_text(encoding="utf-8")
        self.store.put("widgets.parquet", b"PAR1" + b"already published" * 11)
        self.store.truncate_by(5)
        self.assertOutcome(
            self.run_seam("restamp", "--dataset", "widgets"), EXIT_ERROR, "delivered an incomplete response"
        )
        self.assertEqual(descriptor.read_text(encoding="utf-8"), before)

    def test_a_truncated_readback_is_not_reported_as_a_disagreement(self) -> None:
        """A short download would name a wrong size in a verdict about the data."""
        self.write_dataset()
        self.assertOutcome(self.publish(self.publishable(b"PAR1" + b"w" * 260)), EXIT_OK)
        self.store.truncate_by(5)
        self.assertOutcome(
            self.run_seam("verify", "--dataset", "widgets"), EXIT_ERROR, "delivered an incomplete response"
        )

    def test_restamp_will_not_invent_a_size_for_an_absent_object(self) -> None:
        descriptor = self.write_dataset()
        before = descriptor.read_text(encoding="utf-8")
        self.assertOutcome(self.run_seam("restamp", "--dataset", "widgets"), EXIT_ERROR, "404")
        self.assertEqual(descriptor.read_text(encoding="utf-8"), before)

    # ----------------------------------------- could-not-run is not a verdict

    def test_a_relative_resource_path_is_an_error(self) -> None:
        self.write_dataset(url="widgets.parquet")
        self.assertOutcome(
            self.run_seam("verify", "--dataset", "widgets"),
            EXIT_ERROR,
            "not an endpoint this seam can read back",
        )

    def test_a_malformed_descriptor_is_an_error(self) -> None:
        package_dir = self.datasets / "widgets"
        package_dir.mkdir(parents=True)
        (package_dir / "datapackage.json").write_text("{ not json", encoding="utf-8")
        self.assertOutcome(self.run_seam("verify", "--dataset", "widgets"), EXIT_ERROR, "cannot read descriptor")

    def test_an_empty_datasets_tree_is_an_error_not_a_pass(self) -> None:
        self.assertOutcome(self.run_seam("verify"), EXIT_ERROR, "no */datapackage.json")


if __name__ == "__main__":
    unittest.main(verbosity=2)
