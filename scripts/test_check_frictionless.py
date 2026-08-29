#!/usr/bin/env python3
"""Self-test for check_frictionless.py — proves the check can fail, and on what.

    uv run --with frictionless==5.19.0 scripts/test_check_frictionless.py

No PEP 723 header, deliberately: `check_descriptors.py` and its self-test carry none
either, for the same reason. A `dependencies =` line here would be a THIRD pin on the
reference implementation — beside the workflow's `FRICTIONLESS_VERSION` and the version
`frictionless-rejections.json` states its entries were measured against — and nothing
would reconcile it. The check itself compares the rejections file against the version
that is actually imported, so the pin that decides is the runtime's.

Every case builds a scratch `datasets/` tree of real `datapackage.json` files, runs
the check as a subprocess, and asserts BOTH the exit status AND the message. Status
alone cannot tell a refusal from a crash, which is why the check separates 1 (a
refusal nothing accounts for, or an entry that no longer describes one) from 2 (an
input that could not be read, or an implementation the entries were not measured
against) and why every case pins the number it expects.

THE MUTATIONS ARE ON THE DESCRIPTORS AND THE ENTRIES, NOT ON THE CHECK. The defect is
a descriptor the reference implementation refuses while every other check passes, so a
case that edited the comparison code would be driving the wrong altitude. Each case
below moves a declaration or an entry and asserts what the report says about it:

  * a constraint put beside a type v2 has no place for it on, with nothing accounting
    for it — the plain refusal, with the package, the pointer AND the note asserted,
    because a report naming only the package cannot be acted on;
  * THE REFUSAL MOVED TO A DIFFERENT FIELD, message unchanged. `frictionless` names no
    field in its note and raises the same sentence for every `date` column, so an entry
    matched on the message alone would go on reading as live after the defect moved.
    The case asserts both halves: the new field is unaccounted for, and the entry on
    the old one is reported as no longer describing anything;
  * THE FIX LANDING, driven by repairing the declaration rather than by editing the
    entry. That is the altitude: the failure being pinned is an entry that goes on
    recording a refusal after the refusal is gone;
  * A DECLARATION MOVING. A field inserted above the pinned one is the mutation — an
    index would have moved under it and gone on reading as live — and its pair renames
    the field, which must be reported rather than silently matching a neighbour;
  * an entry whose notes are a SUBSET of the refusal, which is what a pin narrowed to
    the convenient half would look like;
  * A PACKAGE-LEVEL REFUSAL no field accounts for, reported against the package. An
    unattributed refusal must not read as no refusal, and a check that only walked
    fields would print nothing for it;
  * THE VERSION PIN, driven by moving the stated version rather than the installed one.
    A refusal measured against one implementation and read against another reads
    exactly like one measured now, so the two are reconciled at check time and the
    message names both;
  * every field an entry is refused for — no `reason`, no `blocked_on`, empty `notes`,
    an index-form pointer, the same refusal stated twice — each one a claim the entry
    could not otherwise be checked by;
  * an empty `datasets/` directory, a missing rejections file and an unreadable
    descriptor: the vacuous pass and the partial sweep, each refused by name;
  * THE ENTRIES THIS REPOSITORY SHIPS, run against the descriptors it ships. Every case
    above is a scratch tree, which is the right altitude for the mechanism and the wrong
    one for the entries: a shipped entry pinning a note the reference implementation no
    longer raises would be an accounting of a refusal that does not exist, and every
    scratch case would still pass.

Nothing here touches the network.
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

CHECK = Path(__file__).with_name("check_frictionless.py")

EXIT_OK = 0
EXIT_DISAGREEMENT = 1
EXIT_ERROR = 2

DATE_PATTERN = "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"
NOTE_PATTERN_ON_DATE = 'constraint "pattern" is not supported by type "date"'
NOTE_PATTERN_ON_INTEGER = 'constraint "pattern" is not supported by type "integer"'
NOTE_MINLENGTH_ON_DATE = 'constraint "minLength" is not supported by type "date"'


def field(name: str, type_: str = "string", **extra: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {"name": name, "type": type_, "description": "A column."}
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


def clean(slug: str) -> dict[str, Any]:
    """A package the reference implementation accepts, with two `date` columns."""
    return package(
        slug,
        [
            field("legal_name", constraints={"minLength": 1, "maxLength": 500}),
            field("opened", "date"),
            field("closed", "date"),
        ],
    )


def pointer(slug: str, name: str) -> str:
    return f"resources[name={slug}].schema.fields[name={name}]"


def entry(*, drop: tuple[str, ...] = (), **over: Any) -> dict[str, Any]:
    """A well-formed entry for a `pattern` on `alpha.opened`, before a case breaks it."""
    stated: dict[str, Any] = {
        "package": "alpha",
        "pointer": pointer("alpha", "opened"),
        "notes": [NOTE_PATTERN_ON_DATE],
        "blocked_on": "A regeneration against the corrected engine, and a republish.",
        "reason": "The engine wrote the label's ISO-8601 pattern without routing it by type.",
    }
    stated.update(over)
    for key in drop:
        stated.pop(key, None)
    return stated


class ScratchTree(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="frictionless-selftest-")
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

    def rejections(self, *entries: dict[str, Any], version: str | None = None) -> None:
        document: dict[str, Any] = {
            "frictionless_version": version if version is not None else installed_version(),
            "rejections": list(entries),
        }
        self.write_rejections(document)

    def write_rejections(self, document: Any) -> None:
        (self.datasets / "frictionless-rejections.json").write_text(
            json.dumps(document, indent=2) + "\n", encoding="utf-8"
        )

    def run_check(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(CHECK), "--datasets-dir", str(self.datasets), *extra],
            capture_output=True,
            text=True,
        )

    def both(self, result: subprocess.CompletedProcess[str]) -> str:
        return result.stdout + result.stderr

    def settled(self) -> None:
        """Two packages the reference implementation accepts, and no stated refusals."""
        self.write(clean("alpha"))
        self.write(clean("beta"))
        self.rejections()
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_OK, self.both(result))
        self.assertIn("2 of 2 package(s) accepted", result.stdout)
        self.assertIn("ACCEPTED  alpha", result.stdout)

    def with_pattern(self, slug: str, name: str) -> dict[str, Any]:
        """`clean(slug)` with a `pattern` beside the `date` on `name`."""
        document = clean(slug)
        target = next(
            f for f in document["resources"][0]["schema"]["fields"] if f["name"] == name
        )
        target["constraints"] = {"pattern": DATE_PATTERN}
        return document


_INSTALLED: str | None = None


def installed_version() -> str:
    global _INSTALLED
    if _INSTALLED is None:
        import frictionless

        _INSTALLED = frictionless.__version__
    return _INSTALLED


class ARefusalNothingAccountsFor(ScratchTree):
    """The plain direction: a descriptor the reference implementation will not load."""

    def test_a_pattern_on_a_date_is_reported_with_its_package_pointer_and_note(self) -> None:
        self.settled()
        self.write(self.with_pattern("alpha", "opened"))
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_DISAGREEMENT, self.both(result))
        self.assertIn("REFUSED   alpha", result.stdout)
        self.assertIn(f"{pointer('alpha', 'opened')}: {NOTE_PATTERN_ON_DATE}", result.stdout)
        self.assertIn("no entry in frictionless-rejections.json accounts for it", result.stdout)
        self.assertIn("1 of 2 package(s) accepted", result.stdout)
        self.assertIn("1 fault(s)", result.stdout)

    def test_a_pattern_on_an_integer_is_reported_the_same_way(self) -> None:
        """The `edgar.cik` shape: a hand-forced type its own pattern cannot carry."""
        self.settled()
        self.write(
            package(
                "alpha",
                [field("cik", "integer", constraints={"pattern": "^[0-9]+$"})],
            )
        )
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_DISAGREEMENT, self.both(result))
        self.assertIn(f"{pointer('alpha', 'cik')}: {NOTE_PATTERN_ON_INTEGER}", result.stdout)

    def test_an_accounted_refusal_passes_and_is_still_printed(self) -> None:
        """An entry closes nothing: the refusal goes on being reported."""
        self.settled()
        self.write(self.with_pattern("alpha", "opened"))
        self.rejections(entry())
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_OK, self.both(result))
        self.assertIn(f"{pointer('alpha', 'opened')}: {NOTE_PATTERN_ON_DATE}", result.stdout)
        self.assertIn("1 of 2 package(s) accepted", result.stdout)
        self.assertIn("0 fault(s)", result.stdout)


class TheRefusalChangesUnderTheEntry(ScratchTree):
    """The entry stays exactly as written; what it describes moves out from under it."""

    def test_the_refusal_moving_to_another_field_is_caught_though_the_note_is_identical(
        self,
    ) -> None:
        """`frictionless` names no field, so a pin on the message alone stays green here."""
        self.settled()
        self.write(self.with_pattern("alpha", "opened"))
        self.rejections(entry())
        self.assertEqual(self.run_check().returncode, EXIT_OK)

        # Same note, same package, different field.
        self.write(self.with_pattern("alpha", "closed"))
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_DISAGREEMENT, self.both(result))
        self.assertIn(
            f"alpha:{pointer('alpha', 'closed')} — refused by the reference implementation "
            f"and no entry",
            result.stdout,
        )
        self.assertIn(
            f"alpha:{pointer('alpha', 'opened')} — the entry records a refusal the "
            f"reference implementation no longer raises here",
            result.stdout,
        )
        self.assertIn("2 fault(s)", result.stdout)

    def test_a_wider_refusal_on_the_pinned_field_names_both_sides(self) -> None:
        self.settled()
        widened = self.with_pattern("alpha", "opened")
        target = widened["resources"][0]["schema"]["fields"][1]
        target["constraints"]["minLength"] = 10
        self.write(widened)
        self.rejections(entry())
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_DISAGREEMENT, self.both(result))
        self.assertIn("the entry pins", result.stdout)
        self.assertIn("and the reference implementation now says", result.stdout)
        self.assertIn(NOTE_MINLENGTH_ON_DATE, result.stdout)

    def test_an_entry_pinning_only_half_the_refusal_does_not_close_it(self) -> None:
        """A pin narrowed to the convenient half would licence the rest of the refusal."""
        self.settled()
        widened = self.with_pattern("alpha", "opened")
        widened["resources"][0]["schema"]["fields"][1]["constraints"]["minLength"] = 10
        self.write(widened)
        self.rejections(entry(notes=[NOTE_MINLENGTH_ON_DATE]))
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_DISAGREEMENT, self.both(result))
        self.assertIn("the entry pins", result.stdout)

    def test_the_fix_landing_reddens_until_the_entry_is_deleted(self) -> None:
        """Driven by repairing the declaration, not by editing the entry."""
        self.settled()
        self.write(self.with_pattern("alpha", "opened"))
        self.rejections(entry())
        self.assertEqual(self.run_check().returncode, EXIT_OK)

        self.write(clean("alpha"))  # the republish landed
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_DISAGREEMENT, self.both(result))
        self.assertIn("the reference implementation ACCEPTS alpha now", result.stdout)
        self.assertIn("delete the entry", result.stdout)


class TheDeclarationMoves(ScratchTree):
    """A pointer is addressed by the `name` its element carries, never by index."""

    def test_a_field_inserted_above_the_pinned_one_does_not_move_the_pin(self) -> None:
        self.settled()
        moved = self.with_pattern("alpha", "opened")
        moved["resources"][0]["schema"]["fields"].insert(0, field("inserted"))
        self.write(moved)
        self.rejections(entry())
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_OK, self.both(result))
        self.assertIn("0 fault(s)", result.stdout)

    def test_a_renamed_field_leaves_the_pointer_naming_nothing(self) -> None:
        self.settled()
        renamed = self.with_pattern("alpha", "opened")
        renamed["resources"][0]["schema"]["fields"][1]["name"] = "commenced"
        self.write(renamed)
        self.rejections(entry())
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_DISAGREEMENT, self.both(result))
        self.assertIn("the entry's pointer names nothing in the descriptor", result.stdout)

    def test_an_entry_naming_an_absent_package_is_reported(self) -> None:
        self.settled()
        self.rejections(entry(package="gamma", pointer=pointer("gamma", "opened")))
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_DISAGREEMENT, self.both(result))
        self.assertIn("names a package", result.stdout)


class ARefusalNoFieldAccountsFor(ScratchTree):
    """An unattributed refusal must not read as no refusal."""

    def test_a_package_level_refusal_is_reported_against_the_package(self) -> None:
        self.settled()
        bad = clean("alpha")
        bad["name"] = "Not A Legal Package Name"
        self.write(bad, slug="alpha")
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_DISAGREEMENT, self.both(result))
        self.assertIn("(package): ", result.stdout)
        self.assertIn("alpha: (package) — refused by the reference implementation", result.stdout)

    def test_a_package_level_refusal_can_be_accounted_for_by_an_empty_pointer(self) -> None:
        self.settled()
        bad = clean("alpha")
        bad["name"] = "Not A Legal Package Name"
        self.write(bad, slug="alpha")
        measured = self.run_check()
        note = next(
            line.split("(package): ", 1)[1].strip()
            for line in measured.stdout.splitlines()
            if "(package): " in line
        )
        self.rejections(entry(pointer="", notes=[note]))
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_OK, self.both(result))
        self.assertIn("0 fault(s)", result.stdout)


class TheVersionThisWasMeasuredAgainst(ScratchTree):
    """A refusal measured against another implementation reads like one measured now."""

    def test_a_stated_version_other_than_the_installed_one_is_a_fault(self) -> None:
        self.settled()
        self.write(self.with_pattern("alpha", "opened"))
        self.rejections(entry(), version="4.0.0")
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_ERROR, self.both(result))
        self.assertIn("measured against frictionless 4.0.0", result.stderr)
        self.assertIn(f"frictionless {installed_version()} is installed", result.stderr)

    def test_a_file_stating_no_version_is_a_fault(self) -> None:
        self.settled()
        self.write_rejections({"rejections": []})
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_ERROR, self.both(result))
        self.assertIn("states no `frictionless_version`", result.stderr)


class AnEntryThatCannotBeChecked(ScratchTree):
    """Every field an entry is refused for is a claim it could not be checked by."""

    def refused(self, stated: dict[str, Any], fragment: str) -> None:
        self.settled()
        self.write(self.with_pattern("alpha", "opened"))
        self.rejections(stated)
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_ERROR, self.both(result))
        self.assertIn(fragment, result.stderr)

    def test_no_reason(self) -> None:
        self.refused(entry(drop=("reason",)), "states no `reason`")

    def test_no_blocked_on(self) -> None:
        self.refused(entry(drop=("blocked_on",)), "states no `blocked_on`")

    def test_an_empty_reason(self) -> None:
        self.refused(entry(reason="   "), "`reason` is not a sentence")

    def test_no_notes(self) -> None:
        self.refused(entry(notes=[]), "`notes` is not a non-empty list of strings")

    def test_an_index_form_pointer(self) -> None:
        self.refused(
            entry(pointer="resources[0].schema.fields[1]"),
            "there is no index form",
        )

    def test_an_index_form_pointer_on_an_accepted_package_is_still_a_fault(self) -> None:
        """The case above is decided by the match loop either way; this one is not.

        An entry for a package the reference implementation now ACCEPTS is short-
        circuited before its pointer is ever resolved, so a malformed pointer there is
        seen only by the entry-reading pass. Without that pass this exits 1 saying the
        entry is stale, which is a verdict about an entry nobody can read.
        """
        self.settled()
        self.rejections(entry(pointer="resources[0].schema.fields[1]"))
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_ERROR, self.both(result))
        self.assertIn("there is no index form", result.stderr)

    def test_the_same_refusal_stated_twice(self) -> None:
        self.settled()
        self.write(self.with_pattern("alpha", "opened"))
        self.rejections(entry(), entry())
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_ERROR, self.both(result))
        self.assertIn("is stated twice", result.stderr)

    def test_rejections_that_are_not_a_list(self) -> None:
        self.settled()
        self.write_rejections(
            {"frictionless_version": installed_version(), "rejections": {"alpha": []}}
        )
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_ERROR, self.both(result))
        self.assertIn("`rejections` is not a list", result.stderr)


class TheSweepMustBeWhole(ScratchTree):
    """A pass over nothing, and a pass over some of it, are both faults."""

    def test_an_empty_datasets_directory_is_a_fault_not_a_pass(self) -> None:
        self.rejections()
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_ERROR, self.both(result))
        self.assertIn("which is a fault and not a pass", result.stderr)

    def test_a_missing_rejections_file_is_a_fault(self) -> None:
        self.write(clean("alpha"))
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_ERROR, self.both(result))
        self.assertIn("does not exist", result.stderr)

    def test_an_unreadable_descriptor_is_a_fault(self) -> None:
        self.settled()
        (self.datasets / "alpha" / "datapackage.json").write_text("{ not json", encoding="utf-8")
        result = self.run_check()
        self.assertEqual(result.returncode, EXIT_ERROR, self.both(result))
        self.assertIn("cannot be read as JSON", result.stderr)

    def test_the_findings_are_written_when_asked(self) -> None:
        self.settled()
        self.write(self.with_pattern("alpha", "opened"))
        out = Path(self._tmp.name) / "findings.json"
        result = self.run_check("--json", str(out))
        self.assertEqual(result.returncode, EXIT_DISAGREEMENT, self.both(result))
        findings = json.loads(out.read_text())
        self.assertEqual(findings["accepted"], ["beta"])
        self.assertEqual(
            findings["refused"]["alpha"][pointer("alpha", "opened")],
            [NOTE_PATTERN_ON_DATE],
        )
        self.assertEqual(findings["frictionless_version"], installed_version())


class TheEntriesThisRepositoryShips(unittest.TestCase):
    """The scratch cases above are the right altitude for the mechanism, not the entries.

    A shipped entry pinning a note the reference implementation no longer raises, or a
    refusal nothing accounts for, would leave every case above passing.
    """

    def test_every_shipped_entry_is_live_and_every_refusal_is_accounted_for(self) -> None:
        root = Path(__file__).resolve().parent.parent / "datasets"
        if not (root / "frictionless-rejections.json").exists():  # pragma: no cover
            self.skipTest("no shipped rejections file")
        result = subprocess.run(
            [sys.executable, str(CHECK), "--datasets-dir", str(root)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, EXIT_OK, result.stdout + result.stderr)
        self.assertIn("0 fault(s)", result.stdout)
        document = json.loads((root / "frictionless-rejections.json").read_text())
        for stated in document["rejections"]:
            for note in stated["notes"]:
                self.assertIn(f"{stated['pointer']}: {note}", result.stdout)

    def test_the_edgar_descriptor_is_accepted(self) -> None:
        """The one this repository fixed by hand: `cik` is `integer` and carries no pattern."""
        root = Path(__file__).resolve().parent.parent / "datasets"
        descriptor = root / "edgar" / "datapackage.json"
        if not descriptor.exists():  # pragma: no cover
            self.skipTest("no shipped edgar descriptor")
        document = json.loads(descriptor.read_text())
        cik = next(
            f for f in document["resources"][0]["schema"]["fields"] if f["name"] == "cik"
        )
        self.assertEqual(cik["type"], "integer")
        self.assertNotIn("pattern", cik.get("constraints") or {})
        self.assertEqual(
            cik["x-finetype-unsupported-constraints"]["pattern"],
            "^[0-9]+$",
            "the pattern is carried, not dropped",
        )


class TheCheckRefusesToRunWithoutTheImplementation(unittest.TestCase):
    """`frictionless` absent must be a fault, never a sweep that found nothing."""

    def test_an_unimportable_frictionless_is_exit_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            shim = Path(tmp) / "frictionless.py"
            shim.write_text("raise ImportError('blocked by the self-test')\n")
            env = {
                "PATH": "/usr/bin:/bin",
                "PYTHONPATH": tmp,
                "HOME": tmp,
            }
            result = subprocess.run(
                [sys.executable, str(CHECK), "--datasets-dir", tmp],
                capture_output=True,
                text=True,
                env=env,
            )
        self.assertEqual(result.returncode, EXIT_ERROR, result.stdout + result.stderr)
        self.assertIn("frictionless is not importable", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
