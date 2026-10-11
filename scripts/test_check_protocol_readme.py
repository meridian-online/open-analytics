#!/usr/bin/env python3
# /// script
# requires-python = "==3.12.*"
# dependencies = ["pyyaml==6.0.3"]
# ///
"""Self-test for check_protocol_readme.py — proves the check can fail, and on what.

Every case builds a scratch `datasets/` tree (a real `arcform.yaml` and a real
`README.md` beside it), runs the check as a subprocess, and asserts BOTH the exit
status AND the message. Status alone cannot tell a refusal from a crash, which is why
the check distinguishes 1 (the README and the manifest disagree) from 2 (something
could not be read at all) and why every case below pins the number it expects.

The mutations are chosen at the altitude the drift actually happens, not the altitude
the check is written at:

  * a step RENAMED, ADDED and REMOVED — the step list moving under the README;
  * a step whose KIND changed from `op:` to `command:` — the case that really occurred,
    where the README kept asserting no shell steps while the manifest grew three. The
    case asserts the generated sentence NAMES the new shell step, not merely that
    something changed, because a diff that reddens for the wrong reason is a pin on
    nothing;
  * an operator VERSION bumped `@1` → `@2` with the name and kind unchanged — a
    different step wearing the same name;
  * a `command:` step's command line EDITED by one character, on its first line and on
    a later line of a multi-line one, with the step name and kind unchanged — the line is
    never printed in the README, so the row's fingerprint is the only thing that moves.
    The cases assert WHICH row moved and what its fingerprint became, computed here from
    the command string rather than read back from the checker;
  * a manifest whose only `command:` is inside a COMMENT — the check must NOT report a
    shell step, which is what separates parsing the document from grepping the file.
    `grep -c 'command:' datasets/edgar_gleif/arcform.yaml` is 4 with no `command:` step
    in it, and a criterion built on that grep is why this case exists;
  * a README with NO block, with TWO blocks, and a manifest with no README at all —
    each a refusal rather than a skip, because a check that quietly examines nothing
    passes loudest exactly when a Protocol ships undocumented;
  * an empty `datasets/` directory — the vacuous pass, refused by name.

The count of manifests examined is asserted in the cases that carry more than one
dataset. Without it a check that opened the first manifest and silently gave up on the
rest would be indistinguishable from one that read them all.

Nothing here touches the network or the real `datasets/` tree.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

CHECK = Path(__file__).with_name("check_protocol_readme.py")

EXIT_OK = 0
EXIT_DISAGREEMENT = 1
EXIT_ERROR = 2

OPEN_MARKER = (
    "<!-- protocol-steps: generated from arcform.yaml by "
    "scripts/check_protocol_readme.py — do not edit this block by hand -->"
)
CLOSE_MARKER = "<!-- /protocol-steps -->"

ALL_OPS = textwrap.dedent(
    """\
    # A manifest whose comments mention command: on purpose — see the module docstring.
    name: alpha
    engine: duckdb
    steps:
      - name: fetch_alpha
        op: http_fetch@1
        with:
          url: https://example.invalid/alpha.parquet
          out: build/alpha.parquet
      - name: load
        sql: models/load.sql
      # This step replaced a `command:` step, and this comment says so.
      - name: describe
        op: datapackage_describe@1
        with:
          parquet: build/alpha.parquet
          out: datapackage.json
    """
)


FETCH_COMMAND = "curl -o build/beta.zip https://example.invalid/beta.zip"
# A YAML `|` block scalar: the string is these lines joined by newlines plus a trailing one.
STAMP_LINES = ("python3 stamp.py --descriptor datapackage.json", "echo done")
STAMP_COMMAND = "\n".join(STAMP_LINES) + "\n"

TWO_SHELLS = (
    "# The command lines below are long on purpose; the README must not print them.\n"
    "name: beta\n"
    "steps:\n"
    "  - name: fetch\n"
    f'    command: "{FETCH_COMMAND}"\n'
    "  - name: load\n"
    "    sql: models/load.sql\n"
    "  - name: stamp\n"
    "    command: |\n" + "".join(f"      {line}\n" for line in STAMP_LINES)
)


def fingerprint(command: str) -> str:
    """The cell the checker should print for `command`, worked out here from the string."""
    return f"`command:` (shell, sha256 `{hashlib.sha256(command.encode()).hexdigest()[:12]}`)"


def write_dataset(root: Path, slug: str, manifest: str, *, block: str | None) -> Path:
    """Lay down datasets/<slug>/{arcform.yaml, README.md} and return the directory.

    `block` is the README's copy of the step list, markers included; `None` writes a
    README with no block at all.
    """
    directory = root / slug
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "arcform.yaml").write_text(manifest, encoding="utf-8")
    body = f"# {slug}\n\nProse a human wrote.\n"
    if block is not None:
        body += "\n" + block + "\n"
    (directory / "README.md").write_text(body, encoding="utf-8")
    return directory


def run_check(datasets_dir: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECK), "--datasets-dir", str(datasets_dir), *extra],
        capture_output=True,
        text=True,
    )


def generated_block(datasets_dir: Path, slug: str) -> str:
    """The block the check itself writes for `slug` — the baseline every case mutates."""
    result = run_check(datasets_dir, "--write")
    assert result.returncode == EXIT_OK, result.stderr
    readme = (datasets_dir / slug / "README.md").read_text(encoding="utf-8")
    start = readme.index(OPEN_MARKER)
    end = readme.index(CLOSE_MARKER) + len(CLOSE_MARKER)
    return readme[start:end]


class ScratchTree(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="protocol-readme-selftest-")
        self.addCleanup(self._tmp.cleanup)
        self.datasets = Path(self._tmp.name) / "datasets"
        self.datasets.mkdir(parents=True)

    def settled(self, slug: str = "alpha", manifest: str = ALL_OPS) -> str:
        """A dataset whose README already agrees with its manifest. Returns the block."""
        write_dataset(self.datasets, slug, manifest, block=None)
        block = generated_block(self.datasets, slug)
        result = run_check(self.datasets)
        self.assertEqual(result.returncode, EXIT_OK, result.stderr)
        return block


class TheStepListMoves(ScratchTree):
    """The manifest changes and the README's copy does not."""

    def mutate_manifest(self, new_manifest: str, slug: str = "alpha") -> subprocess.CompletedProcess[str]:
        (self.datasets / slug / "arcform.yaml").write_text(new_manifest, encoding="utf-8")
        return run_check(self.datasets)

    def test_a_renamed_step_reddens_and_the_diff_names_both_spellings(self) -> None:
        self.settled()
        result = self.mutate_manifest(ALL_OPS.replace("name: fetch_alpha", "name: pull_alpha"))
        self.assertEqual(result.returncode, EXIT_DISAGREEMENT, result.stdout + result.stderr)
        self.assertIn("-| 1 | `fetch_alpha` |", result.stderr)
        self.assertIn("+| 1 | `pull_alpha` |", result.stderr)

    def test_an_added_step_reddens_and_the_count_in_the_sentence_moves(self) -> None:
        self.settled()
        result = self.mutate_manifest(
            ALL_OPS + "  - name: validate\n    op: finetype_validate@1\n"
        )
        self.assertEqual(result.returncode, EXIT_DISAGREEMENT, result.stdout + result.stderr)
        self.assertIn("+| 4 | `validate` | `op: finetype_validate@1` |", result.stderr)
        self.assertIn("-All 3 steps are", result.stderr)
        self.assertIn("+All 4 steps are", result.stderr)

    def test_a_removed_step_reddens(self) -> None:
        self.settled()
        trimmed = ALL_OPS[: ALL_OPS.index("  - name: load")]
        result = self.mutate_manifest(trimmed)
        self.assertEqual(result.returncode, EXIT_DISAGREEMENT, result.stdout + result.stderr)
        self.assertIn("-| 2 | `load` |", result.stderr)

    def test_an_operator_version_bump_reddens_though_the_name_and_kind_are_unchanged(self) -> None:
        """`parquet_export@1` and `@2` are not the same step, and the table says which."""
        self.settled()
        result = self.mutate_manifest(ALL_OPS.replace("http_fetch@1", "http_fetch@2"))
        self.assertEqual(result.returncode, EXIT_DISAGREEMENT, result.stdout + result.stderr)
        self.assertIn("-| 1 | `fetch_alpha` | `op: http_fetch@1` |", result.stderr)
        self.assertIn("+| 1 | `fetch_alpha` | `op: http_fetch@2` |", result.stderr)


class TheShellStepClaim(ScratchTree):
    """The sentence that was false for months, and the grep that could not see it."""

    def test_an_op_becoming_a_shell_step_reddens_and_the_new_sentence_names_it(self) -> None:
        self.settled()
        shelled = ALL_OPS.replace(
            "  - name: describe\n    op: datapackage_describe@1\n"
            "    with:\n      parquet: build/alpha.parquet\n"
            "      out: datapackage.json\n",
            '  - name: describe\n    command: "python3 describe.py"\n',
        )
        self.assertNotIn("op: datapackage_describe@1", shelled)
        (self.datasets / "alpha" / "arcform.yaml").write_text(shelled, encoding="utf-8")
        result = run_check(self.datasets)
        self.assertEqual(result.returncode, EXIT_DISAGREEMENT, result.stdout + result.stderr)
        # The absence claim must go, and what replaces it must NAME the step. A
        # reddening that merely said "something changed" would leave a README free to
        # keep asserting an absence in its own words.
        self.assertIn("-All 3 steps are", result.stderr)
        self.assertIn("This Protocol runs no opaque", result.stderr)
        self.assertIn("+3 steps, of which 1 runs through an opaque", result.stderr)
        self.assertIn("`describe`", result.stderr)
        self.assertIn(
            f"+| 3 | `describe` | {fingerprint('python3 describe.py')} |", result.stderr
        )

    def test_command_in_a_comment_is_not_a_shell_step(self) -> None:
        """The check parses the document; it does not grep the file.

        `ALL_OPS` carries `command:` twice in comments and has no `command:` step. A
        text scan reports shell steps here and this case reddens; the parser reports
        none and the generated sentence asserts the absence.
        """
        self.assertIn("`command:` step", ALL_OPS)
        block = self.settled()
        self.assertIn("This Protocol runs no opaque `command:`/shell step.", block)
        self.assertNotIn("(shell", block)
        self.assertNotIn("fingerprint", block)

    def test_the_sentence_names_every_shell_step_not_just_the_first(self) -> None:
        two_shells = textwrap.dedent(
            """\
            name: beta
            steps:
              - name: fetch_beta
                command: "curl -o build/beta.zip https://example.invalid/beta.zip"
              - name: load
                sql: models/load.sql
              - name: stamp
                command: "python3 stamp.py"
            """
        )
        block = self.settled(slug="beta", manifest=two_shells)
        self.assertIn(
            "3 steps, of which 2 run through an opaque `command:`/shell step: "
            "`fetch_beta`, `stamp`.",
            block,
        )


class TheCommandLineIsHeldToo(ScratchTree):
    """An edit to what a shell step runs reddens the check, though the line is never printed."""

    def edit(self, old: str, new: str) -> subprocess.CompletedProcess[str]:
        path = self.datasets / "beta" / "arcform.yaml"
        text = path.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, old)
        path.write_text(text.replace(old, new), encoding="utf-8")
        return run_check(self.datasets)

    def test_one_character_of_a_command_line_reddens_and_regenerating_clears_it(self) -> None:
        block = self.settled(slug="beta", manifest=TWO_SHELLS)
        self.assertIn(fingerprint(FETCH_COMMAND), block)
        # The reason the line is not rendered, and the thing the fingerprint replaces.
        self.assertNotIn("curl", block)
        self.assertNotIn(FETCH_COMMAND, block)

        edited = FETCH_COMMAND.replace("beta.zip https", "beta.zap https")
        self.assertNotEqual(edited, FETCH_COMMAND)
        result = self.edit(FETCH_COMMAND, edited)
        self.assertEqual(result.returncode, EXIT_DISAGREEMENT, result.stdout + result.stderr)
        self.assertIn(f"-| 1 | `fetch` | {fingerprint(FETCH_COMMAND)} |", result.stderr)
        self.assertIn(f"+| 1 | `fetch` | {fingerprint(edited)} |", result.stderr)
        self.assertNotIn("curl", result.stderr)

        repaired = run_check(self.datasets, "--write")
        self.assertEqual(repaired.returncode, EXIT_OK, repaired.stderr)
        self.assertIn("rewrote 1 block(s)", repaired.stdout)
        self.assertEqual(run_check(self.datasets).returncode, EXIT_OK)
        readme = (self.datasets / "beta" / "README.md").read_text(encoding="utf-8")
        self.assertIn(fingerprint(edited), readme)
        self.assertNotIn("curl", readme)

    def test_only_the_edited_steps_row_moves(self) -> None:
        """Two shell steps, one edit: the fingerprint is per step, not one for the manifest."""
        self.settled(slug="beta", manifest=TWO_SHELLS)
        result = self.edit("echo done", "echo dune")
        self.assertEqual(result.returncode, EXIT_DISAGREEMENT, result.stdout + result.stderr)
        self.assertIn(f"+| 3 | `stamp` | {fingerprint(STAMP_COMMAND.replace('done', 'dune'))} |", result.stderr)
        self.assertNotIn("-| 1 | `fetch`", result.stderr)
        self.assertNotIn("+| 1 | `fetch`", result.stderr)

    def test_an_edit_on_a_later_line_of_a_multi_line_command_reddens(self) -> None:
        """The whole string is fingerprinted, not its first line."""
        self.settled(slug="beta", manifest=TWO_SHELLS)
        result = self.edit("echo done", "echo dune")
        self.assertEqual(result.returncode, EXIT_DISAGREEMENT, result.stdout + result.stderr)
        self.assertIn(f"-| 3 | `stamp` | {fingerprint(STAMP_COMMAND)} |", result.stderr)

    def test_requoting_or_commenting_a_command_does_not_redden(self) -> None:
        """The command is read as the string YAML decodes it to, so only a changed string counts."""
        self.settled(slug="beta", manifest=TWO_SHELLS)
        result = self.edit(
            f'    command: "{FETCH_COMMAND}"\n',
            f"    # requoted, and commented\n    command: '{FETCH_COMMAND}'\n",
        )
        self.assertEqual(result.returncode, EXIT_OK, result.stdout + result.stderr)

    def test_a_command_with_no_string_to_fingerprint_is_refused(self) -> None:
        for label, value in (("empty", '""'), ("a list", "[python3, stamp.py]")):
            with self.subTest(label):
                write_dataset(
                    self.datasets,
                    "alpha",
                    f"name: alpha\nsteps:\n  - name: stamp\n    command: {value}\n",
                    block=None,
                )
                result = run_check(self.datasets, "--write")
                self.assertEqual(result.returncode, EXIT_ERROR, result.stdout + result.stderr)
                self.assertIn("declares `command:` with no value", result.stderr)


class TheCheckRefusesRatherThanSkips(ScratchTree):
    """Every shape where a lenient check would examine nothing and exit 0."""

    def test_a_readme_with_no_block_is_a_failure_not_a_skip(self) -> None:
        write_dataset(self.datasets, "alpha", ALL_OPS, block=None)
        result = run_check(self.datasets)
        self.assertEqual(result.returncode, EXIT_ERROR, result.stdout + result.stderr)
        self.assertIn("found 0 opening marker(s) and 0 closing marker(s)", result.stderr)

    def test_two_blocks_are_refused_rather_than_resolved_by_picking_one(self) -> None:
        block = self.settled()
        readme = self.datasets / "alpha" / "README.md"
        readme.write_text(
            readme.read_text(encoding="utf-8") + "\n" + block + "\n", encoding="utf-8"
        )
        result = run_check(self.datasets)
        self.assertEqual(result.returncode, EXIT_ERROR, result.stdout + result.stderr)
        self.assertIn("found 2 opening marker(s) and 2 closing marker(s)", result.stderr)

    def test_a_manifest_with_no_readme_beside_it_is_a_failure(self) -> None:
        self.settled()
        (self.datasets / "alpha" / "README.md").unlink()
        result = run_check(self.datasets)
        self.assertEqual(result.returncode, EXIT_ERROR, result.stdout + result.stderr)
        self.assertIn("has no README.md beside it", result.stderr)

    def test_an_empty_datasets_directory_is_a_failure_not_a_vacuous_pass(self) -> None:
        result = run_check(self.datasets)
        self.assertEqual(result.returncode, EXIT_ERROR, result.stdout + result.stderr)
        self.assertIn("nothing was checked", result.stderr)

    def test_a_manifest_declaring_neither_sql_nor_op_nor_command_is_refused(self) -> None:
        write_dataset(
            self.datasets,
            "alpha",
            "name: alpha\nsteps:\n  - name: nothing\n",
            block=None,
        )
        result = run_check(self.datasets, "--write")
        self.assertEqual(result.returncode, EXIT_ERROR, result.stdout + result.stderr)
        self.assertIn("declares none of sql:/op:/command:", result.stderr)


class EveryManifestIsExamined(ScratchTree):
    """A check that stopped after the first dataset would still exit 0 without this."""

    def three_datasets(self) -> None:
        for slug in ("alpha", "beta", "gamma"):
            write_dataset(
                self.datasets, slug, ALL_OPS.replace("name: alpha", f"name: {slug}"), block=None
            )
        result = run_check(self.datasets, "--write")
        self.assertEqual(result.returncode, EXIT_OK, result.stderr)

    def test_the_run_says_how_many_manifests_it_read(self) -> None:
        self.three_datasets()
        result = run_check(self.datasets)
        self.assertEqual(result.returncode, EXIT_OK, result.stdout + result.stderr)
        self.assertIn("examined 3 manifest(s) of 3 found", result.stdout)

    def test_a_faulty_dataset_does_not_stop_the_others_and_the_count_says_so(self) -> None:
        """The line that says this run was not vacuous, pinned on a tree that moves it.

        `examined N of M found` is only evidence if N and M can differ. They can:
        the dataset with no block is collected as a fault and the other two are still
        held to their manifests, so the count reads 2 of 3. A check that printed the
        number of manifests it FOUND rather than the number it READ would print 3 of
        3 here and this case reddens.
        """
        self.three_datasets()
        (self.datasets / "beta" / "README.md").write_text("# beta\n", encoding="utf-8")
        result = run_check(self.datasets)
        self.assertEqual(result.returncode, EXIT_ERROR, result.stdout + result.stderr)
        self.assertIn("examined 2 manifest(s) of 3 found", result.stdout)
        self.assertIn("1 of 3 manifest(s) could not be held to a README at all", result.stderr)
        self.assertIn("beta/README.md", result.stderr)
        self.assertNotIn("alpha/README.md", result.stderr)

    def test_every_faulty_dataset_is_named_not_just_the_first(self) -> None:
        self.three_datasets()
        for slug in ("alpha", "gamma"):
            (self.datasets / slug / "README.md").write_text(f"# {slug}\n", encoding="utf-8")
        result = run_check(self.datasets)
        self.assertEqual(result.returncode, EXIT_ERROR, result.stdout + result.stderr)
        self.assertIn("examined 1 manifest(s) of 3 found", result.stdout)
        self.assertIn("2 of 3 manifest(s) could not be held to a README at all", result.stderr)
        self.assertIn("alpha/README.md", result.stderr)
        self.assertIn("gamma/README.md", result.stderr)

    def test_a_disagreement_in_the_last_dataset_still_reddens(self) -> None:
        """The failure a first-dataset-only check would miss."""
        self.three_datasets()
        path = self.datasets / "gamma" / "arcform.yaml"
        path.write_text(
            path.read_text(encoding="utf-8").replace("name: load", "name: normalise"),
            encoding="utf-8",
        )
        result = run_check(self.datasets)
        self.assertEqual(result.returncode, EXIT_DISAGREEMENT, result.stdout + result.stderr)
        self.assertIn("gamma", result.stderr)
        self.assertIn("+| 2 | `normalise` |", result.stderr)
        self.assertNotIn("alpha/README.md: its step block", result.stderr)


class WriteRepairsWhatCheckReports(ScratchTree):
    def test_write_closes_a_real_disagreement_and_is_then_idempotent(self) -> None:
        self.settled()
        path = self.datasets / "alpha" / "arcform.yaml"
        path.write_text(
            path.read_text(encoding="utf-8").replace("name: fetch_alpha", "name: pull_alpha"),
            encoding="utf-8",
        )
        self.assertEqual(run_check(self.datasets).returncode, EXIT_DISAGREEMENT)

        repaired = run_check(self.datasets, "--write")
        self.assertEqual(repaired.returncode, EXIT_OK, repaired.stderr)
        self.assertIn("rewrote 1 block(s)", repaired.stdout)
        self.assertEqual(run_check(self.datasets).returncode, EXIT_OK)

        before = (self.datasets / "alpha" / "README.md").read_text(encoding="utf-8")
        again = run_check(self.datasets, "--write")
        self.assertEqual(again.returncode, EXIT_OK, again.stderr)
        self.assertIn("already what the manifest declares", again.stdout)
        self.assertEqual(
            before, (self.datasets / "alpha" / "README.md").read_text(encoding="utf-8")
        )

    def test_write_leaves_the_prose_around_the_block_alone(self) -> None:
        self.settled()
        readme = self.datasets / "alpha" / "README.md"
        text = readme.read_text(encoding="utf-8")
        marked = text.replace(OPEN_MARKER, "Prose above.\n\n" + OPEN_MARKER).replace(
            CLOSE_MARKER, CLOSE_MARKER + "\n\nProse below."
        )
        readme.write_text(marked, encoding="utf-8")
        path = self.datasets / "alpha" / "arcform.yaml"
        path.write_text(
            path.read_text(encoding="utf-8").replace("name: load", "name: normalise"),
            encoding="utf-8",
        )
        self.assertEqual(run_check(self.datasets, "--write").returncode, EXIT_OK)
        after = readme.read_text(encoding="utf-8")
        self.assertIn("Prose above.", after)
        self.assertIn("Prose below.", after)
        self.assertIn("| 2 | `normalise` |", after)


if __name__ == "__main__":
    unittest.main(verbosity=2)
