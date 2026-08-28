#!/usr/bin/env python3
# /// script
# requires-python = "==3.12.*"
# dependencies = []
# ///
"""Self-test for check_label_agreement.py — proves the check can fail, and on what.

Every case builds a scratch `datasets/` tree of real `datapackage.json` files, runs
the check as a subprocess, and asserts BOTH the exit status AND the message. Status
alone cannot tell a refusal from a crash, which is why the check separates 1 (a
stated reason no longer describes anything) from 2 (an input could not be read at
all) and why every case pins the number it expects.

THE MUTATIONS ARE ON THE DESCRIPTORS, NOT ON THE CHECK. The defect is two packages
describing one concept differently while every per-package check passes, so a case
that edits the comparison code would be driving the wrong altitude. Each case below
moves a declaration in a package and asserts what the report says about it:

  * two packages that AGREED made to differ, and two that DIFFERED made to agree —
    the report has to move in both directions, or it is not reading the tree;
  * a member that declares no bound at all, reported as `(absent)` rather than
    dropped. `edgar_gleif.company_name` reached its 1..65536 by being left at the
    describe default, and a comparison that skipped undeclared properties would
    have called that agreement;
  * `type` and `format`, not only `constraints` — a `string` and a `date` under one
    label disagree about the concept as loudly as two patterns do;
  * THE ESCAPE HATCH: a constraint difference hidden by RELABELLING one of the two
    fields. Grouping by label alone, that mutation takes the check from reporting a
    difference to reporting nothing, and nothing has been fixed. The case asserts
    the concept difference goes and a field-name difference arrives in its place;
  * A REASON GOING STALE, driven by moving the value the reason pins rather than by
    editing the reason. That is the altitude: the failure being pinned is a sentence
    that goes on excusing a difference after the difference changed underneath it.
    Its sibling case asserts that a reason naming the right concept and property but
    a DIFFERENT value set does not close the difference — if matching ignored the
    values, one exemption would license every later divergence of that label;
  * an entry with no `reason:` sentence, refused as a fault. That is what silencing
    would look like if it were written down;
  * two fields labelled `unknown` with different constraints, NOT reported as a
    concept — and both NAMED in the output, because an exclusion nobody can see
    reads the same as a field the sweep never reached;
  * an empty `datasets/` directory and an unreadable descriptor — the vacuous pass
    and the partial sweep, each refused by name, with `examined N of M` asserted
    where N and M differ.

Nothing here touches the network or the real `datasets/` tree.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

CHECK = Path(__file__).with_name("check_label_agreement.py")

EXIT_OK = 0
EXIT_DISAGREEMENT = 1
EXIT_ERROR = 2

LABEL = "x-finetype-label"


def field(
    name: str,
    label: str | None,
    *,
    type_: str = "string",
    constraints: dict[str, Any] | None = None,
    description: str = "A column.",
    **extra: Any,
) -> dict[str, Any]:
    entry: dict[str, Any] = {"name": name, "type": type_, "description": description}
    if label is not None:
        entry[LABEL] = label
    if constraints is not None:
        entry["constraints"] = constraints
    entry.update(extra)
    return entry


def package(slug: str, fields: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "name": slug,
        "resources": [
            {
                "name": slug,
                "path": f"https://example.invalid/{slug}.parquet",
                "schema": {"fields": fields},
            }
        ],
    }


ALPHA = package(
    "alpha",
    [
        field(
            "legal_name",
            "representation.text.entity_name",
            constraints={"minLength": 1, "maxLength": 500},
        ),
        field("corpus", "representation.text.plain_text", constraints={"maxLength": 65536}),
        field("as_of", "datetime.date.iso", type_="date", format="%Y-%m-%d"),
    ],
)

BETA = package(
    "beta",
    [
        field(
            "legal_name",
            "representation.text.entity_name",
            constraints={"minLength": 1, "maxLength": 500},
        ),
        field("corpus", "representation.text.plain_text", constraints={"maxLength": 65536}),
        field("as_of", "datetime.date.iso", type_="date", format="%Y-%m-%d"),
    ],
)


def correction(*, drop: tuple[str, ...] = (), **over: Any) -> dict[str, Any]:
    """A well-formed correction on `beta.legal_name`, before whatever a case breaks."""
    entry: dict[str, Any] = {
        "package": "beta",
        "pointer": "resources[name=beta].schema.fields[name=legal_name].constraints.maxLength",
        "verdict": "wrong",
        "declares": 200,
        "becomes": 500,
        "blocked_on": "A republish of the object whose footer carries this descriptor.",
        "reason": "Beta narrowed a register-typed bound to a measurement of its own snapshot.",
    }
    entry.update(over)
    for key in drop:
        entry.pop(key, None)
    return entry


class ScratchTree(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="label-agreement-selftest-")
        self.addCleanup(self._tmp.cleanup)
        self.datasets = Path(self._tmp.name) / "datasets"
        self.datasets.mkdir(parents=True)

    def write(self, document: dict[str, Any], slug: str | None = None) -> Path:
        slug = slug or document["name"]
        directory = self.datasets / slug
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "datapackage.json"
        path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        return path

    def reasons(self, *entries: dict[str, Any]) -> None:
        self.agreement(reasons=list(entries))

    def agreement(
        self,
        reasons: list[dict[str, Any]] | None = None,
        corrections: list[dict[str, Any]] | None = None,
    ) -> None:
        (self.datasets / "label-agreement.json").write_text(
            json.dumps({"reasons": reasons or [], "corrections": corrections or []}, indent=2)
            + "\n",
            encoding="utf-8",
        )

    def run_check(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(CHECK), "--datasets-dir", str(self.datasets), *extra],
            capture_output=True,
            text=True,
        )

    def settled(self) -> None:
        """Two packages that describe every shared concept the same way."""
        self.write(ALPHA)
        self.write(BETA)
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_OK, result.stdout + result.stderr)
        self.assertIn("every concept is described the same way", result.stdout)

    def both(self, result: subprocess.CompletedProcess[str]) -> str:
        return result.stdout + result.stderr


class TwoPackagesStopAgreeing(ScratchTree):
    """The direction the card names: make two packages differ where they agree."""

    def test_a_moved_maxlength_is_reported_and_both_values_are_named(self) -> None:
        self.settled()
        moved = copy.deepcopy(BETA)
        moved["resources"][0]["schema"]["fields"][0]["constraints"]["maxLength"] = 200
        self.write(moved)
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_OK, self.both(result))
        self.assertIn(
            "label 'representation.text.entity_name' — constraints.maxLength differs [OPEN]",
            result.stdout,
        )
        self.assertIn("alpha.legal_name: 500", result.stdout)
        self.assertIn("beta.legal_name: 200", result.stdout)
        self.assertIn("1 difference(s)", result.stdout)

    def test_an_undeclared_bound_is_reported_as_absent_not_skipped(self) -> None:
        """The `edgar_gleif.company_name` case: a default nobody decided, not agreement."""
        self.settled()
        moved = copy.deepcopy(BETA)
        del moved["resources"][0]["schema"]["fields"][0]["constraints"]["maxLength"]
        self.write(moved)
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_OK, self.both(result))
        self.assertIn("constraints.maxLength differs [OPEN]", result.stdout)
        self.assertIn("beta.legal_name: (absent)", result.stdout)

    def test_a_differing_type_under_one_label_is_reported(self) -> None:
        self.settled()
        moved = copy.deepcopy(BETA)
        moved["resources"][0]["schema"]["fields"][2]["type"] = "string"
        self.write(moved)
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_OK, self.both(result))
        self.assertIn("label 'datetime.date.iso' — type differs [OPEN]", result.stdout)
        self.assertIn('alpha.as_of: "date"', result.stdout)
        self.assertIn('beta.as_of: "string"', result.stdout)

    def test_a_differing_format_under_one_label_is_reported(self) -> None:
        self.settled()
        moved = copy.deepcopy(BETA)
        moved["resources"][0]["schema"]["fields"][2]["format"] = "%d/%m/%Y"
        self.write(moved)
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_OK, self.both(result))
        self.assertIn("label 'datetime.date.iso' — format differs [OPEN]", result.stdout)

    def test_prose_and_measurements_are_not_compared_and_the_one_declaration_is(self) -> None:
        """Descriptions name each package's own register; confidences measure a column."""
        self.settled()
        moved = copy.deepcopy(BETA)
        entry = moved["resources"][0]["schema"]["fields"][0]
        entry["description"] = "An entirely different sentence about this column."
        entry["x-finetype-confidence"] = 0.42
        entry["constraints"]["maxLength"] = 200
        self.write(moved)
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_OK, self.both(result))
        self.assertIn("1 difference(s)", result.stdout)
        self.assertIn("constraints.maxLength differs", result.stdout)
        self.assertNotIn("description differs", result.stdout)
        self.assertNotIn("x-finetype-confidence differs", result.stdout)


class TwoPackagesStartAgreeing(ScratchTree):
    """The other direction, without which a report that always fires would pass."""

    def test_making_them_agree_removes_the_difference(self) -> None:
        differing = copy.deepcopy(BETA)
        differing["resources"][0]["schema"]["fields"][0]["constraints"]["maxLength"] = 200
        self.write(ALPHA)
        self.write(differing)
        before = self.run_check()
        self.assertIn("1 difference(s)", before.stdout)

        self.write(BETA)
        after = self.run_check()
        self.assertEqual(after.returncode, EXIT_OK, self.both(after))
        self.assertIn("every concept is described the same way", after.stdout)
        self.assertNotIn("difference(s) between packages", after.stdout)

    def test_a_label_only_one_field_carries_is_not_a_disagreement(self) -> None:
        self.settled()
        alone = copy.deepcopy(BETA)
        alone["resources"][0]["schema"]["fields"].append(
            field("ticker", "finance.securities.ticker", constraints={"pattern": "^[A-Z]+$"})
        )
        self.write(alone)
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_OK, self.both(result))
        self.assertIn("every concept is described the same way", result.stdout)


class RelabellingCannotHideADisagreement(ScratchTree):
    """The escape hatch grouping by label alone leaves open, and the case that closes it."""

    def test_relabelling_moves_the_finding_rather_than_removing_it(self) -> None:
        self.settled()
        differing = copy.deepcopy(BETA)
        differing["resources"][0]["schema"]["fields"][0]["constraints"]["maxLength"] = 200
        self.write(differing)
        before = self.run_check()
        self.assertIn(
            "label 'representation.text.entity_name' — constraints.maxLength differs",
            before.stdout,
        )

        # Nothing is fixed here: the two bounds still disagree. Only the grouping key
        # moves, and under a label-only check the report would go silent.
        relabelled = copy.deepcopy(differing)
        relabelled["resources"][0]["schema"]["fields"][0][LABEL] = "representation.text.plain_text"
        self.write(relabelled)
        after = self.run_check()
        self.assertEqual(after.returncode, EXIT_OK, self.both(after))
        self.assertNotIn(
            "label 'representation.text.entity_name' — constraints.maxLength differs",
            after.stdout,
        )
        self.assertIn("field name 'legal_name' — x-finetype-label differs [OPEN]", after.stdout)
        self.assertIn('alpha.legal_name: "representation.text.entity_name"', after.stdout)
        self.assertIn('beta.legal_name: "representation.text.plain_text"', after.stdout)

    def test_relabelling_to_unknown_does_not_buy_silence_either(self) -> None:
        """`unknown` leaves the concept grouping, so it is the cheapest way to hide."""
        self.settled()
        hidden = copy.deepcopy(BETA)
        hidden["resources"][0]["schema"]["fields"][0][LABEL] = "unknown"
        self.write(hidden)
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_OK, self.both(result))
        self.assertIn("field name 'legal_name' — x-finetype-label differs [OPEN]", result.stdout)
        self.assertIn('beta.legal_name: "unknown"', result.stdout)

    def test_deleting_the_label_outright_does_not_buy_silence_either(self) -> None:
        self.settled()
        stripped = copy.deepcopy(BETA)
        del stripped["resources"][0]["schema"]["fields"][0][LABEL]
        self.write(stripped)
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_OK, self.both(result))
        self.assertIn("field name 'legal_name' — x-finetype-label differs [OPEN]", result.stdout)
        self.assertIn("beta.legal_name: (absent)", result.stdout)


class UnknownIsNotAConcept(ScratchTree):
    def test_two_unknown_fields_are_not_compared_and_are_named_as_excluded(self) -> None:
        self.write(
            package(
                "alpha",
                [field("notes", "unknown", constraints={"maxLength": 10})],
            )
        )
        self.write(
            package(
                "beta",
                [field("remarks", "unknown", constraints={"maxLength": 99999})],
            )
        )
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_OK, self.both(result))
        self.assertIn("every concept is described the same way", result.stdout)
        self.assertIn("2 field(s) carry no concept to group by", result.stdout)
        self.assertIn("alpha.notes", result.stdout)
        self.assertIn("beta.remarks", result.stdout)


class AReasonClosesOneDifferenceAndNotALabel(ScratchTree):
    """The values are pinned, so a reason cannot become a standing licence."""

    def differing(self) -> None:
        moved = copy.deepcopy(BETA)
        moved["resources"][0]["schema"]["fields"][0]["constraints"]["maxLength"] = 200
        self.write(ALPHA)
        self.write(moved)

    def entry(self, values: dict[str, Any], reason: str = "Two registers, two typed value spaces.") -> dict[str, Any]:
        return {
            "concept": "representation.text.entity_name",
            "property": "constraints.maxLength",
            "values": values,
            "reason": reason,
        }

    def test_a_matching_reason_closes_the_difference(self) -> None:
        self.differing()
        self.reasons(self.entry({"alpha.legal_name": 500, "beta.legal_name": 200}))
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_OK, self.both(result))
        self.assertIn("constraints.maxLength differs [accounted for]", result.stdout)
        self.assertIn("0 with no stated reason", result.stdout)
        self.assertIn("reason: Two registers, two typed value spaces.", result.stdout)

    def test_moving_the_pinned_value_makes_the_reason_stale_and_reddens(self) -> None:
        """Driven by moving the descriptor, not by editing the reason."""
        self.differing()
        self.reasons(self.entry({"alpha.legal_name": 500, "beta.legal_name": 200}))
        self.assertEqual(self.run_check().returncode, EXIT_OK)

        moved_again = copy.deepcopy(BETA)
        moved_again["resources"][0]["schema"]["fields"][0]["constraints"]["maxLength"] = 300
        self.write(moved_again)
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_DISAGREEMENT, self.both(result))
        self.assertIn("1 stated reason(s) no longer describe any difference", result.stderr)
        self.assertIn("it pins beta.legal_name: 200", result.stderr)
        # And the difference is reported again rather than staying closed.
        self.assertIn("constraints.maxLength differs [OPEN]", result.stdout)
        self.assertIn("beta.legal_name: 300", result.stdout)

    def test_a_reason_naming_the_label_but_other_values_does_not_close_it(self) -> None:
        """Matching on label and property alone would license every later divergence."""
        self.differing()
        self.reasons(self.entry({"alpha.legal_name": 500, "beta.legal_name": 250}))
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_DISAGREEMENT, self.both(result))
        self.assertIn("constraints.maxLength differs [OPEN]", result.stdout)
        self.assertIn("no longer describe any difference", result.stderr)

    def test_a_reason_that_matches_nothing_at_all_reddens(self) -> None:
        self.settled()
        self.reasons(self.entry({"alpha.legal_name": 500, "beta.legal_name": 200}))
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_DISAGREEMENT, self.both(result))
        self.assertIn("no longer describe any difference", result.stderr)

    def test_a_field_name_reason_closes_a_label_difference(self) -> None:
        self.settled()
        relabelled = copy.deepcopy(BETA)
        relabelled["resources"][0]["schema"]["fields"][1][LABEL] = "unknown"
        self.write(relabelled)
        self.reasons(
            {
                "field": "corpus",
                "property": LABEL,
                "values": {
                    "alpha.corpus": "representation.text.plain_text",
                    "beta.corpus": "unknown",
                },
                "reason": "Not yet re-measured on that object.",
            }
        )
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_OK, self.both(result))
        self.assertIn("field name 'corpus' — x-finetype-label differs [accounted for]", result.stdout)

    def test_a_concept_reason_does_not_close_a_field_name_difference(self) -> None:
        """The two groupings are separate; a reason says which one it answers."""
        self.settled()
        relabelled = copy.deepcopy(BETA)
        relabelled["resources"][0]["schema"]["fields"][1][LABEL] = "unknown"
        self.write(relabelled)
        self.reasons(
            {
                "concept": "corpus",
                "property": LABEL,
                "values": {
                    "alpha.corpus": "representation.text.plain_text",
                    "beta.corpus": "unknown",
                },
                "reason": "Not yet re-measured on that object.",
            }
        )
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_DISAGREEMENT, self.both(result))
        self.assertIn("field name 'corpus' — x-finetype-label differs [OPEN]", result.stdout)


class AnEntryWithNoSentenceIsRefused(ScratchTree):
    """What silencing would look like if someone wrote it down."""

    def base(self) -> dict[str, Any]:
        return {
            "concept": "representation.text.entity_name",
            "property": "constraints.maxLength",
            "values": {"alpha.legal_name": 500, "beta.legal_name": 200},
            "reason": "A sentence.",
        }

    def refused(self, entry: dict[str, Any], expected: str) -> None:
        self.settled()
        self.reasons(entry)
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_ERROR, self.both(result))
        self.assertIn(expected, result.stderr)

    def test_no_reason_key(self) -> None:
        entry = self.base()
        del entry["reason"]
        self.refused(entry, "carries no `reason:`")

    def test_a_blank_reason(self) -> None:
        entry = self.base()
        entry["reason"] = "   "
        self.refused(entry, "carries no `reason:`")

    def test_both_groupings_named(self) -> None:
        entry = self.base()
        entry["field"] = "legal_name"
        self.refused(entry, "of `concept:`/`field:`")

    def test_neither_grouping_named(self) -> None:
        entry = self.base()
        del entry["concept"]
        self.refused(entry, "of `concept:`/`field:`")

    def test_one_value_is_not_a_difference(self) -> None:
        entry = self.base()
        entry["values"] = {"alpha.legal_name": 500}
        self.refused(entry, "must name at least two")

    def test_a_reasons_file_that_is_not_json(self) -> None:
        self.settled()
        (self.datasets / "label-agreement.json").write_text("{not json", encoding="utf-8")
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_ERROR, self.both(result))
        self.assertIn("cannot be read as JSON", result.stderr)


class StrictTurnsAnOpenDifferenceIntoAFailure(ScratchTree):
    def test_the_same_tree_passes_reporting_and_fails_strict(self) -> None:
        moved = copy.deepcopy(BETA)
        moved["resources"][0]["schema"]["fields"][0]["constraints"]["maxLength"] = 200
        self.write(ALPHA)
        self.write(moved)
        reporting = self.run_check()
        self.assertEqual(reporting.returncode, EXIT_OK, self.both(reporting))
        strict = self.run_check("--strict")
        self.assertEqual(strict.returncode, EXIT_DISAGREEMENT, self.both(strict))
        self.assertIn("FAILED under --strict", strict.stderr)

    def test_strict_passes_once_every_difference_has_a_reason(self) -> None:
        moved = copy.deepcopy(BETA)
        moved["resources"][0]["schema"]["fields"][0]["constraints"]["maxLength"] = 200
        self.write(ALPHA)
        self.write(moved)
        self.reasons(
            {
                "concept": "representation.text.entity_name",
                "property": "constraints.maxLength",
                "values": {"alpha.legal_name": 500, "beta.legal_name": 200},
                "reason": "Two registers, two typed value spaces.",
            }
        )
        strict = self.run_check("--strict")
        self.assertEqual(strict.returncode, EXIT_OK, self.both(strict))


class TheSweepRefusesRatherThanSkips(ScratchTree):
    def test_an_empty_datasets_directory_is_a_fault_not_a_pass(self) -> None:
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_ERROR, self.both(result))
        self.assertIn("nothing was compared, which is a fault and not a pass", result.stderr)

    def test_an_unreadable_descriptor_is_collected_and_the_count_shows_it(self) -> None:
        self.settled()
        (self.datasets / "beta" / "datapackage.json").write_text("{ broken", encoding="utf-8")
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_ERROR, self.both(result))
        self.assertIn("examined 1 package(s) of 2 found", result.stdout)
        self.assertIn("cannot be read as JSON", result.stderr)

    def test_two_faults_are_both_named_rather_than_the_first_only(self) -> None:
        self.write(ALPHA)
        self.write(BETA)
        for slug in ("alpha", "beta"):
            (self.datasets / slug / "datapackage.json").write_text("{ broken", encoding="utf-8")
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_ERROR, self.both(result))
        self.assertIn("examined 0 package(s) of 2 found", result.stdout)
        self.assertIn("2 input(s) could not be read at all", result.stderr)

    def test_one_package_declaring_a_field_name_twice_is_refused(self) -> None:
        """`<package>.<field>` in a reason has to name exactly one field."""
        two_resources = {
            "name": "alpha",
            "resources": [
                {"name": "one", "schema": {"fields": [field("legal_name", "x")]}},
                {"name": "two", "schema": {"fields": [field("legal_name", "y")]}},
            ],
        }
        self.write(two_resources)
        self.write(BETA)
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_ERROR, self.both(result))
        self.assertIn("cannot name both", result.stderr)


class TheFindingsAreWritableAsJson(ScratchTree):
    def test_json_carries_every_difference_and_its_reason_state(self) -> None:
        moved = copy.deepcopy(BETA)
        moved["resources"][0]["schema"]["fields"][0]["constraints"]["maxLength"] = 200
        self.write(ALPHA)
        self.write(moved)
        out = Path(self._tmp.name) / "findings.json"
        result = self.run_check("--json", str(out))
        self.assertEqual(result.returncode, EXIT_OK, self.both(result))
        findings = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(findings["examined"], 2)
        self.assertEqual(len(findings["differences"]), 1)
        self.assertIsNone(findings["differences"][0]["reason"])
        self.assertEqual(
            findings["differences"][0]["values"],
            {"alpha.legal_name": 500, "beta.legal_name": 200},
        )


CORRECTION_POINTER = "resources[name=beta].schema.fields[name=legal_name].constraints.maxLength"


class ACorrectionNamesTheDefectAndClosesNothing(ScratchTree):
    """The state the reasons file could not express, and the one it must not become.

    A reason says a difference is legitimate and closes it. A correction says it is a
    defect and closes NOTHING — the difference is still counted, still printed, and
    `--strict` still fails on it. Every case here drives that asymmetry, because a
    correction that quietened the check would be a reason with a different word on it.
    """

    def differing(self, maxlength: int = 200) -> None:
        moved = copy.deepcopy(BETA)
        moved["resources"][0]["schema"]["fields"][0]["constraints"]["maxLength"] = maxlength
        self.write(ALPHA)
        self.write(moved)

    def test_the_difference_is_reported_and_still_counted(self) -> None:
        self.differing()
        self.agreement(corrections=[correction()])
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_OK, self.both(result))
        self.assertIn("constraints.maxLength differs [correction declared]", result.stdout)
        self.assertIn("correction: beta.legal_name declares 200 and must become 500", result.stdout)
        self.assertIn("1 carrying a declared correction and still disagreeing", result.stdout)
        self.assertIn("0 with no stated reason", result.stdout)
        self.assertIn("--strict still fails on it", result.stdout)

    def test_strict_still_fails_on_a_difference_a_correction_adjudicated(self) -> None:
        """The one property that separates a correction from a reason."""
        self.differing()
        self.agreement(corrections=[correction()])
        strict = self.run_check("--strict")
        self.assertEqual(strict.returncode, EXIT_DISAGREEMENT, self.both(strict))
        self.assertIn("FAILED under --strict", strict.stderr)
        self.assertIn("1 of them adjudicated by a correction", strict.stderr)

    def test_a_correction_reddens_when_the_declaration_moves(self) -> None:
        self.differing()
        self.agreement(corrections=[correction()])
        self.assertEqual(self.run_check().returncode, EXIT_OK)

        self.differing(300)
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_DISAGREEMENT, self.both(result))
        self.assertIn("1 declared correction(s) no longer describe", result.stderr)
        self.assertIn("it pins 200", result.stderr)
        self.assertIn("the package declares 300", result.stderr)
        self.assertIn("moved:", result.stderr)

    def test_a_correction_reddens_when_the_fix_lands(self) -> None:
        """The property a note in a PR body does not have: it expires by being done."""
        self.differing()
        self.agreement(corrections=[correction()])
        self.assertEqual(self.run_check().returncode, EXIT_OK)

        self.differing(500)  # the corrected value — alpha and beta now agree
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_DISAGREEMENT, self.both(result))
        self.assertIn("landed:", result.stderr)
        self.assertIn("the package now declares what this entry asked for", result.stderr)
        self.assertIn("delete it, the defect is gone", result.stderr)


class ACorrectionReachesWhatTheComparisonCannot(ScratchTree):
    """The two instances that are not a difference between two constraints.

    A description corrected in one descriptor and copy-pasted uncorrected into its
    sibling, and a join whose cardinality one surface reads differently. Neither is
    visible to a comparison of `constraints` across a label group — the first because
    descriptions are deliberately never compared, the second because it is not a field
    property at all — so a mechanism that could only pin a compared property would leave
    both exactly as unaccounted for as writing nothing down.
    """

    def test_a_description_is_pinnable_though_it_is_never_compared(self) -> None:
        alpha = copy.deepcopy(ALPHA)
        beta = copy.deepcopy(BETA)
        beta["resources"][0]["schema"]["fields"][0]["description"] = (
            "Always an ISO 3166-1 alpha-2 country code."
        )
        self.write(alpha)
        self.write(beta)
        self.agreement(
            corrections=[
                correction(
                    pointer="resources[name=beta].schema.fields[name=legal_name].description",
                    declares_contains="Always an ISO 3166-1 alpha-2 country code",
                    drop=("declares", "becomes"),
                )
            ]
        )
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_OK, self.both(result))
        # The comparison still does not see it, and the pin still holds it.
        self.assertNotIn("description differs", result.stdout)
        self.assertIn("declares text containing 'Always an ISO 3166-1", result.stdout)

    def test_the_pinned_phrase_going_away_is_what_landing_looks_like(self) -> None:
        alpha = copy.deepcopy(ALPHA)
        beta = copy.deepcopy(BETA)
        beta["resources"][0]["schema"]["fields"][0]["description"] = "Hedged, and correct."
        self.write(alpha)
        self.write(beta)
        self.agreement(
            corrections=[
                correction(
                    pointer="resources[name=beta].schema.fields[name=legal_name].description",
                    declares_contains="Always an ISO 3166-1 alpha-2 country code",
                    drop=("declares", "becomes"),
                )
            ]
        )
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_DISAGREEMENT, self.both(result))
        self.assertIn("landed:", result.stderr)
        self.assertIn("the pinned phrase is gone", result.stderr)

    def test_a_package_level_declaration_no_field_grouping_could_address(self) -> None:
        alpha = copy.deepcopy(ALPHA)
        beta = copy.deepcopy(BETA)
        beta["x-joins"] = {"joins": [{"name": "alpha", "cardinality": "partial — most miss"}]}
        self.write(alpha)
        self.write(beta)
        self.agreement(
            corrections=[
                correction(
                    pointer="x-joins.joins[name=alpha].cardinality",
                    verdict="right",
                    declares_contains="partial",
                    disagrees="A surface outside this repository reads it as confirmed.",
                    drop=("declares", "becomes"),
                )
            ]
        )
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_OK, self.both(result))
        self.assertIn("[right] beta · x-joins.joins[name=alpha].cardinality", result.stdout)
        self.assertIn("A surface outside this repository", result.stdout)

    def test_a_declaration_pinned_as_right_reddens_when_someone_changes_it(self) -> None:
        """The cheap wrong fix: close the disagreement by moving the side that is right."""
        alpha = copy.deepcopy(ALPHA)
        beta = copy.deepcopy(BETA)
        beta["x-joins"] = {"joins": [{"name": "alpha", "cardinality": "confirmed"}]}
        self.write(alpha)
        self.write(beta)
        self.agreement(
            corrections=[
                correction(
                    pointer="x-joins.joins[name=alpha].cardinality",
                    verdict="right",
                    declares_contains="partial",
                    disagrees="A surface outside this repository reads it as confirmed.",
                    drop=("declares", "becomes"),
                )
            ]
        )
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_DISAGREEMENT, self.both(result))
        self.assertIn("gone from a declaration pinned as right", result.stderr)


class APointerNamesAThingAndNotAPosition(ScratchTree):
    """An index would have moved under an insertion and gone on reading as live."""

    def test_a_field_inserted_above_the_target_does_not_move_the_pin(self) -> None:
        moved = copy.deepcopy(BETA)
        moved["resources"][0]["schema"]["fields"][0]["constraints"]["maxLength"] = 200
        moved["resources"][0]["schema"]["fields"].insert(
            0, field("lei", "identifier.entity.lei", constraints={"maxLength": 20})
        )
        self.write(ALPHA)
        self.write(moved)
        self.agreement(corrections=[correction()])
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_OK, self.both(result))
        self.assertIn("correction: beta.legal_name declares 200 and must become 500", result.stdout)

    def test_a_pointer_naming_no_field_reddens(self) -> None:
        self.settled()
        self.agreement(
            corrections=[
                correction(
                    pointer="resources[name=beta].schema.fields[name=absent].constraints.maxLength"
                )
            ]
        )
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_DISAGREEMENT, self.both(result))
        self.assertIn("0 element(s) carry that name", result.stderr)

    def test_a_pointer_naming_an_unread_package_reddens(self) -> None:
        self.settled()
        self.agreement(corrections=[correction(package="gamma")])
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_DISAGREEMENT, self.both(result))
        self.assertIn("no datasets/gamma/datapackage.json was read", result.stderr)


class ACorrectionWithoutItsClaimIsRefused(ScratchTree):
    """Each field a correction is refused for is one the entry could not be checked by."""

    def refused(self, entry: dict[str, Any], expected: str) -> None:
        self.settled()
        self.agreement(corrections=[entry])
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_ERROR, self.both(result))
        self.assertIn(expected, result.stderr)

    def test_a_wrong_verdict_with_no_becomes(self) -> None:
        self.refused(correction(drop=("becomes",)), "names no `becomes:`")

    def test_a_becomes_that_changes_nothing(self) -> None:
        self.refused(correction(becomes=200), "the value already declared")

    def test_a_right_verdict_with_no_disagrees(self) -> None:
        self.refused(
            correction(verdict="right", drop=("becomes",)), "names no `disagrees:`"
        )

    def test_a_becomes_on_a_substring_pin(self) -> None:
        self.refused(
            correction(declares_contains="ISO 3166", drop=("declares",)),
            "names a `becomes:` and must not",
        )

    def test_no_blocked_on(self) -> None:
        self.refused(correction(drop=("blocked_on",)), "carries no `blocked_on:`")

    def test_no_reason(self) -> None:
        self.refused(correction(drop=("reason",)), "carries no `reason:`")

    def test_both_pin_forms(self) -> None:
        self.refused(
            correction(declares_contains="200"), "of `declares:`/`declares_contains:`"
        )

    def test_an_index_form_pointer(self) -> None:
        self.refused(
            correction(pointer="resources[0].schema.fields[0].constraints.maxLength"),
            "there is no index form",
        )

    def test_a_pointer_matching_two_elements_names_nothing(self) -> None:
        """Refused rather than resolved by taking the first — a first match is a guess."""
        twinned = copy.deepcopy(BETA)
        twinned["x-joins"] = {
            "joins": [{"name": "alpha", "cardinality": "partial"}, {"name": "alpha"}]
        }
        self.write(ALPHA)
        self.write(twinned)
        self.agreement(
            corrections=[
                correction(
                    pointer="x-joins.joins[name=alpha].cardinality",
                    verdict="right",
                    declares_contains="partial",
                    disagrees="Something outside this repository.",
                    drop=("declares", "becomes"),
                )
            ]
        )
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_DISAGREEMENT, self.both(result))
        self.assertIn("2 element(s) carry that name", result.stderr)


class TheShippedAgreementFileIsRead(unittest.TestCase):
    """The corrections this repository actually ships, held to the packages it ships.

    The cases above run on scratch trees, which is the right altitude for the mechanism
    and the wrong one for the entries. An entry whose pointer named nothing in a real
    descriptor would be a verdict about a declaration that does not exist, and every
    scratch case above would still pass.
    """

    def test_every_shipped_correction_resolves_and_is_live(self) -> None:
        root = Path(__file__).resolve().parent.parent / "datasets"
        if not (root / "label-agreement.json").exists():  # pragma: no cover
            self.skipTest("no shipped agreement file")
        result = subprocess.run(
            [sys.executable, str(CHECK), "--datasets-dir", str(root)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, EXIT_OK, result.stdout + result.stderr)
        self.assertNotIn("no longer describe", result.stderr)
        entries = json.loads((root / "label-agreement.json").read_text())["corrections"]
        self.assertTrue(entries, "the file ships no corrections")
        for entry in entries:
            self.assertIn(f"· {entry['pointer']}", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
