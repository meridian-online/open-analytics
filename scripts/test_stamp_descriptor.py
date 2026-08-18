#!/usr/bin/env python3
"""Self-test for stamp_descriptor.py — proves the stamp lands, survives and can fail.

Four claims are made about a stamped object elsewhere in this repository, and each
one is a claim a reader has no way to check by looking at the code:

  1. what comes back out of the footer is this repository's descriptor with the two
     self-referential keys removed, and nothing else;
  2. stamping moves the footer and NOT the data — the `order_by` clauses in every
     Protocol exist to make an export byte-reproducible, and a stamp that re-encoded
     the rows would quietly retire that guarantee;
  3. the descriptor's `bytes` and `hash` are re-measured off the stamped file, since
     the figures `describe` took off the unstamped build are wrong the moment the
     stamp lands;
  4. reading the description costs one ranged request to the object's own URL and
     touches nothing else — no sibling file, no repository, no registry;

and one more that only matters because every Protocol runs the step on every run:
stamping an already-stamped object changes neither the object nor the descriptor.

Each case asserts a measurement rather than an intention. The last one runs against
a real HTTP server that records every request it receives, so "touches nothing else"
is a list of requests, not a reading of the source.

No case reaches the public internet. The server is on loopback, and the local cases
need no network at all.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from socketserver import TCPServer
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import stamp_descriptor as sd  # noqa: E402 - the module under test, found beside this file

STAMPER = Path(__file__).with_name("stamp_descriptor.py")

EXIT_OK = 0
EXIT_DISAGREEMENT = 1
EXIT_ERROR = 2


# ────────────────────────────────────────────────────────── a recording server


class RecordingHandler(BaseHTTPRequestHandler):
    """Serves one object, refuses everything else, and writes down every request."""

    def log_message(self, *args: Any) -> None:  # keep the test output readable
        pass

    def _respond(self, body_wanted: bool) -> None:
        self.server.requests.append((self.command, self.path, self.headers.get("Range")))
        if self.path != self.server.object_path:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = self.server.body
        wanted = self.headers.get("Range")
        match = re.match(r"bytes=(\d+)-(\d*)", wanted) if wanted else None
        if match:
            start = int(match.group(1))
            end = int(match.group(2)) if match.group(2) else len(body) - 1
            end = min(end, len(body) - 1)
            chunk = body[start : end + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(body)}")
            self.send_header("Content-Length", str(len(chunk)))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            if body_wanted:
                self.server.served += len(chunk)
                self.wfile.write(chunk)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        if body_wanted:
            self.server.served += len(body)
            self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler naming
        self._respond(True)

    def do_HEAD(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler naming
        self._respond(False)


class ObjectServer(TCPServer):
    allow_reuse_address = True

    def __init__(self, body: bytes, object_path: str = "/object.parquet") -> None:
        super().__init__(("127.0.0.1", 0), RecordingHandler)
        self.body = body
        self.object_path = object_path
        self.requests: list[tuple[str, str, str | None]] = []
        self.served = 0

    @property
    def url(self) -> str:
        host, port = self.server_address[0], self.server_address[1]
        return f"http://{host}:{port}{self.object_path}"

    def __enter__(self) -> ObjectServer:
        threading.Thread(target=self.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.shutdown()
        self.server_close()


# ─────────────────────────────────────────────────────────────────── fixtures


def build_dataset(root: Path, *, rows: int = 4) -> tuple[Path, Path]:
    """A scratch dataset: a real Parquet and the descriptor that describes it."""
    import duckdb

    root.mkdir(parents=True, exist_ok=True)
    parquet = root / "widgets.parquet"
    con = duckdb.connect()
    # `md5(i)` rather than a counter: a compressible column makes a small file, and a
    # small file cannot show a range read apart from a download.
    con.execute(
        f"COPY (SELECT printf('%08d', i) AS code, md5(i::VARCHAR) AS label FROM range({rows}) t(i) "
        f"ORDER BY code) TO '{parquet}' (FORMAT parquet, COMPRESSION zstd)"
    )
    con.close()
    document = {
        "$schema": "https://datapackage.org/profiles/2.0/datapackage.json",
        "name": "widgets",
        "title": "Widgets",
        "licenses": [{"name": "CC0-1.0"}],
        "resources": [
            {
                "bytes": 1,
                "format": "parquet",
                "hash": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
                "mediatype": "application/vnd.apache.parquet",
                "name": "widgets",
                "path": "widgets.parquet",
                "schema": {
                    "fields": [
                        {"name": "code", "type": "string", "constraints": {"pattern": "^[0-9]{8}$"}},
                        {"name": "label", "type": "string"},
                    ],
                    "primaryKey": ["code"],
                },
            }
        ],
    }
    descriptor = root / "datapackage.json"
    descriptor.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return descriptor, parquet


def data_section(path: Path) -> bytes:
    """Everything before the Parquet footer, parsed here rather than borrowed.

    Deliberately a second implementation: case 2 would prove nothing if it measured
    the data with the same function the code under test uses to decide it is happy.
    """
    raw = path.read_bytes()
    assert raw[:4] == b"PAR1" and raw[-4:] == b"PAR1", f"{path} is not a Parquet file"
    footer_length = int.from_bytes(raw[-8:-4], "little")
    return raw[: len(raw) - 8 - footer_length]


def run_cli(*argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(STAMPER), *argv], capture_output=True, text=True, check=False
    )


class StampDescriptorSelfTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="stamp-descriptor-test-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "widgets"

    def assertOutcome(  # noqa: N802 - unittest naming
        self, result: subprocess.CompletedProcess[str], expected_exit: int, *needles: str
    ) -> None:
        output = result.stdout + result.stderr
        self.assertEqual(
            result.returncode,
            expected_exit,
            f"expected exit {expected_exit}, got {result.returncode}\n--- output ---\n{output}",
        )
        for needle in needles:
            self.assertIn(needle, output, f"message did not name {needle!r}\n--- output ---\n{output}")

    # ───────────────────────────────────────────────── 1. what comes back out

    def test_the_object_returns_the_repository_copy_without_bytes_and_hash(self) -> None:
        descriptor, parquet = build_dataset(self.root)
        self.assertOutcome(
            run_cli("stamp", "--descriptor", str(descriptor), "--parquet", str(parquet)), EXIT_OK
        )
        result = run_cli("read", str(parquet))
        self.assertOutcome(result, EXIT_OK)
        carried = json.loads(result.stdout)
        expected = json.loads(descriptor.read_text(encoding="utf-8"))
        for key in ("bytes", "hash"):
            self.assertIn(key, expected["resources"][0], f"the repository copy should still declare {key}")
            expected["resources"][0].pop(key)
        self.assertEqual(carried, expected)
        self.assertEqual(carried["resources"][0]["schema"]["primaryKey"], ["code"])
        self.assertEqual(
            carried["resources"][0]["schema"]["fields"][0]["constraints"]["pattern"], "^[0-9]{8}$"
        )
        self.assertEqual(carried["licenses"], [{"name": "CC0-1.0"}])

    # ───────────────────────────────────────────── 2. the data does not move

    def test_stamping_leaves_every_data_byte_where_it_was(self) -> None:
        descriptor, parquet = build_dataset(self.root, rows=200_000)
        before = data_section(parquet)
        self.assertOutcome(
            run_cli("stamp", "--descriptor", str(descriptor), "--parquet", str(parquet)), EXIT_OK
        )
        after = data_section(parquet)
        self.assertEqual(
            len(before), len(after), "the stamped file's data section changed length"
        )
        self.assertEqual(before, after, "the stamp moved a data byte")
        self.assertGreater(len(before), 100_000, "the fixture is too small to be evidence")

    def test_a_stamp_that_moved_the_data_is_refused(self) -> None:
        """The guard above is only worth having if it fires. This is it firing."""
        descriptor, parquet = build_dataset(self.root)
        original = parquet.read_bytes()
        honest = sd.stamp_parquet

        def rewrites_the_rows(con, source: Path, destination: Path, text: str) -> None:
            con.execute(
                f"COPY (SELECT code, label || ' tampered' AS label FROM read_parquet('{source}')) "
                f"TO '{destination}' (FORMAT parquet, COMPRESSION zstd, KV_METADATA {{'x': 'y'}})"
            )

        with mock.patch.object(sd, "stamp_parquet", rewrites_the_rows):
            code = sd.main(["stamp", "--descriptor", str(descriptor), "--parquet", str(parquet)])
        self.assertEqual(code, EXIT_ERROR)
        self.assertEqual(parquet.read_bytes(), original, "a refused stamp must leave the file alone")
        self.assertIs(sd.stamp_parquet, honest)

    def test_stamping_twice_changes_nothing(self) -> None:
        """Every Protocol runs this step on every run; a churning stamp would move
        `bytes` and `hash` on runs where no datum did."""
        descriptor, parquet = build_dataset(self.root)
        self.assertOutcome(
            run_cli("stamp", "--descriptor", str(descriptor), "--parquet", str(parquet)), EXIT_OK
        )
        once, once_descriptor = parquet.read_bytes(), descriptor.read_text(encoding="utf-8")
        self.assertOutcome(
            run_cli("stamp", "--descriptor", str(descriptor), "--parquet", str(parquet)), EXIT_OK
        )
        self.assertEqual(parquet.read_bytes(), once, "a second stamp moved the object")
        self.assertEqual(descriptor.read_text(encoding="utf-8"), once_descriptor)

    # ────────────────────────────────────── 3. the declared size is re-measured

    def test_stamp_rewrites_the_declared_bytes_and_hash(self) -> None:
        descriptor, parquet = build_dataset(self.root)
        stale = json.loads(descriptor.read_text(encoding="utf-8"))["resources"][0]
        self.assertOutcome(
            run_cli("stamp", "--descriptor", str(descriptor), "--parquet", str(parquet)), EXIT_OK
        )
        resource = json.loads(descriptor.read_text(encoding="utf-8"))["resources"][0]
        size, digest = sd.file_measurement(parquet)
        self.assertEqual(resource["bytes"], size)
        self.assertEqual(resource["hash"], digest)
        self.assertNotEqual(resource["bytes"], stale["bytes"])
        self.assertNotEqual(resource["hash"], stale["hash"])

    def test_keep_declared_bytes_leaves_the_descriptor_alone(self) -> None:
        descriptor, parquet = build_dataset(self.root)
        before = descriptor.read_text(encoding="utf-8")
        self.assertOutcome(
            run_cli(
                "stamp",
                "--descriptor",
                str(descriptor),
                "--parquet",
                str(parquet),
                "--keep-declared-bytes",
            ),
            EXIT_OK,
        )
        self.assertEqual(descriptor.read_text(encoding="utf-8"), before)

    # ──────────────────────────────── 4. one URL, one ranged request, nothing else

    def test_reading_over_http_asks_for_the_object_and_nothing_else(self) -> None:
        descriptor, parquet = build_dataset(self.root, rows=200_000)
        self.assertOutcome(
            run_cli("stamp", "--descriptor", str(descriptor), "--parquet", str(parquet)), EXIT_OK
        )
        body = parquet.read_bytes()
        with ObjectServer(body) as server:
            result = run_cli("read", server.url)
            self.assertOutcome(result, EXIT_OK)
            carried = json.loads(result.stdout)
            paths = {path for _, path, _ in server.requests}
        self.assertEqual(carried["name"], "widgets")
        self.assertEqual(
            paths,
            {"/object.parquet"},
            f"the read touched something other than the object: {sorted(paths)}",
        )
        self.assertLess(
            server.served,
            len(body) // 4,
            f"served {server.served:,} of {len(body):,} bytes — a download, not a range read",
        )

    def test_reading_an_object_that_describes_nothing_is_a_verdict_not_a_crash(self) -> None:
        _, parquet = build_dataset(self.root)
        self.assertOutcome(
            run_cli("read", str(parquet)), EXIT_DISAGREEMENT, "does not describe itself"
        )

    def test_reading_a_footer_that_is_not_json_is_refused(self) -> None:
        _, parquet = build_dataset(self.root)
        con = sd.connect()
        stamped = parquet.with_suffix(".stamped")
        sd.stamp_parquet(con, parquet, stamped, "not json at all")
        os.replace(stamped, parquet)
        self.assertOutcome(run_cli("read", str(parquet)), EXIT_DISAGREEMENT, "is not JSON")

    def test_reading_a_missing_object_is_an_error_not_a_verdict(self) -> None:
        self.assertOutcome(
            run_cli("read", str(self.root / "absent.parquet")), EXIT_ERROR, "cannot read the footer"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
