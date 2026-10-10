#!/usr/bin/env python3
"""Self-test for check_constraint_ratchet.py — proves the ratchet can fail, and on what.

Every case builds a scratch git repository whose base commit holds a package and the
sidecar that hand-holds some of its constraints, edits the working tree the way a
regeneration would, runs the checker as a subprocess, and asserts BOTH the exit status
AND the message. Nothing here touches the network or the real `datasets/` tree.

The cases that matter most are the ones a weaker check passes:

  * the sidecar entry deleted in the same change as the loosening — a check that only
    asks "does the descriptor honour the sidecar" is satisfied by an empty sidecar;
    only the predecessor, read from git, still remembers it;
  * `required: true` replaced by `required: 1` — Python's `True == 1`;
  * a change that is allowed because a person moved it in the sidecar — which must be
    listed, not silent, or the allowance is a way to hide a loosening;
  * the check running at all — an unreadable base is an error, never a pass.
"""

from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

CHECKER = Path(__file__).with_name("check_constraint_ratchet.py")
WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "descriptors.yml"

EXIT_OK = 0
EXIT_REGRESSION = 1
EXIT_ERROR = 2

LEGAL_FORM = {"minLength": 4, "maxLength": 4, "pattern": "^[A-Z0-9]{4}$"}
LEGAL_NAME = {"minLength": 1, "maxLength": 500}
LOOSE = "^[A-Za-z][A-Za-z0-9 /_.#-]*[0-9][A-Za-z0-9 /_.#-]*$"


def field(name: str, **extra: Any) -> dict[str, Any]:
    return {"name": name, "type": "string", **extra}


def base_descriptor() -> dict[str, Any]:
    """Four fields: two hand-held (legal_form, legal_name), one emitted (country), one bare."""
    return {
        "name": "gleif",
        "resources": [
            {
                "name": "gleif",
                "path": "gleif.parquet",
                "schema": {
                    "fields": [
                        field("lei", description="The identifier."),
                        field(
                            "legal_form",
                            constraints=dict(LEGAL_FORM),
                            **{"x-finetype-confidence": 0.6, "x-finetype-label": "representation.identifier.alphanumeric_id"},
                        ),
                        field("legal_name", constraints=dict(LEGAL_NAME)),
                        field("country", constraints={"maxLength": 3}),
                    ]
                },
            }
        ],
    }


def base_sidecar() -> dict[str, Any]:
    return {
        "name": "gleif",
        "fields": {
            "lei": {"description": "The identifier."},
            "legal_form": {"constraints": dict(LEGAL_FORM)},
            "legal_name": {"constraints": dict(LEGAL_NAME)},
        },
    }


def fields_in(descriptor: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {f["name"]: f for f in descriptor["resources"][0]["schema"]["fields"]}


class RatchetSelfTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="constraint-ratchet-")
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name).resolve() / "repo"
        self.datasets = self.repo / "datasets"
        self.datasets.mkdir(parents=True)
        self.git("init", "--quiet", "-b", "main")
        self.write_package("gleif", base_descriptor(), base_sidecar())
        self.commit("base")

    # -- scratch repository ------------------------------------------------------------

    def git(self, *args: str) -> str:
        done = subprocess.run(
            ["git", "-C", str(self.repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
             "-c", "commit.gpgsign=false", *args],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(done.returncode, 0, f"git {' '.join(args)}: {done.stderr}")
        return done.stdout.strip()

    def commit(self, message: str) -> None:
        self.git("add", "-A")
        self.git("commit", "--quiet", "-m", message)

    def write_package(self, slug: str, descriptor: dict[str, Any], sidecar: dict[str, Any] | None) -> None:
        package = self.datasets / slug
        package.mkdir(parents=True, exist_ok=True)
        (package / "datapackage.json").write_text(json.dumps(descriptor, indent=2) + "\n", encoding="utf-8")
        sidecar_path = package / "descriptor.overrides.json"
        if sidecar is None:
            sidecar_path.unlink(missing_ok=True)
        else:
            sidecar_path.write_text(json.dumps(sidecar, indent=2) + "\n", encoding="utf-8")

    def regenerate(self, edit_descriptor=None, edit_sidecar=None, slug: str = "gleif") -> None:
        """Edit the working tree as a regeneration would, leaving the base commit alone."""
        descriptor, sidecar = base_descriptor(), base_sidecar()
        if edit_descriptor:
            edit_descriptor(fields_in(descriptor), descriptor)
        if edit_sidecar:
            edit_sidecar(sidecar["fields"], sidecar)
        self.write_package(slug, descriptor, sidecar)

    def run_check(self, *extra: str, datasets: Path | None = None) -> subprocess.CompletedProcess[str]:
        args = ["--datasets-dir", str(datasets or self.datasets), *extra]
        if "--base" not in extra:
            args += ["--base", "HEAD"]
        return subprocess.run([sys.executable, str(CHECKER), *args], capture_output=True, text=True, check=False)

    def assertOutcome(self, result: subprocess.CompletedProcess[str], expected_exit: int, *needles: str) -> str:  # noqa: N802
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, expected_exit, f"expected exit {expected_exit}, got {result.returncode}\n--- output ---\n{output}")
        for needle in needles:
            self.assertIn(needle, output, f"output did not contain {needle!r}\n--- output ---\n{output}")
        return output

    # -- the case the check exists for --------------------------------------------------

    def test_an_unchanged_tree_passes_and_counts_what_it_held(self) -> None:
        # 5 = legal_form's three keys and legal_name's two. A count of 0 would mean the
        # sidecar was never read, and every refusal below would be unreachable.
        self.assertOutcome(self.run_check(), EXIT_OK, "none replaced or ignored", "5 hand-held constraint(s) carried")

    def test_a_looser_emitted_pattern_replacing_a_hand_held_one_reddens(self) -> None:
        self.regenerate(lambda f, _d: f["legal_form"]["constraints"].update(pattern=LOOSE))
        self.assertOutcome(
            self.run_check(), EXIT_REGRESSION,
            "gleif.legal_form: constraints.pattern", '"^[A-Z0-9]{4}$"', LOOSE, "an emitted value has replaced a hand-held one", "FAILED",
        )

    def test_deleting_the_sidecar_entry_in_the_same_change_does_not_launder_it(self) -> None:
        def loosen(fields: dict, _d: dict) -> None:
            fields["legal_form"]["constraints"].update(pattern=LOOSE)

        def forget(entries: dict, _s: dict) -> None:
            del entries["legal_form"]

        self.regenerate(loosen, forget)
        self.assertOutcome(self.run_check(), EXIT_REGRESSION, "gleif.legal_form: constraints.pattern", "the sidecar declares (absent)", "FAILED")

    def test_a_constraint_that_vanishes_reddens_whether_or_not_the_sidecar_follows(self) -> None:
        def drop(fields: dict, _d: dict) -> None:
            del fields["legal_form"]["constraints"]["maxLength"]

        def forget_key(entries: dict, _s: dict) -> None:
            del entries["legal_form"]["constraints"]["maxLength"]

        with self.subTest("the sidecar still declares it"):
            self.regenerate(drop)
            self.assertOutcome(self.run_check(), EXIT_REGRESSION, "gleif.legal_form: constraints.maxLength", "the descriptor now carries (absent)")
        with self.subTest("the sidecar dropped it too"):
            self.regenerate(drop, forget_key)
            self.assertOutcome(self.run_check(), EXIT_REGRESSION, "gleif.legal_form: constraints.maxLength", "the sidecar declares (absent)")

    def test_a_constraint_moved_in_the_sidecar_is_listed_and_not_refused(self) -> None:
        def widen_descriptor(fields: dict, _d: dict) -> None:
            fields["legal_form"]["constraints"].update(pattern="^[A-Z0-9]{4,6}$", maxLength=6)

        def widen_sidecar(entries: dict, _s: dict) -> None:
            entries["legal_form"]["constraints"].update(pattern="^[A-Z0-9]{4,6}$", maxLength=6)

        self.regenerate(widen_descriptor, widen_sidecar)
        output = self.assertOutcome(
            self.run_check(), EXIT_OK,
            "gleif.legal_form: constraints.pattern", '"^[A-Z0-9]{4}$" → "^[A-Z0-9]{4,6}$"', "gleif.legal_form: constraints.maxLength: 4 → 6",
        )
        self.assertNotIn("FAILED", output)

    def test_a_sidecar_that_moved_to_a_value_the_descriptor_does_not_carry_reddens(self) -> None:
        def widen_sidecar(entries: dict, _s: dict) -> None:
            entries["legal_form"]["constraints"].update(maxLength=6)

        def loosen(fields: dict, _d: dict) -> None:
            fields["legal_form"]["constraints"].update(maxLength=9)

        self.regenerate(loosen, widen_sidecar)
        self.assertOutcome(self.run_check(), EXIT_REGRESSION, "gleif.legal_form: constraints.maxLength", "the descriptor now carries 9 and the sidecar declares 6")

    # -- the sidecar half, which needs no predecessor ------------------------------------

    def test_an_override_the_descriptor_never_applied_reddens(self) -> None:
        def declare(entries: dict, _s: dict) -> None:
            entries["country"] = {"constraints": {"maxLength": 2}}

        self.regenerate(None, declare)
        self.assertOutcome(self.run_check(), EXIT_REGRESSION, "gleif.country: constraints.maxLength", "the regeneration did not apply the override", "the descriptor carries 3")

    # -- columns -------------------------------------------------------------------------

    def test_a_column_dropped_with_its_override_is_listed_and_not_refused(self) -> None:
        def drop_column(_f: dict, descriptor: dict) -> None:
            descriptor["resources"][0]["schema"]["fields"] = [f for f in descriptor["resources"][0]["schema"]["fields"] if f["name"] != "legal_name"]

        def drop_entry(entries: dict, _s: dict) -> None:
            del entries["legal_name"]

        self.regenerate(drop_column, drop_entry)
        self.assertOutcome(self.run_check(), EXIT_OK, "gleif.legal_name: removed")

    def test_a_column_dropped_while_its_override_stays_reddens(self) -> None:
        def drop_column(_f: dict, descriptor: dict) -> None:
            descriptor["resources"][0]["schema"]["fields"] = [f for f in descriptor["resources"][0]["schema"]["fields"] if f["name"] != "legal_name"]

        self.regenerate(drop_column)
        self.assertOutcome(self.run_check(), EXIT_REGRESSION, "gleif.legal_name: constraints.minLength", "the descriptor now carries (absent)")

    def test_a_constraint_released_by_the_sidecar_is_listed(self) -> None:
        def release(entries: dict, _s: dict) -> None:
            del entries["legal_name"]

        self.regenerate(None, release)
        self.assertOutcome(self.run_check(), EXIT_OK, "gleif.legal_name: constraints.maxLength: 500 → released by the sidecar")

    # -- every changed field is named ------------------------------------------------------

    def test_every_changed_field_is_listed_even_when_nothing_is_refused(self) -> None:
        def regenerate(fields: dict, descriptor: dict) -> None:
            fields["legal_form"]["x-finetype-confidence"] = 0.4
            fields["country"]["constraints"]["maxLength"] = 5  # emitted, not hand-held
            fields["lei"]["type"] = "integer"
            fields["lei"]["description"] = "A different sentence entirely."
            descriptor["resources"][0]["schema"]["fields"].append(field("city", constraints={"maxLength": 80}))

        self.regenerate(regenerate)
        output = self.assertOutcome(
            self.run_check(), EXIT_OK,
            "gleif.legal_form: x-finetype-confidence: 0.6 → 0.4",
            "gleif.country: constraints.maxLength: 3 → 5",
            "gleif.lei: type: \"string\" → \"integer\"",
            "gleif.lei: description: (prose) → (prose changed)",
            "gleif.city: added",
            "5 field change(s)",
        )
        self.assertNotIn("A different sentence", output, "prose is named as changed, not printed")

    # -- the value, not the Python object ---------------------------------------------------

    def test_true_is_not_one(self) -> None:
        required = {"required": True}
        descriptor = base_descriptor()
        fields_in(descriptor)["lei"]["constraints"] = dict(required)
        sidecar = base_sidecar()
        sidecar["fields"]["lei"]["constraints"] = dict(required)
        self.write_package("gleif", descriptor, sidecar)
        self.commit("lei is required")
        edited = copy.deepcopy(descriptor)
        fields_in(edited)["lei"]["constraints"] = {"required": 1}
        self.write_package("gleif", edited, sidecar)
        self.assertOutcome(self.run_check(), EXIT_REGRESSION, "gleif.lei: constraints.required", "the predecessor held true by hand", "now carries 1")

    # -- reach ------------------------------------------------------------------------------

    def test_every_package_is_visited_not_just_the_first(self) -> None:
        self.write_package("edgar", base_descriptor(), base_sidecar())
        self.commit("a second package")
        self.regenerate(lambda f, _d: f["legal_form"]["constraints"].update(pattern=LOOSE), slug="gleif")
        # `edgar` sorts before `gleif`: a loop that stopped after the first would pass.
        self.assertOutcome(self.run_check(), EXIT_REGRESSION, "gleif.legal_form: constraints.pattern", "1 of 2 descriptor(s)")

    def test_only_narrows_and_names_what_it_cannot_find(self) -> None:
        self.write_package("edgar", base_descriptor(), base_sidecar())
        self.commit("a second package")
        self.regenerate(lambda f, _d: f["legal_form"]["constraints"].update(pattern=LOOSE), slug="gleif")
        self.assertOutcome(self.run_check("--only", "edgar"), EXIT_OK, "none replaced or ignored")
        self.assertOutcome(self.run_check("--only", "nonesuch"), EXIT_ERROR, "no descriptor for dataset(s): nonesuch")

    def test_a_package_new_since_the_base_is_named_and_still_held_to_its_sidecar(self) -> None:
        self.write_package("naics", base_descriptor(), base_sidecar())
        self.assertOutcome(self.run_check(), EXIT_OK, "new since", "naics")
        descriptor = base_descriptor()
        fields_in(descriptor)["legal_form"]["constraints"].pop("pattern")
        self.write_package("naics", descriptor, base_sidecar())
        self.assertOutcome(self.run_check(), EXIT_REGRESSION, "naics.legal_form: constraints.pattern", "the regeneration did not apply the override")

    # -- a check that cannot read its predecessor must not pass --------------------------------

    def test_an_unresolvable_base_is_an_error_not_a_pass(self) -> None:
        self.assertOutcome(self.run_check("--base", "origin/nonesuch"), EXIT_ERROR, "does not resolve to a commit", "must not pass")

    def test_a_tree_outside_git_is_an_error_not_a_pass(self) -> None:
        with tempfile.TemporaryDirectory(prefix="constraint-ratchet-bare-") as bare:
            datasets = Path(bare) / "datasets"
            package = datasets / "gleif"
            package.mkdir(parents=True)
            (package / "datapackage.json").write_text(json.dumps(base_descriptor()), encoding="utf-8")
            self.assertOutcome(self.run_check(datasets=datasets), EXIT_ERROR, "not inside a git work tree")

    def test_a_malformed_descriptor_is_an_error_not_a_pass(self) -> None:
        (self.datasets / "gleif" / "datapackage.json").write_text("{", encoding="utf-8")
        self.assertOutcome(self.run_check(), EXIT_ERROR, "not valid JSON")

    def test_a_field_two_resources_share_cannot_be_addressed_and_is_refused(self) -> None:
        descriptor = base_descriptor()
        descriptor["resources"].append(copy.deepcopy(descriptor["resources"][0]) | {"name": "again"})
        self.write_package("gleif", descriptor, base_sidecar())
        self.assertOutcome(self.run_check(), EXIT_ERROR, "appears in more than one resource")

    def test_no_datasets_is_an_error(self) -> None:
        empty = self.repo / "empty"
        empty.mkdir()
        self.assertOutcome(self.run_check(datasets=empty), EXIT_ERROR, "no datasets/*/datapackage.json")

    # -- the report ------------------------------------------------------------------------------

    def test_the_json_report_names_the_base_and_each_violation(self) -> None:
        self.regenerate(lambda f, _d: f["legal_form"]["constraints"].update(pattern=LOOSE))
        out = self.repo / "ratchet.json"
        self.assertOutcome(self.run_check("--json", str(out)), EXIT_REGRESSION)
        report = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(report["base_sha"], self.git("rev-parse", "HEAD"))
        self.assertEqual([(v["package"], v["field"], v["key"], v["kind"]) for v in report["violations"]], [("gleif", "legal_form", "pattern", "replaced")])
        self.assertEqual([c["what"] for c in report["changes"]], ["constraints.pattern"])

    def test_a_clean_run_writes_an_empty_violations_list(self) -> None:
        out = self.repo / "ratchet.json"
        self.assertOutcome(self.run_check("--json", str(out)), EXIT_OK)
        self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["violations"], [])


class WiredIntoCiTest(unittest.TestCase):
    """A check nobody runs reddens nothing. The workflow must run it and its self-test."""

    def test_the_workflow_runs_the_check_and_its_self_test(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertRegex(text, r"run: python scripts/test_check_constraint_ratchet\.py")
        self.assertRegex(text, r"python scripts/check_constraint_ratchet\.py\b")

    def test_the_gate_reads_a_history_that_contains_its_base(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        job = re.search(r"\n  constraint-ratchet:\n(.*?)(?=\n  [a-z][a-z-]*:\n|\Z)", text, re.S)
        if job is None:
            self.fail("no constraint-ratchet job")
        self.assertIn("fetch-depth: 0", job.group(1), "a shallow checkout has no origin/main to hold the tree to")
        self.assertIn("--base origin/main", job.group(1))


if __name__ == "__main__":
    unittest.main(verbosity=2)
