#!/usr/bin/env python3
# /// script
# requires-python = "==3.12.*"
# dependencies = ["pyyaml==6.0.3"]
# ///
"""A Protocol that can reach a fixed point declares each file it writes once.

arcform marks a step stale when a file the step produced no longer hashes to what the
step wrote. When two steps declare the same path, the later one rewrites the earlier
one's output on every run, the earlier step reads as stale on the next, and so does
everything downstream of it: the run re-does work that nothing asked it to redo and a
publish is never a no-op. `edgar_gleif` did exactly this — `stamp_descriptor` rewrote
the Parquet `export_edgar_gleif` wrote, and `describe`, `derive_join_coverage` and
`stamp_descriptor` all wrote `datapackage.json` — until each file was given one writer.

The paths a step writes are read from the manifest as a document: a step's
`produces:` list, the `produces:` list inside an operator's `with:` block (where `uv@1`
keeps it), and the `dest:` / `out:` of that block. A manifest that lists a path in a
comment declares nothing.

Only the manifests in `ONE_WRITER` are held to this. `edgar`, `gleif` and `naics`
still have their stamp step rewrite the file their export wrote, and fixing that is
their own change; they join this list when it lands.

Nothing here touches the network, and the shipped manifests are only read.
"""

from __future__ import annotations

import textwrap
import unittest
from pathlib import Path
from typing import Any

import yaml

DATASETS = Path(__file__).resolve().parent.parent / "datasets"

# Manifests that have been restructured so each file they write has one writer.
ONE_WRITER = ("edgar_gleif",)

# The `with:` keys that name a file an operator writes.
WRITTEN_BY_OPERATOR = ("dest", "out")


def declared_writers(manifest: dict[str, Any]) -> dict[str, list[str]]:
    """path -> the steps that declare they write it, in manifest order."""
    writers: dict[str, list[str]] = {}
    for step in manifest.get("steps") or []:
        name = str(step.get("name"))
        with_block = step.get("with") or {}
        # `produces:` sits on the step for `sql:` and `command:` steps, and inside `with:`
        # for `uv@1` — the stamp step's own declaration is the second kind.
        paths = [str(path) for path in step.get("produces") or []]
        paths += [str(path) for path in with_block.get("produces") or []]
        for key in WRITTEN_BY_OPERATOR:
            if isinstance(with_block.get(key), str):
                paths.append(with_block[key])
        for path in dict.fromkeys(paths):
            writers.setdefault(path, []).append(name)
    return writers


def shared_paths(manifest: dict[str, Any]) -> dict[str, list[str]]:
    return {path: steps for path, steps in declared_writers(manifest).items() if len(steps) > 1}


class OneWriterPerFile(unittest.TestCase):
    def test_the_restructured_manifests_declare_each_file_once(self) -> None:
        for slug in ONE_WRITER:
            manifest = yaml.safe_load((DATASETS / slug / "arcform.yaml").read_text(encoding="utf-8"))
            self.assertEqual(
                shared_paths(manifest),
                {},
                f"{slug}: a path declared by more than one step makes the earlier writer stale on every run",
            )

    def test_the_manifests_are_read_not_skimmed(self) -> None:
        """Both ends of the publish are declared, once each, by the step that ends it."""
        manifest = yaml.safe_load((DATASETS / "edgar_gleif" / "arcform.yaml").read_text(encoding="utf-8"))
        writers = declared_writers(manifest)
        self.assertEqual(writers["build/edgar_gleif.parquet"], ["stamp_descriptor"])
        self.assertEqual(writers["datapackage.json"], ["stamp_descriptor"])

    # ------------------------------------------------- the check can fail, and on what

    def scratch(self, body: str) -> dict[str, Any]:
        return yaml.safe_load(textwrap.dedent(body))

    def test_a_step_rewriting_an_earlier_steps_operator_output_is_named(self) -> None:
        manifest = self.scratch(
            """
            steps:
              - name: export
                op: parquet_export@1
                with: {dest: build/x.parquet}
              - name: stamp
                op: uv@1
                with: {produces: [build/x.parquet]}
            """
        )
        self.assertEqual(shared_paths(manifest), {"build/x.parquet": ["export", "stamp"]})

    def test_two_steps_producing_one_file_are_named(self) -> None:
        manifest = self.scratch(
            """
            steps:
              - name: describe
                op: datapackage_describe@1
                with: {out: datapackage.json}
              - name: derive
                op: uv@1
                with: {produces: [datapackage.json]}
              - name: stamp
                op: uv@1
                with: {produces: [datapackage.json]}
            """
        )
        self.assertEqual(shared_paths(manifest), {"datapackage.json": ["describe", "derive", "stamp"]})

    def test_a_path_that_appears_only_in_a_comment_declares_nothing(self) -> None:
        manifest = self.scratch(
            """
            steps:
              - name: export
                op: parquet_export@1
                with: {dest: build/x.parquet}
              # - name: stamp
              #   produces: [build/x.parquet]
            """
        )
        self.assertEqual(shared_paths(manifest), {})

    def test_the_manifest_as_it_was_before_the_restructure_is_refused(self) -> None:
        """The defect, as it was: the stamp declared the export's file and describe's file."""
        manifest = self.scratch(
            """
            steps:
              - name: export_edgar_gleif
                op: parquet_export@1
                with: {dest: build/edgar_gleif.parquet}
              - name: describe
                op: datapackage_describe@1
                with: {parquet: build/edgar_gleif.parquet, out: datapackage.json}
              - name: derive_join_coverage
                op: uv@1
                with: {produces: [datapackage.json]}
              - name: stamp_descriptor
                op: uv@1
                with: {produces: [build/edgar_gleif.parquet, datapackage.json]}
            """
        )
        self.assertEqual(
            shared_paths(manifest),
            {
                "build/edgar_gleif.parquet": ["export_edgar_gleif", "stamp_descriptor"],
                "datapackage.json": ["describe", "derive_join_coverage", "stamp_descriptor"],
            },
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
