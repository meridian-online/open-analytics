#!/usr/bin/env python3
"""Self-test for brightfield_arc_pin.py: proves each subcommand can refuse, and on what.

Every case runs the script as a subprocess on a scratch Cargo.lock or a scratch copy of
the loader crate, and asserts both the exit status and the message. The retarget cases
run on a copy of the real `scripts/arc_manifest_load/Cargo.toml`, so a reshaped
dependency line there turns these red rather than the gate job at run time.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("brightfield_arc_pin.py")
CRATE = Path(__file__).with_name("arc_manifest_load")

A = "f5f3f071bd10c04a20aafb4619f637c61127760a"
B = "d2ef25df67a81dcab3414c22cca2654e156ff734"


def lock_entry(name: str, rev: str) -> str:
    return (
        f'[[package]]\nname = "{name}"\nversion = "0.2.0"\n'
        f'source = "git+https://github.com/meridian-online/arcform?rev={rev}#{rev}"\n\n'
    )


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True, check=False
    )


class Rev(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        self.lock = self.dir / "Cargo.lock"

    def test_one_rev_across_arcform_crates_is_printed(self) -> None:
        self.lock.write_text(
            '[[package]]\nname = "anyhow"\nversion = "1.0.0"\n'
            'source = "registry+https://github.com/rust-lang/crates.io-index"\n\n'
            + lock_entry("arcform", A)
            + lock_entry("arcform-sqlparser", A)
        )
        result = run("rev", str(self.lock))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), A)

    def test_no_arcform_source_is_refused(self) -> None:
        self.lock.write_text(
            '[[package]]\nname = "anyhow"\nversion = "1.0.0"\n'
            'source = "registry+https://github.com/rust-lang/crates.io-index"\n'
        )
        result = run("rev", str(self.lock))
        self.assertEqual(result.returncode, 2)
        self.assertIn("names no arcform source", result.stderr)

    def test_two_revs_are_refused_and_both_named(self) -> None:
        self.lock.write_text(lock_entry("arcform", A) + lock_entry("arcform-sqlparser", B))
        result = run("rev", str(self.lock))
        self.assertEqual(result.returncode, 2)
        self.assertIn(A, result.stderr)
        self.assertIn(B, result.stderr)

    def test_a_short_rev_is_refused(self) -> None:
        self.lock.write_text(lock_entry("arcform", "f5f3f07"))
        result = run("rev", str(self.lock))
        self.assertEqual(result.returncode, 2)
        self.assertIn("names no arcform source", result.stderr)

    def test_an_html_error_page_is_refused(self) -> None:
        self.lock.write_text("<html><body>404: Not Found</body></html>\n")
        result = run("rev", str(self.lock))
        self.assertEqual(result.returncode, 2)


class Retarget(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        self.toml = self.dir / "Cargo.toml"
        shutil.copy(CRATE / "Cargo.toml", self.toml)

    def test_the_real_manifest_is_moved_to_the_new_rev(self) -> None:
        before = self.toml.read_text()
        self.assertIn(f'rev = "{B}"', before, "the loader's pin moved; update B here")
        result = run("retarget", str(self.toml), A)
        self.assertEqual(result.returncode, 0, result.stderr)
        after = self.toml.read_text()
        self.assertIn(f'rev = "{A}"', after)
        self.assertNotIn(B, after.split("[dependencies]", 1)[1])
        # Only the rev changed: the feature set Brightfield's loader shares stays.
        self.assertEqual(after.replace(A, B), before)

    def test_a_manifest_with_no_arc_dependency_line_is_refused(self) -> None:
        self.toml.write_text('[package]\nname = "x"\n\n[dependencies]\nanyhow = "1"\n')
        result = run("retarget", str(self.toml), A)
        self.assertEqual(result.returncode, 2)
        self.assertIn("one arc dependency line", result.stderr)

    def test_a_rev_that_is_not_a_full_commit_is_refused(self) -> None:
        result = run("retarget", str(self.toml), "main")
        self.assertEqual(result.returncode, 2)
        self.assertIn("40-character commit", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
