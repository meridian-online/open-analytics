#!/usr/bin/env python3
"""Self-test for check_engine_version.py — proves the gate can fail, and on what.

Every case builds a scratch dataset tree (a real Parquet file, written by the DuckDB
this job actually installed, and a real `datapackage.json` beside it), runs the
checker as a subprocess, and asserts BOTH the exit status AND the message.

The disagreeing case does not merely assert a fabricated string; it patches the
`created_by` field inside a real footer that DuckDB itself wrote, in place, at the
byte level — the same technique a version bump uses in production, since
`created_by` is a fixed-width field for any `vX.Y.Z (build <10 hex chars>)` string.
Reading it back with `parquet_file_metadata()` afterwards proves the patched footer
is what the checker actually sees, not a string asserted in this file's memory.

A rule this suite does not cover: comment out the `written != pin` comparison in
`check_resources()` (or replace it with `False`), rerun `test_mismatch_reddens_...`,
and it fails.
"""

from __future__ import annotations

import json
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

CHECKER = Path(__file__).with_name("check_engine_version.py")
DESCRIPTOR_CHECKER = Path(__file__).with_name("check_descriptors.py")

# The words `check_descriptors.py` prints when the DATA is wrong. This gate must never
# print them: a writer/reader engine skew is not a claim about the data, and telling a
# reader it is sends them to fix bytes that are correct. Pinned against that file's own
# source by `test_the_data_defect_vocabulary_is_real`, because a phrase this file
# invents cannot fail to be absent.
DATA_DEFECT_VOCABULARY = "between descriptor and data"

EXIT_OK = 0
EXIT_MISMATCH = 1
EXIT_ERROR = 2


def write_parquet(target: Path, select_sql: str) -> None:
    import duckdb

    target.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"COPY ({select_sql}) TO '{target}' (FORMAT parquet)")
    con.close()


def real_created_by(target: Path) -> str:
    import duckdb

    con = duckdb.connect()
    row = con.execute("SELECT created_by FROM parquet_file_metadata(?)", [str(target)]).fetchone()
    con.close()
    return row[0]


def patch_created_by_to_disagree(target: Path) -> tuple[str, str]:
    """Rewrite the real `created_by` in `target`'s footer to a different version,
    at the same byte length, so no other offset in the footer has to move.

    Returns (original_created_by, patched_created_by).
    """
    original = real_created_by(target)
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", original)
    if match is None:
        raise AssertionError(f"fixture created_by {original!r} does not carry a version to patch")
    patch_digits = match.group(3)
    # Rotate every digit by one — guaranteed different, guaranteed the same length.
    rotated = "".join(str((int(digit) + 1) % 10) for digit in patch_digits)
    patched = original[: match.start(3)] + rotated + original[match.end(3) :]
    assert len(patched) == len(original), "patch changed the footer's byte length"
    assert patched != original

    raw = target.read_bytes()
    old_bytes = original.encode("utf-8")
    new_bytes = patched.encode("utf-8")
    assert raw.count(old_bytes) == 1, f"expected exactly one occurrence of {original!r} in the footer"
    target.write_bytes(raw.replace(old_bytes, new_bytes, 1))
    return original, patched


def write_package(root: Path, slug: str) -> Path:
    package_dir = root / slug
    parquet = package_dir / f"{slug}.parquet"
    write_parquet(parquet, "SELECT i AS n FROM range(3) t(i)")
    descriptor = package_dir / "datapackage.json"
    descriptor.write_text(
        json.dumps(
            {
                "name": slug,
                "resources": [
                    {
                        "name": slug,
                        "format": "parquet",
                        "path": parquet.name,
                        "schema": {"fields": [{"name": "n", "type": "integer"}]},
                    }
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return descriptor


class ObjectHandler(BaseHTTPRequestHandler):
    """Serves one Parquet over http:// so a resource path is genuinely REMOTE.

    Range-aware, because that is how DuckDB reads a footer — a handler that ignored
    `Range` would still satisfy the read and would quietly turn a footer read into a
    whole-object download, which is the property the live check depends on.
    """

    def log_message(self, *args: Any) -> None:
        pass

    def _respond(self, body_wanted: bool) -> None:
        if self.path != self.server.object_path:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = self.server.body
        match = re.match(r"bytes=(\d+)-(\d*)", self.headers.get("Range") or "")
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
                self.wfile.write(chunk)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        if body_wanted:
            self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler naming
        self._respond(True)

    def do_HEAD(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler naming
        self._respond(False)


class ObjectServer(TCPServer):
    allow_reuse_address = True

    def __init__(self, body: bytes, object_path: str = "/object.parquet") -> None:
        super().__init__(("127.0.0.1", 0), ObjectHandler)
        self.body = body
        self.object_path = object_path

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}{self.object_path}"


def serve(body: bytes) -> ObjectServer:
    server = ObjectServer(body)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def run_check(datasets_dir: Path, *extra: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    import os

    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, str(CHECKER), "--datasets-dir", str(datasets_dir), *extra],
        capture_output=True,
        text=True,
        check=False,
        env=full_env,
    )


class EngineVersionCheckSelfTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="engine-version-check-")
        self.addCleanup(self._tmp.cleanup)
        self.datasets = Path(self._tmp.name) / "datasets"
        self.datasets.mkdir(parents=True)

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

    def test_footer_agreeing_with_the_pin_passes(self) -> None:
        write_package(self.datasets, "widgets")
        # No DUCKDB_VERSION override: the pin falls back to the running duckdb, which
        # is exactly the engine that wrote this fixture's footer.
        self.assertOutcome(run_check(self.datasets, env={"DUCKDB_VERSION": ""}), EXIT_OK, "were written by")

    def test_mismatch_reddens_and_names_engine_mismatch_not_data(self) -> None:
        descriptor = write_package(self.datasets, "widgets")
        parquet = descriptor.parent / "widgets.parquet"
        original, patched = patch_created_by_to_disagree(parquet)
        original_version = re.search(r"(\d+\.\d+\.\d+)", original).group(1)
        patched_version = re.search(r"(\d+\.\d+\.\d+)", patched).group(1)

        # The pin is whatever actually wrote the fixture (original_version); the
        # footer now claims patched_version. This is the disagreement.
        result = run_check(self.datasets, env={"DUCKDB_VERSION": original_version})
        self.assertOutcome(
            result,
            EXIT_MISMATCH,
            "WRITER/READER ENGINE MISMATCH",
            "not a data disagreement",
            patched_version,
            original_version,
        )
        # EACH VERSION AGAINST ITS OWN LABEL, not merely present somewhere in the
        # output. Swapping `writer_version` and `reader_pin` in the Mismatch
        # construction was caught only by the JSON report; all three tests that read
        # the human message used `assertIn` on both strings independently, so a report
        # saying "written by <the pin>; pinned to <the writer>" satisfied every one of
        # them. That report sends a reader to change the wrong thing.
        self.assertIn(f"was written by DuckDB {patched_version}", result.stdout)
        self.assertIn(f"this check is pinned to DuckDB {original_version}", result.stdout)
        # The point of AC3b: this vocabulary, not the one `check_descriptors.py` uses
        # for an actual data defect.
        #
        # THE ASSERTED STRING IS TAKEN FROM THAT TOOL RATHER THAN PARAPHRASED. It
        # prints "N disagreement(s) between descriptor and data"; the earlier version
        # of this line asserted the absence of "disagreement between descriptor and
        # data" — the singular, without the `(s)` — which no output of either tool can
        # contain. A negative assertion that can never match is green against every
        # possible headline, including the data-defect vocabulary it exists to forbid.
        # `DATA_DEFECT_VOCABULARY` is asserted below to be a real substring of the
        # sibling tool's own source, so a rewording there reddens this rather than
        # silently emptying it again.
        self.assertNotIn(DATA_DEFECT_VOCABULARY, result.stdout + result.stderr)

    def test_a_remote_resource_is_read_rather_than_skipped(self) -> None:
        """The live check reads ONLY remote objects, and every other case in this file
        is a local file — so until this one existed, `if is_remote: continue` passed
        all seven and took the real gate to exit 0 having read no footers at all.

        The object is served from the loopback, so this needs no host: what is under
        test is that the remote arm is entered and its footer parsed, not that any
        particular origin answers.
        """
        local = self.datasets / "widgets" / "widgets.parquet"
        write_parquet(local, "SELECT i AS n FROM range(3) t(i)")
        server = serve(local.read_bytes())
        self.addCleanup(server.shutdown)
        package_dir = self.datasets / "remote_widgets"
        package_dir.mkdir(parents=True)
        (package_dir / "datapackage.json").write_text(
            json.dumps(
                {
                    "name": "remote_widgets",
                    "resources": [
                        {
                            "name": "remote_widgets",
                            "format": "parquet",
                            "path": server.url,
                            "schema": {"fields": [{"name": "n", "type": "integer"}]},
                        }
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        result = run_check(
            self.datasets, "--only", "remote_widgets", env={"DUCKDB_VERSION": "0.0.0"}
        )
        # It must REDDEN, which it can only do having actually read the footer over
        # http. A skip would exit 0, and so would a skip dressed as a pass.
        self.assertOutcome(result, EXIT_MISMATCH, "WRITER/READER ENGINE MISMATCH", "0.0.0")
        self.assertIn("127.0.0.1", result.stdout + result.stderr)

    def test_a_footer_it_cannot_read_is_an_error_not_a_pass(self) -> None:
        """A remote object that is not a Parquet must redden, not be shrugged off.

        This is NOT the zero-count guard — the read raises before anything is counted,
        which is why that guard needs its own test below. Both exist because they fail
        differently: this one is an object that answered wrongly, and that one is a
        loop that declined to ask.
        """
        server = serve(b"not a parquet at all")
        self.addCleanup(server.shutdown)
        package_dir = self.datasets / "unreadable"
        package_dir.mkdir(parents=True)
        (package_dir / "datapackage.json").write_text(
            json.dumps(
                {
                    "name": "unreadable",
                    "resources": [
                        {
                            "name": "unreadable",
                            "format": "parquet",
                            "path": server.url,
                            "schema": {"fields": [{"name": "n", "type": "integer"}]},
                        }
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        result = run_check(self.datasets, "--only", "unreadable")
        self.assertNotEqual(
            result.returncode, EXIT_OK, f"a footer it could not read must not pass:\n{result.stdout}{result.stderr}"
        )

    def test_a_healthy_remote_beside_a_broken_one_cannot_pass(self) -> None:
        """The case the previous round's fix did not cover, and the reason the
        invariant is `checked == declared` rather than `checked > 0`.

        Wrapping the remote footer read in `try/except CheckError: continue` — a
        plausible "do not let one flaky object kill the whole run" change — used to
        leave every test green while producing a real false pass: a tree with one
        healthy remote and one broken one printed a success line naming both packages
        and exited 0, having never read the broken one. Every other remote case in
        this file has a single resource, so the zero-count guard masked it there.

        This run has two, and it must not exit 0 by any route.
        """
        local = self.datasets / "widgets" / "widgets.parquet"
        write_parquet(local, "SELECT i AS n FROM range(3) t(i)")
        good = serve(local.read_bytes())
        self.addCleanup(good.shutdown)
        bad = serve(b"not a parquet at all")
        self.addCleanup(bad.shutdown)

        for slug, url in (("good_pkg", good.url), ("bad_pkg", bad.url)):
            package_dir = self.datasets / slug
            package_dir.mkdir(parents=True)
            (package_dir / "datapackage.json").write_text(
                json.dumps(
                    {
                        "name": slug,
                        "resources": [
                            {
                                "name": slug,
                                "format": "parquet",
                                "path": url,
                                "schema": {"fields": [{"name": "n", "type": "integer"}]},
                            }
                        ],
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

        result = run_check(self.datasets)
        output = result.stdout + result.stderr
        self.assertNotEqual(
            result.returncode,
            EXIT_OK,
            f"a run holding one unreadable resource must not report success:\n{output}",
        )
        # And it must not claim to have checked what it did not. The mutation's
        # signature was a success line counting only the resources it managed to read.
        self.assertNotIn("were written by the DuckDB this check is pinned to", output)
        # THE MESSAGE HAS TO BE THE READ FAILURE, not the count backstop. Both exit
        # non-zero, so an assertion on the status alone cannot tell "this object is not
        # a Parquet" from "something skipped it and the backstop noticed" — and those
        # send a reader to two different places. Asserting the wording is what makes
        # the `try/except … continue` mutation redden HERE rather than incidentally.
        self.assertIn("bad_pkg", output)
        self.assertNotIn("declared resource footer(s)", output)

    def test_a_missing_local_file_is_named_rather_than_skipped(self) -> None:
        """The same defect class on the other branch of the resolver.

        `resolve_path` sends a relative path down a local arm, and turning THAT read
        into a silent skip is the same one-line change as on the remote arm. Nothing
        drove it: every other local case in this file points at a file that exists.

        Two packages so a skip cannot be confused with an empty run, and the assertion
        is on the WORDING — under a skip the backstop still refuses, but it refuses
        with a count rather than with the path, and a reader needs the path.
        """
        write_package(self.datasets, "aaa_present")
        package_dir = self.datasets / "zzz_absent"
        package_dir.mkdir(parents=True)
        (package_dir / "datapackage.json").write_text(
            json.dumps(
                {
                    "name": "zzz_absent",
                    "resources": [
                        {
                            "name": "zzz_absent",
                            "format": "parquet",
                            "path": "nothing_here.parquet",
                            "schema": {"fields": [{"name": "n", "type": "integer"}]},
                        }
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        result = run_check(self.datasets)
        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, EXIT_OK, output)
        self.assertIn("nothing_here.parquet", output)
        self.assertIn("does not exist", output)
        self.assertNotIn("declared resource footer(s)", output)

    def test_the_backstop_is_actually_wired_into_the_loop(self) -> None:
        """A source-text assertion, and it is second-best on purpose.

        `every_declared_resource_was_read` is unreachable in the current code — every
        resource is read or raises — so no behavioural test can notice its CALL being
        deleted, only its body. That is the gap: the function has cover and the wiring
        does not, and deleting one line from `check_resources` restored the exact false
        pass this whole guard exists to prevent while every other test stayed green.

        Reading the source is a weaker check than driving the behaviour and can be
        fooled by indirection, which is why it says so here rather than pretending
        otherwise. It is chosen over nothing, not over something better: the thing that
        would replace it is a reachable code path, and there is none to build until a
        resource can legitimately be skipped.
        """
        source = CHECKER.read_text(encoding="utf-8")
        body_at = source.index("def check_resources(")
        body = source[body_at:]
        self.assertIn(
            "every_declared_resource_was_read(checked, declared,",
            body,
            "check_resources must call the backstop; deleting the call is invisible to "
            "every behavioural test in this file",
        )

    def test_the_read_count_must_equal_the_declared_count(self) -> None:
        """The backstop itself, driven directly.

        It is meant to be unreachable: every resource in `check_resources` today is
        either read or raises, so nothing in the current code can make the two
        disagree. That is exactly why it needs a test of its own — an inline comparison
        has nothing to redden when it is deleted, and that is how the `checked > 0`
        version it replaced went untested through a whole round.
        """
        sys.path.insert(0, str(CHECKER.parent))
        import check_engine_version as cev  # noqa: PLC0415

        # Agreement is silence, including the empty case.
        cev.every_declared_resource_was_read(0, 0, 0)
        cev.every_declared_resource_was_read(4, 4, 4)

        # A partial read is the case the review found and is refused by name.
        with self.assertRaises(cev.CheckError) as caught:
            cev.every_declared_resource_was_read(1, 2, 2)
        self.assertIn("read 1 of 2", str(caught.exception))
        self.assertIn("skipped is not a resource that agreed", str(caught.exception))

        # And the total shutout, which is the case the earlier version caught.
        with self.assertRaises(cev.CheckError):
            cev.every_declared_resource_was_read(0, 4, 4)

    def test_the_count_invariant_holds_when_nothing_is_declared(self) -> None:
        """`check_resources` compares what it READ against what was DECLARED, so the
        empty case agrees with itself and does not raise.

        Stated rather than assumed, because the earlier invariant was `checked > 0` and
        this input was how it was driven. Nothing reaches `check_resources` with an
        empty list through the CLI — `discover()` raises on an empty tree — so the
        boundary is recorded here rather than left for someone to rediscover as a bug.
        """
        sys.path.insert(0, str(CHECKER.parent))
        import check_engine_version as cev  # noqa: PLC0415
        import duckdb  # noqa: PLC0415

        con = duckdb.connect()
        self.addCleanup(con.close)
        self.assertEqual(cev.check_resources(con, [], "1.5.5"), ([], 0))

    def test_every_descriptor_is_visited_not_just_the_first(self) -> None:
        """Truncating the descriptor loop to its first entry passed the whole suite.

        The live tree's first descriptor sorts to a dataset that agrees, so a
        truncated loop would report success across "1 resource(s) across 4
        descriptor(s)" and nothing enforced the discrepancy. Here the AGREEING package
        sorts first and the disagreeing one second, so a loop that stops early exits 0
        and this reddens.
        """
        write_package(self.datasets, "aaa_agrees")
        descriptor = write_package(self.datasets, "zzz_disagrees")
        parquet = descriptor.parent / "zzz_disagrees.parquet"
        original, patched = patch_created_by_to_disagree(parquet)
        original_version = re.search(r"(\d+\.\d+\.\d+)", original).group(1)

        result = run_check(self.datasets, env={"DUCKDB_VERSION": original_version})
        # The reddening is the first half: a loop that stopped after `aaa_agrees`
        # exits 0 here, because the only disagreement is in the package it never
        # reached.
        self.assertOutcome(result, EXIT_MISMATCH, "zzz_disagrees")
        # And the COUNT is the enforcement rather than the prose. The failure summary
        # names how many resources were read and which packages they came from; a
        # truncated loop reports one and names one.
        self.assertIn("across 2 resource(s)", result.stdout + result.stderr)
        self.assertIn("aaa_agrees, zzz_disagrees", result.stdout + result.stderr)

        # The same count on the PASS path, which is the branch a truncated loop would
        # actually reach in production — the live tree's first descriptor agrees, so
        # the gate would print success over one of four and nothing would object.
        original_pin = re.search(r"(\d+\.\d+\.\d+)", real_created_by(self.datasets / "aaa_agrees" / "aaa_agrees.parquet")).group(1)
        parquet.write_bytes(parquet.read_bytes().replace(patched.encode(), original.encode(), 1))
        agreeing = run_check(self.datasets, env={"DUCKDB_VERSION": original_pin})
        self.assertOutcome(agreeing, EXIT_OK, "2 resource(s) across 2 descriptor(s)")

    def test_the_data_defect_vocabulary_is_real(self) -> None:
        """`DATA_DEFECT_VOCABULARY` must be a substring of `check_descriptors.py`.

        Without this, the negative assertion in the mismatch test is a phrase this
        file made up, and a made-up phrase is guaranteed absent from every output —
        green whatever the gate prints, which is exactly how the earlier singular
        spelling ("disagreement between descriptor and data") became vacuous.
        """
        self.assertIn(
            DATA_DEFECT_VOCABULARY,
            DESCRIPTOR_CHECKER.read_text(encoding="utf-8"),
            "the sibling tool no longer prints this phrase, so the negative assertion "
            "that guards against it is now vacuous — take the new wording from that file",
        )

    def test_mismatch_still_reddens_when_the_pin_itself_differs_from_the_footer(self) -> None:
        """The same disagreement, produced the other way: an untouched footer held
        to a pin nothing on disk wrote. Exercises `reader_pin()` reading
        `DUCKDB_VERSION` rather than the byte-patch path above.
        """
        write_package(self.datasets, "widgets")
        self.assertOutcome(
            run_check(self.datasets, env={"DUCKDB_VERSION": "0.0.0"}),
            EXIT_MISMATCH,
            "WRITER/READER ENGINE MISMATCH",
            "0.0.0",
        )

    def test_footer_with_no_created_by_is_an_error_not_a_mismatch(self) -> None:
        descriptor = write_package(self.datasets, "widgets")
        parquet = descriptor.parent / "widgets.parquet"
        original = real_created_by(parquet)
        blanked = " " * len(original)
        raw = parquet.read_bytes()
        old_bytes = original.encode("utf-8")
        new_bytes = blanked.encode("utf-8")
        self.assertEqual(raw.count(old_bytes), 1)
        parquet.write_bytes(raw.replace(old_bytes, new_bytes, 1))

        self.assertOutcome(run_check(self.datasets), EXIT_ERROR, "does not name a DuckDB version")

    def test_only_narrows_to_the_dataset_it_names(self) -> None:
        write_package(self.datasets, "widgets")
        descriptor = write_package(self.datasets, "gadgets")
        original, _patched = patch_created_by_to_disagree(descriptor.parent / "gadgets.parquet")
        version = re.search(r"(\d+\.\d+\.\d+)", original).group(1)

        self.assertOutcome(run_check(self.datasets, "--only", "widgets", env={"DUCKDB_VERSION": version}), EXIT_OK)
        self.assertOutcome(
            run_check(self.datasets, "--only", "gadgets", env={"DUCKDB_VERSION": version}), EXIT_MISMATCH
        )

    def test_json_report_names_the_pin_and_the_mismatch(self) -> None:
        descriptor = write_package(self.datasets, "widgets")
        patch_created_by_to_disagree(descriptor.parent / "widgets.parquet")
        out = Path(self._tmp.name) / "report.json"

        result = run_check(self.datasets, "--json", str(out), env={"DUCKDB_VERSION": "0.0.0"})
        self.assertEqual(result.returncode, EXIT_MISMATCH)
        report = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(report["reader_pin"], "0.0.0")
        self.assertEqual(len(report["mismatches"]), 1)
        self.assertEqual(report["mismatches"][0]["reader_pin"], "0.0.0")

    def test_no_datasets_is_an_error(self) -> None:
        self.assertOutcome(run_check(self.datasets), EXIT_ERROR, "no datasets/*/datapackage.json")


if __name__ == "__main__":
    unittest.main(verbosity=2)
