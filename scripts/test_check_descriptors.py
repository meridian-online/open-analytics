#!/usr/bin/env python3
"""Self-test for check_descriptors.py — proves the gate can fail, and on what.

Every case here builds a scratch dataset tree (a real Parquet file and a real
`datapackage.json` beside it), runs the checker as a subprocess, and asserts BOTH
the exit status AND the message. Status alone cannot tell a refusal apart from a
crash, which is why the checker distinguishes 1 (disagreement) from 2 (could not
check) and why every case below pins the number it expects.

A rule that no case reddens is a rule nobody could notice going missing, so the
cases below exist to be reddened by deleting the rule they cover. That coverage is
not complete, and the gaps are named rather than implied: deleting any of the
foreign-key resource-ambiguity, arity-mismatch or unknown-local-field branches, the
no-schema-fields branch, or the branch that turns a failed primary-key scan into
`could not check`, leaves this suite green. Each of those five behaves correctly in
the shipped checker — the gap is here, not there.

The primary-key group carries a rule that shipped blind: `primaryKey` appeared in
every published descriptor and in none of this checker, so a key over a column the
Parquet did not have passed. Those cases assert the counts, not just the exit
status, because a key is a claim about values and a count is the only part of the
message that could not have been written without reading them.

The catalogue group covers the table on the front page — the first quantitative
claim a stranger meets about these datasets, and the one nothing had ever read. It
is not enough there to redden on a wrong figure: the check reads text, and text can
be edited, so those cases also assert that blanking the cell, deleting the row and
renaming the column each redden rather than pass.

Three of them are about how a table row is divided into cells, which is the part of
that group that failed. A row carrying `\|` inside one cell and one column fewer than
its header splits, under a divider that treats every `|` as a delimiter, into exactly
the number of cells the header wants — and the figure that lands in the Rows slot is
read out of the neighbouring cell. The checker agreed with a number no reader of the
rendered table is shown. The case is built here rather than described, so the split
staying escape-aware is pinned by a test rather than by a comment.

The last group covers the self-described form: a Parquet that carries its own
descriptor in its footer, which is what a consumer holding only an object URL
actually reads. Those cases matter for a reason the others do not share — once the
footer is the surface people see, a check that kept reading only the file in
`datasets/` would go on passing while that surface drifted. So they stamp a
deliberately wrong description into a real footer and assert the checker names it.

No case touches the network: every fixture resource is a relative path next to its
descriptor, so the self-test runs offline and cannot be flaky for a reason that has
nothing to do with the check.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import stamp_descriptor as sd  # noqa: E402 - found beside this file

CHECKER = Path(__file__).with_name("check_descriptors.py")

EXIT_OK = 0
EXIT_VIOLATIONS = 1
EXIT_ERROR = 2


def write_parquet(target: Path, select_sql: str) -> int:
    """Write the fixture and return how many rows landed in it."""
    import duckdb

    target.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"COPY ({select_sql}) TO '{target}' (FORMAT parquet)")
    rows = con.execute("SELECT count(*) FROM read_parquet(?)", [str(target)]).fetchone()[0]
    con.close()
    return int(rows)


def write_catalogue_row(root: Path, slug: str, stated: str) -> None:
    """Add one row to the scratch README's catalogue, starting the table if needed.

    The checker locates the table by its `Rows` column and ties each row to a package
    through the descriptor it links to, so this fixture only has to carry those two
    things — the other columns exist to keep it a plausible table rather than a
    minimal one the real README could not be.
    """
    readme = root.parent / "README.md"
    if not readme.exists():
        readme.write_text(
            "# scratch catalogue\n\n## Datasets\n\n"
            "| Dataset | Rows | License | Descriptor |\n|---|---|---|---|\n",
            encoding="utf-8",
        )
    with readme.open("a", encoding="utf-8") as handle:
        handle.write(f"| {slug} | {stated} | CC0-1.0 | [datapackage.json]({root.name}/{slug}/datapackage.json) |\n")


def write_package(
    root: Path,
    slug: str,
    *,
    select_sql: str,
    fields: list[dict[str, Any]],
    primary_key: Any = None,
    foreign_keys: list[dict[str, Any]] | None = None,
    declared_bytes: int | None = None,
    resource_path: str | None = None,
    resource_name: str | None = None,
    catalogue: str | bool = True,
) -> Path:
    """Lay down datasets/<slug>/{<slug>.parquet, datapackage.json} and return the descriptor.

    `catalogue` decides what the scratch README says about this package: True states
    the count the fixture actually holds, a string states that figure verbatim, and
    False leaves the package out of the catalogue altogether. It defaults to True so
    every case in this file carries a catalogue that agrees with its data, and only
    the cases about the catalogue have to think about it.
    """
    package_dir = root / slug
    parquet = package_dir / f"{slug}.parquet"
    rows = write_parquet(parquet, select_sql)

    schema: dict[str, Any] = {"fields": fields}
    if primary_key is not None:
        schema["primaryKey"] = primary_key
    if foreign_keys is not None:
        schema["foreignKeys"] = foreign_keys
    resource: dict[str, Any] = {
        "name": resource_name or slug,
        "format": "parquet",
        "path": resource_path if resource_path is not None else parquet.name,
        "schema": schema,
    }
    if declared_bytes is not None:
        resource["bytes"] = declared_bytes

    descriptor = package_dir / "datapackage.json"
    descriptor.write_text(
        json.dumps({"name": slug, "title": slug, "resources": [resource]}, indent=2) + "\n",
        encoding="utf-8",
    )
    if catalogue is not False:
        write_catalogue_row(root, slug, f"{rows:,}" if catalogue is True else catalogue)
    return descriptor


def stamp_package(descriptor: Path, *, text: str | None = None) -> None:
    """Put a description into the fixture's Parquet footer, in place.

    `text` defaults to the honest one — this fixture's own descriptor with the two
    keys a file cannot state about itself removed. Passing anything else is how a
    case builds an object that lies about its own contents, which is the only way to
    find out whether the checker would notice.
    """
    document = json.loads(descriptor.read_text(encoding="utf-8"))
    parquet = descriptor.parent / document["resources"][0]["path"]
    con = sd.connect()
    staged = parquet.with_suffix(".stamped")
    sd.stamp_parquet(con, parquet, staged, sd.embedded_text(document) if text is None else text)
    os.replace(staged, parquet)


def run_check(datasets_dir: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECKER), "--datasets-dir", str(datasets_dir), *extra],
        capture_output=True,
        text=True,
        check=False,
    )


class DescriptorCheckSelfTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="descriptor-check-")
        self.addCleanup(self._tmp.cleanup)
        self.datasets = Path(self._tmp.name) / "datasets"
        self.datasets.mkdir(parents=True)

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

    # ---------------------------------------------------------------- baseline

    def test_conformant_descriptor_passes(self) -> None:
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT * FROM (VALUES ('AB', 'alpha'), ('CD', 'beta')) t(code, label)",
            fields=[
                {"name": "code", "type": "string", "constraints": {"pattern": "^[A-Z]{2}$"}},
                {"name": "label", "type": "string", "constraints": {"minLength": 4, "maxLength": 8}},
            ],
        )
        self.assertOutcome(run_check(self.datasets), EXIT_OK, "agree with their data")

    # ------------------------------------------------------------- constraints

    def test_pattern_violation_names_the_field(self) -> None:
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT * FROM (VALUES ('AB'), ('US-DE'), ('CD')) t(code)",
            fields=[{"name": "code", "type": "string", "constraints": {"pattern": "^[A-Z]{2}$"}}],
        )
        self.assertOutcome(
            run_check(self.datasets),
            EXIT_VIOLATIONS,
            "widgets.code",
            "constraints.pattern",
            "1 of 3 non-null value(s) does not match ^[A-Z]{2}$",
            "'US-DE'",
        )

    def test_min_length_violation_names_the_field(self) -> None:
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT * FROM (VALUES ('alpha'), ('x')) t(label)",
            fields=[{"name": "label", "type": "string", "constraints": {"minLength": 2}}],
        )
        self.assertOutcome(
            run_check(self.datasets),
            EXIT_VIOLATIONS,
            "widgets.label",
            "constraints.minLength",
            "shorter than minLength 2",
        )

    def test_max_length_violation_names_the_field(self) -> None:
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT * FROM (VALUES ('alpha'), ('a-very-long-label')) t(label)",
            fields=[{"name": "label", "type": "string", "constraints": {"maxLength": 8}}],
        )
        self.assertOutcome(
            run_check(self.datasets),
            EXIT_VIOLATIONS,
            "widgets.label",
            "constraints.maxLength",
            "longer than maxLength 8",
        )

    def test_enum_violation_names_the_field(self) -> None:
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT * FROM (VALUES ('sector'), ('galaxy')) t(level)",
            fields=[{"name": "level", "type": "string", "constraints": {"enum": ["sector", "subsector"]}}],
        )
        self.assertOutcome(
            run_check(self.datasets),
            EXIT_VIOLATIONS,
            "widgets.level",
            "constraints.enum",
            "outside enum of 2 member(s)",
            "'galaxy'",
        )

    def test_required_violation_names_the_field(self) -> None:
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT * FROM (VALUES ('alpha'), (NULL)) t(label)",
            fields=[{"name": "label", "type": "string", "constraints": {"required": True}}],
        )
        self.assertOutcome(
            run_check(self.datasets), EXIT_VIOLATIONS, "widgets.label", "constraints.required"
        )

    def test_required_violation_states_an_arithmetically_consistent_count_and_total(self) -> None:
        """`2 of 1 non-null value(s)` was the bug: the numerator counts nulls and a
        `count(col)` denominator excludes exactly the rows being counted, so the two
        numbers could not describe the same population. Two nulls among three rows
        must be reported as `2 of 3`, never `2 of 1`."""
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT * FROM (VALUES ('alpha'), (NULL), (NULL)) t(label)",
            fields=[{"name": "label", "type": "string", "constraints": {"required": True}}],
        )
        result = run_check(self.datasets)
        self.assertOutcome(
            result,
            EXIT_VIOLATIONS,
            "widgets.label",
            "constraints.required",
            "2 of 3 row(s) missing though declared required",
        )
        self.assertNotIn("2 of 1", result.stdout + result.stderr)

    def test_unusable_pattern_is_reported_not_skipped(self) -> None:
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT * FROM (VALUES ('AB')) t(code)",
            fields=[{"name": "code", "type": "string", "constraints": {"pattern": "^[A-Z"}}],
        )
        self.assertOutcome(
            run_check(self.datasets),
            EXIT_VIOLATIONS,
            "widgets.code",
            "is not a usable regular expression",
        )

    def test_unusable_pattern_does_not_swallow_the_field_s_other_constraints(self) -> None:
        """A field whose `pattern` cannot compile must still have `maxLength` (and
        every other declared constraint) evaluated over the same rows — the bad regex
        is one disagreement, not licence to stop checking the field."""
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT * FROM (VALUES ('ABCDE')) t(code)",
            fields=[
                {
                    "name": "code",
                    "type": "string",
                    "constraints": {"pattern": "^[A-Z", "maxLength": 3},
                }
            ],
        )
        self.assertOutcome(
            run_check(self.datasets),
            EXIT_VIOLATIONS,
            "widgets.code",
            "is not a usable regular expression",
            "longer than maxLength 3",
        )

    # -------------------------------------------------------------------- type

    def test_type_integer_over_varchar_column_fails(self) -> None:
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT * FROM (VALUES ('320193'), ('S000105435')) t(key)",
            fields=[{"name": "key", "type": "integer"}],
        )
        self.assertOutcome(
            run_check(self.datasets),
            EXIT_VIOLATIONS,
            "widgets.key",
            "type:",
            "declared 'integer' is not coercible from Parquet physical type VARCHAR",
        )

    def test_type_string_over_integer_column_fails(self) -> None:
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT * FROM (VALUES (320193), (789019)) t(cik)",
            fields=[{"name": "cik", "type": "string"}],
        )
        self.assertOutcome(
            run_check(self.datasets),
            EXIT_VIOLATIONS,
            "widgets.cik",
            "declared 'string' is not coercible from Parquet physical type INTEGER",
        )

    def test_type_boolean_over_varchar_column_fails(self) -> None:
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT * FROM (VALUES ('ACTIVE'), ('INACTIVE')) t(entity_status)",
            fields=[{"name": "entity_status", "type": "boolean"}],
        )
        self.assertOutcome(
            run_check(self.datasets),
            EXIT_VIOLATIONS,
            "widgets.entity_status",
            "declared 'boolean' is not coercible from Parquet physical type VARCHAR",
        )

    def test_date_column_declared_date_passes(self) -> None:
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT * FROM (VALUES (DATE '2026-08-10')) t(as_of)",
            fields=[
                {
                    "name": "as_of",
                    "type": "date",
                    "constraints": {"pattern": "^\\d{4}-\\d{2}-\\d{2}$", "minLength": 10, "maxLength": 10},
                }
            ],
        )
        self.assertOutcome(run_check(self.datasets), EXIT_OK, "agree with their data")

    # ------------------------------------------------------------- field sets

    def test_declared_field_absent_from_parquet_fails(self) -> None:
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT * FROM (VALUES ('AB')) t(code)",
            fields=[{"name": "code", "type": "string"}, {"name": "ghost", "type": "string"}],
        )
        self.assertOutcome(
            run_check(self.datasets),
            EXIT_VIOLATIONS,
            "widgets.ghost",
            "declared but absent from the Parquet",
        )

    def test_undeclared_parquet_column_fails(self) -> None:
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT * FROM (VALUES ('AB', 'stowaway')) t(code, corpus)",
            fields=[{"name": "code", "type": "string"}],
        )
        self.assertOutcome(
            run_check(self.datasets),
            EXIT_VIOLATIONS,
            "widgets.corpus",
            "present in the Parquet as VARCHAR but undeclared in the descriptor",
        )

    # ----------------------------------------------------------------- bytes

    def test_declared_bytes_mismatch_fails(self) -> None:
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT * FROM (VALUES ('AB')) t(code)",
            fields=[{"name": "code", "type": "string"}],
            declared_bytes=1,
        )
        self.assertOutcome(
            run_check(self.datasets), EXIT_VIOLATIONS, "resource.bytes", "descriptor declares 1 bytes"
        )

    def test_declared_bytes_match_passes(self) -> None:
        descriptor = write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT * FROM (VALUES ('AB')) t(code)",
            fields=[{"name": "code", "type": "string"}],
            declared_bytes=1,
        )
        document = json.loads(descriptor.read_text(encoding="utf-8"))
        parquet = descriptor.parent / document["resources"][0]["path"]
        document["resources"][0]["bytes"] = parquet.stat().st_size
        descriptor.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        self.assertOutcome(run_check(self.datasets), EXIT_OK, "agree with their data")

    # ----------------------------------------------------------- foreign keys

    def test_foreign_key_type_mismatch_across_packages_fails(self) -> None:
        write_package(
            self.datasets,
            "left",
            select_sql="SELECT * FROM (VALUES (320193)) t(cik)",
            fields=[{"name": "cik", "type": "integer"}],
        )
        write_package(
            self.datasets,
            "right",
            select_sql="SELECT * FROM (VALUES ('320193')) t(key)",
            fields=[{"name": "key", "type": "string"}],
            foreign_keys=[{"fields": ["key"], "reference": {"resource": "left", "fields": ["cik"]}}],
        )
        self.assertOutcome(
            run_check(self.datasets),
            EXIT_VIOLATIONS,
            "right.key",
            "schema.foreignKeys",
            "the join is described with mismatched types",
        )

    def test_foreign_key_to_unknown_resource_fails(self) -> None:
        write_package(
            self.datasets,
            "right",
            select_sql="SELECT * FROM (VALUES ('320193')) t(key)",
            fields=[{"name": "key", "type": "string"}],
            foreign_keys=[{"fields": ["key"], "reference": {"resource": "nowhere", "fields": ["cik"]}}],
        )
        self.assertOutcome(
            run_check(self.datasets),
            EXIT_VIOLATIONS,
            "schema.foreignKeys",
            "which no package declares",
        )

    def test_foreign_key_to_undeclared_field_fails(self) -> None:
        write_package(
            self.datasets,
            "left",
            select_sql="SELECT * FROM (VALUES ('320193')) t(cik)",
            fields=[{"name": "cik", "type": "string"}],
        )
        write_package(
            self.datasets,
            "right",
            select_sql="SELECT * FROM (VALUES ('320193')) t(key)",
            fields=[{"name": "key", "type": "string"}],
            foreign_keys=[{"fields": ["key"], "reference": {"resource": "left", "fields": ["absent"]}}],
        )
        self.assertOutcome(
            run_check(self.datasets),
            EXIT_VIOLATIONS,
            "schema.foreignKeys",
            "which that package does not declare",
        )

    def test_matching_foreign_key_passes(self) -> None:
        write_package(
            self.datasets,
            "left",
            select_sql="SELECT * FROM (VALUES ('320193')) t(cik)",
            fields=[{"name": "cik", "type": "string"}],
        )
        write_package(
            self.datasets,
            "right",
            select_sql="SELECT * FROM (VALUES ('320193')) t(key)",
            fields=[{"name": "key", "type": "string"}],
            foreign_keys=[{"fields": ["key"], "reference": {"resource": "left", "fields": ["cik"]}}],
        )
        self.assertOutcome(run_check(self.datasets), EXIT_OK, "agree with their data")

    def test_only_resolves_foreign_keys_against_every_package_not_just_the_selected_one(self) -> None:
        """`--only right` must still see `left` to resolve `right`'s foreign key.

        Loading just the selected package would make `left` invisible and the
        checker would report `right`'s key as referencing a resource `which no
        package declares` — a violation that does not exist, invented by the flag
        rather than found by it.
        """
        write_package(
            self.datasets,
            "left",
            select_sql="SELECT * FROM (VALUES ('320193')) t(cik)",
            fields=[{"name": "cik", "type": "string"}],
        )
        write_package(
            self.datasets,
            "right",
            select_sql="SELECT * FROM (VALUES ('320193')) t(key)",
            fields=[{"name": "key", "type": "string"}],
            foreign_keys=[{"fields": ["key"], "reference": {"resource": "left", "fields": ["cik"]}}],
        )
        result = run_check(self.datasets, "--only", "right")
        self.assertOutcome(result, EXIT_OK, "agree with their data")
        self.assertNotIn("which no package declares", result.stdout + result.stderr)

    def test_only_reports_solely_the_selected_package_s_own_foreign_key_violations(self) -> None:
        """The other half of the contract above: `--only right` resolves against the
        whole tree, but must not go on to REPORT a sibling's own unrelated foreign-key
        violation. `check_foreign_keys` takes the full package dict to resolve
        references and a second, separate argument naming which packages to walk and
        report on — reusing the full dict for both would leak `other`'s genuine,
        unrelated violation into a `--only right` run, and nothing before this pinned
        that the two must stay distinct.
        """
        write_package(
            self.datasets,
            "left",
            select_sql="SELECT * FROM (VALUES ('320193')) t(cik)",
            fields=[{"name": "cik", "type": "string"}],
        )
        write_package(
            self.datasets,
            "right",
            select_sql="SELECT * FROM (VALUES ('320193')) t(key)",
            fields=[{"name": "key", "type": "string"}],
            foreign_keys=[{"fields": ["key"], "reference": {"resource": "left", "fields": ["cik"]}}],
        )
        write_package(
            self.datasets,
            "other",
            select_sql="SELECT * FROM (VALUES ('x')) t(key)",
            fields=[{"name": "key", "type": "string"}],
            foreign_keys=[{"fields": ["key"], "reference": {"resource": "ghost", "fields": ["id"]}}],
        )
        result = run_check(self.datasets, "--only", "right")
        self.assertOutcome(result, EXIT_OK, "agree with their data")
        output = result.stdout + result.stderr
        self.assertNotIn("ghost", output)
        self.assertNotIn("which no package declares", output)

    # ------------------------------------------ could-not-check is not a pass

    def test_missing_resource_file_is_an_error_not_a_verdict(self) -> None:
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT * FROM (VALUES ('AB')) t(code)",
            fields=[{"name": "code", "type": "string"}],
            resource_path="absent.parquet",
        )
        self.assertOutcome(run_check(self.datasets), EXIT_ERROR, "which does not exist")

    def test_unsupported_constraint_is_an_error_not_a_pass(self) -> None:
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT * FROM (VALUES (5)) t(n)",
            fields=[{"name": "n", "type": "integer", "constraints": {"minimum": 10}}],
        )
        self.assertOutcome(
            run_check(self.datasets),
            EXIT_ERROR,
            "does not evaluate",
            "refusing to report the field as conformant",
        )

    def test_unknown_declared_type_is_an_error(self) -> None:
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT * FROM (VALUES ('AB')) t(code)",
            fields=[{"name": "code", "type": "stringy"}],
        )
        self.assertOutcome(run_check(self.datasets), EXIT_ERROR, "declares unknown type 'stringy'")

    def test_malformed_descriptor_is_an_error(self) -> None:
        package_dir = self.datasets / "widgets"
        package_dir.mkdir(parents=True)
        (package_dir / "datapackage.json").write_text("{ not json", encoding="utf-8")
        self.assertOutcome(run_check(self.datasets), EXIT_ERROR, "cannot read descriptor")

    def test_empty_datasets_tree_is_an_error_not_a_pass(self) -> None:
        self.assertOutcome(run_check(self.datasets), EXIT_ERROR, "no datasets/*/datapackage.json")

    # ----------------------------------------------------- the primary key

    def test_primary_key_over_a_column_the_parquet_does_not_have_fails(self) -> None:
        """The first fixture that passed green before this rule existed."""
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT * FROM (VALUES ('AB'), ('CD')) t(code)",
            fields=[{"name": "code", "type": "string"}],
            primary_key=["no_such_column"],
        )
        self.assertOutcome(
            run_check(self.datasets),
            EXIT_VIOLATIONS,
            "widgets.no_such_column",
            "schema.primaryKey:",
            "names this column in the primary key (no_such_column), and the Parquet has no such column",
        )

    def test_primary_key_holding_duplicates_fails_and_counts_the_rows(self) -> None:
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT * FROM (VALUES ('AB'), ('AB'), ('CD')) t(code)",
            fields=[{"name": "code", "type": "string"}],
            primary_key=["code"],
        )
        self.assertOutcome(
            run_check(self.datasets),
            EXIT_VIOLATIONS,
            "widgets.code",
            "schema.primaryKey.duplicate",
            "2 of 3 row(s) share a key value with another row, across 1 repeated value(s)",
            "the commonest on 2 row(s)",
            "e.g. 'AB'",
        )

    def test_primary_key_holding_a_null_fails_and_says_so_separately(self) -> None:
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT * FROM (VALUES ('AB'), ('CD'), (NULL)) t(code)",
            fields=[{"name": "code", "type": "string"}],
            primary_key=["code"],
        )
        result = run_check(self.datasets)
        self.assertOutcome(
            result,
            EXIT_VIOLATIONS,
            "widgets.code",
            "schema.primaryKey.null",
            "1 of 3 row(s) hold a NULL in it, which no key value may",
        )
        self.assertNotIn("primaryKey.duplicate", result.stdout + result.stderr)

    def test_repeated_nulls_are_nulls_and_not_also_duplicates(self) -> None:
        """Two NULLs group together in SQL. Reporting them as a repeated key value
        would bury the finding that matters under a second one that is an artefact."""
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT * FROM (VALUES ('AB'), (NULL), (NULL)) t(code)",
            fields=[{"name": "code", "type": "string"}],
            primary_key=["code"],
        )
        result = run_check(self.datasets)
        self.assertOutcome(
            result,
            EXIT_VIOLATIONS,
            "schema.primaryKey.null",
            "2 of 3 row(s) hold a NULL",
        )
        self.assertNotIn("primaryKey.duplicate", result.stdout + result.stderr)

    def test_a_key_that_is_both_duplicated_and_null_reports_both_disjointly(self) -> None:
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT * FROM (VALUES ('AB'), ('AB'), ('CD'), (NULL)) t(code)",
            fields=[{"name": "code", "type": "string"}],
            primary_key=["code"],
        )
        self.assertOutcome(
            run_check(self.datasets),
            EXIT_VIOLATIONS,
            "schema.primaryKey.null",
            "1 of 4 row(s) hold a NULL",
            "schema.primaryKey.duplicate",
            "2 of 4 row(s) share a key value",
        )

    def test_composite_key_is_evaluated_as_a_tuple_not_column_by_column(self) -> None:
        """Every column repeats; no PAIR does. A per-column check reddens here, and
        would be wrong to."""
        write_package(
            self.datasets,
            "widgets",
            select_sql=(
                "SELECT * FROM (VALUES ('AB', 'x'), ('AB', 'y'), ('CD', 'x')) t(code, tag)"
            ),
            fields=[{"name": "code", "type": "string"}, {"name": "tag", "type": "string"}],
            primary_key=["code", "tag"],
        )
        self.assertOutcome(run_check(self.datasets), EXIT_OK, "agree with their data")

    def test_composite_key_repeated_as_a_whole_fails(self) -> None:
        write_package(
            self.datasets,
            "widgets",
            select_sql=(
                "SELECT * FROM (VALUES ('AB', 'x'), ('AB', 'x'), ('CD', 'y')) t(code, tag)"
            ),
            fields=[{"name": "code", "type": "string"}, {"name": "tag", "type": "string"}],
            primary_key=["code", "tag"],
        )
        self.assertOutcome(
            run_check(self.datasets),
            EXIT_VIOLATIONS,
            "widgets.code, tag",
            "schema.primaryKey.duplicate",
            "2 of 3 row(s) share a key value",
            "e.g. ('AB', 'x')",
        )

    def test_composite_key_with_one_null_part_is_an_incomplete_key(self) -> None:
        write_package(
            self.datasets,
            "widgets",
            select_sql=(
                "SELECT * FROM (VALUES ('AB', 'x'), ('CD', NULL)) t(code, tag)"
            ),
            fields=[{"name": "code", "type": "string"}, {"name": "tag", "type": "string"}],
            primary_key=["code", "tag"],
        )
        self.assertOutcome(
            run_check(self.datasets),
            EXIT_VIOLATIONS,
            "schema.primaryKey.null",
            "1 of 2 row(s) hold a NULL",
            "e.g. ('CD', NULL)",
        )

    def test_primary_key_the_data_honours_passes(self) -> None:
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT * FROM (VALUES ('AB'), ('CD')) t(code)",
            fields=[{"name": "code", "type": "string"}],
            primary_key=["code"],
        )
        self.assertOutcome(run_check(self.datasets), EXIT_OK, "agree with their data")

    def test_primary_key_written_as_a_bare_field_name_is_read_not_skipped(self) -> None:
        """Frictionless allows `"primaryKey": "code"`. Reading only the array form
        would let the string form through unchecked."""
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT * FROM (VALUES ('AB'), ('AB')) t(code)",
            fields=[{"name": "code", "type": "string"}],
            primary_key="code",
        )
        self.assertOutcome(
            run_check(self.datasets),
            EXIT_VIOLATIONS,
            "schema.primaryKey.duplicate",
            "2 of 2 row(s) share a key value",
        )

    def test_primary_key_of_an_unreadable_shape_is_an_error_not_a_pass(self) -> None:
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT * FROM (VALUES ('AB'), ('AB')) t(code)",
            fields=[{"name": "code", "type": "string"}],
            primary_key=7,
        )
        self.assertOutcome(
            run_check(self.datasets),
            EXIT_ERROR,
            "declares primaryKey 7",
            "neither a field name nor a non-empty array of field names",
        )

    def test_empty_primary_key_is_an_error_not_a_pass(self) -> None:
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT * FROM (VALUES ('AB'), ('AB')) t(code)",
            fields=[{"name": "code", "type": "string"}],
            primary_key=[],
        )
        self.assertOutcome(
            run_check(self.datasets),
            EXIT_ERROR,
            "declares primaryKey []",
            "neither a field name nor a non-empty array of field names",
        )

    def test_primary_key_naming_something_that_is_not_a_field_is_an_error(self) -> None:
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT * FROM (VALUES ('AB'), ('AB')) t(code)",
            fields=[{"name": "code", "type": "string"}],
            primary_key=["code", 7],
        )
        self.assertOutcome(
            run_check(self.datasets),
            EXIT_ERROR,
            "in which 7 is not a field name",
        )

    def test_primary_key_report_carries_the_rule_and_the_columns(self) -> None:
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT * FROM (VALUES ('AB'), ('AB'), (NULL)) t(code)",
            fields=[{"name": "code", "type": "string"}],
            primary_key=["code"],
        )
        report = Path(self._tmp.name) / "report.json"
        self.assertOutcome(run_check(self.datasets, "--json", str(report)), EXIT_VIOLATIONS)
        document = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(
            sorted((v["field"], v["rule"]) for v in document["violations"]),
            [("code", "schema.primaryKey.duplicate"), ("code", "schema.primaryKey.null")],
        )

    # ----------------------------------------------- the self-described form

    def test_object_carrying_its_own_true_description_passes(self) -> None:
        descriptor = write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT * FROM (VALUES ('AB', 'alpha'), ('CD', 'beta')) t(code, label)",
            fields=[
                {"name": "code", "type": "string", "constraints": {"pattern": "^[A-Z]{2}$"}},
                {"name": "label", "type": "string"},
            ],
        )
        stamp_package(descriptor)
        self.assertOutcome(
            run_check(self.datasets),
            EXIT_OK,
            "agree with their data",
            "1 of 1 resource(s) carry their own description",
        )

    def test_object_describing_itself_with_a_type_its_column_contradicts_fails(self) -> None:
        """The card's case: a dataset whose self-description disagrees with its own contents."""
        descriptor = write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT * FROM (VALUES ('320193'), ('S000105435')) t(key)",
            fields=[{"name": "key", "type": "string"}],
        )
        document = json.loads(descriptor.read_text(encoding="utf-8"))
        document["resources"][0]["schema"]["fields"][0]["type"] = "integer"
        stamp_package(descriptor, text=json.dumps(document, indent=2) + "\n")
        self.assertOutcome(
            run_check(self.datasets),
            EXIT_VIOLATIONS,
            "widgets.key",
            "self-description.type",
            "declares 'integer', which is not coercible from the Parquet physical type VARCHAR",
        )

    def test_object_describing_a_field_it_does_not_have_fails(self) -> None:
        descriptor = write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT * FROM (VALUES ('AB')) t(code)",
            fields=[{"name": "code", "type": "string"}],
        )
        document = json.loads(descriptor.read_text(encoding="utf-8"))
        document["resources"][0]["schema"]["fields"].append({"name": "ghost", "type": "string"})
        stamp_package(descriptor, text=json.dumps(document, indent=2) + "\n")
        self.assertOutcome(
            run_check(self.datasets),
            EXIT_VIOLATIONS,
            "widgets.ghost",
            "self-description.schema.fields",
            "the object has no such column",
        )

    def test_object_hiding_a_column_from_its_own_description_fails(self) -> None:
        descriptor = write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT * FROM (VALUES ('AB', 'stowaway')) t(code, corpus)",
            fields=[{"name": "code", "type": "string"}, {"name": "corpus", "type": "string"}],
        )
        document = json.loads(descriptor.read_text(encoding="utf-8"))
        document["resources"][0]["schema"]["fields"] = [
            field for field in document["resources"][0]["schema"]["fields"] if field["name"] != "corpus"
        ]
        stamp_package(descriptor, text=json.dumps(document, indent=2) + "\n")
        self.assertOutcome(
            run_check(self.datasets),
            EXIT_VIOLATIONS,
            "widgets.corpus",
            "self-description.schema.fields",
            "its own description does not declare it",
        )

    def test_object_and_repository_disagreeing_about_the_licence_fails(self) -> None:
        """Not every drift is visible in the columns. A licence is not, and is the point."""
        descriptor = write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT * FROM (VALUES ('AB')) t(code)",
            fields=[{"name": "code", "type": "string"}],
        )
        document = json.loads(descriptor.read_text(encoding="utf-8"))
        document["licenses"] = [{"name": "CC-BY-4.0"}]
        stamp_package(descriptor, text=json.dumps(document, indent=2) + "\n")
        document["licenses"] = [{"name": "CC0-1.0"}]
        descriptor.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        self.assertOutcome(
            run_check(self.datasets),
            EXIT_VIOLATIONS,
            "self-description",
            "differs from this repository's at /licenses/0/name",
            "'CC0-1.0'",
            "'CC-BY-4.0'",
        )

    def test_object_stating_its_own_size_fails(self) -> None:
        descriptor = write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT * FROM (VALUES ('AB')) t(code)",
            fields=[{"name": "code", "type": "string"}],
        )
        document = json.loads(descriptor.read_text(encoding="utf-8"))
        document["resources"][0]["bytes"] = 12345
        stamp_package(descriptor, text=json.dumps(document, indent=2) + "\n")
        self.assertOutcome(
            run_check(self.datasets),
            EXIT_VIOLATIONS,
            "self-description",
            "states its own 'bytes' inside itself",
        )

    def test_object_carrying_a_footer_that_is_not_json_fails(self) -> None:
        descriptor = write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT * FROM (VALUES ('AB')) t(code)",
            fields=[{"name": "code", "type": "string"}],
        )
        stamp_package(descriptor, text="{ not json")
        self.assertOutcome(
            run_check(self.datasets),
            EXIT_VIOLATIONS,
            "self-description",
            "that is not JSON",
        )

    def test_object_declaring_a_key_over_a_column_it_does_not_have_fails(self) -> None:
        """The copy in the footer is what a consumer holding only a URL reads, so it
        is held to the primary-key rule in its own right and names itself as the
        speaker."""
        descriptor = write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT * FROM (VALUES ('AB'), ('CD')) t(code)",
            fields=[{"name": "code", "type": "string"}],
            primary_key=["no_such_column"],
        )
        stamp_package(descriptor)
        self.assertOutcome(
            run_check(self.datasets),
            EXIT_VIOLATIONS,
            "self-description.schema.primaryKey:",
            "the description the object carries names this column in the primary key",
            "the Parquet has no such column",
        )

    def test_object_declaring_a_key_its_own_rows_break_fails(self) -> None:
        descriptor = write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT * FROM (VALUES ('AB'), ('AB'), (NULL)) t(code)",
            fields=[{"name": "code", "type": "string"}],
            primary_key=["code"],
        )
        stamp_package(descriptor)
        self.assertOutcome(
            run_check(self.datasets),
            EXIT_VIOLATIONS,
            "self-description.schema.primaryKey.duplicate",
            "self-description.schema.primaryKey.null",
            "the description the object carries declares (code) the primary key",
        )

    def test_object_carrying_a_key_shape_that_cannot_be_read_is_a_violation_not_a_crash(
        self,
    ) -> None:
        """Unreadable in `datasets/` stops the run; unreadable in a footer is a
        statement the object made about itself, and is reported as one."""
        descriptor = write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT * FROM (VALUES ('AB')) t(code)",
            fields=[{"name": "code", "type": "string"}],
        )
        document = json.loads(descriptor.read_text(encoding="utf-8"))
        document["resources"][0]["schema"]["primaryKey"] = {"field": "code"}
        stamp_package(descriptor, text=json.dumps(document, indent=2) + "\n")
        self.assertOutcome(
            run_check(self.datasets),
            EXIT_VIOLATIONS,
            "self-description.schema.primaryKey:",
            "neither a field name nor a non-empty array of field names",
        )

    def test_object_carrying_a_key_the_repository_copy_does_not_declare_fails(self) -> None:
        """The one a repository-only check cannot see: the file in `datasets/` claims
        no key at all, and the object asserts one its own rows break."""
        descriptor = write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT * FROM (VALUES ('AB'), ('AB')) t(code)",
            fields=[{"name": "code", "type": "string"}],
        )
        document = json.loads(descriptor.read_text(encoding="utf-8"))
        document["resources"][0]["schema"]["primaryKey"] = ["code"]
        stamp_package(descriptor, text=json.dumps(document, indent=2) + "\n")
        result = run_check(self.datasets)
        self.assertOutcome(
            result,
            EXIT_VIOLATIONS,
            "self-description.schema.primaryKey.duplicate",
            "2 of 2 row(s) share a key value",
        )

    def test_object_and_repository_declaring_the_same_honest_key_passes(self) -> None:
        descriptor = write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT * FROM (VALUES ('AB'), ('CD')) t(code)",
            fields=[{"name": "code", "type": "string"}],
            primary_key=["code"],
        )
        stamp_package(descriptor)
        self.assertOutcome(run_check(self.datasets), EXIT_OK, "agree with their data")

    def test_object_without_a_self_description_is_not_a_violation_by_default(self) -> None:
        """Nothing published carries one yet; demanding one would refuse correct data."""
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT * FROM (VALUES ('AB')) t(code)",
            fields=[{"name": "code", "type": "string"}],
        )
        self.assertOutcome(
            run_check(self.datasets),
            EXIT_OK,
            "0 of 1 resource(s) carry their own description",
        )

    def test_require_self_description_reddens_on_an_unstamped_object(self) -> None:
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT * FROM (VALUES ('AB')) t(code)",
            fields=[{"name": "code", "type": "string"}],
        )
        self.assertOutcome(
            run_check(self.datasets, "--require-self-description"),
            EXIT_VIOLATIONS,
            "self-description",
            "carries no datapackage.json in its Parquet footer",
        )

    def test_json_report_names_the_resources_that_describe_themselves(self) -> None:
        descriptor = write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT * FROM (VALUES ('AB')) t(code)",
            fields=[{"name": "code", "type": "string"}],
        )
        stamp_package(descriptor)
        report = Path(self._tmp.name) / "report.json"
        self.assertOutcome(run_check(self.datasets, "--json", str(report)), EXIT_OK)
        document = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(document["self_described"], ["widgets::widgets"])
        self.assertEqual(document["resources"], 1)

    # ------------------------------------------------- the published pathology

    def test_reproduces_the_published_disagreements(self) -> None:
        """The three shapes the published descriptors carried at commit 77ead0d.

        Pinned to that commit, not to "current main": these counts are fixture-local
        and stay true however the published descriptors are later regenerated.

        1. a crosswalk key stored VARCHAR and declared `integer`,
        2. whose foreign key points at a company id declared `string`,
        3. and a column holding ISO-3166-2 subdivisions measured against a
           country-code pattern.
        """
        write_package(
            self.datasets,
            "companies",
            select_sql="SELECT * FROM (VALUES (320193::BIGINT), (789019::BIGINT)) t(cik)",
            fields=[{"name": "cik", "type": "string", "constraints": {"pattern": "^[0-9]+$"}}],
        )
        write_package(
            self.datasets,
            "crosswalk",
            select_sql=(
                "SELECT * FROM (VALUES ('320193', 'US'), ('S000105435', 'US-DE'), ('789019', 'US-CA')) "
                "t(key, jurisdiction)"
            ),
            fields=[
                {"name": "key", "type": "integer", "constraints": {"pattern": "^-?[0-9]+$"}},
                {"name": "jurisdiction", "type": "string", "constraints": {"pattern": "^[A-Z]{2}$"}},
            ],
            foreign_keys=[{"fields": ["key"], "reference": {"resource": "companies", "fields": ["cik"]}}],
        )
        result = run_check(self.datasets)
        self.assertOutcome(
            result,
            EXIT_VIOLATIONS,
            "companies.cik",
            "declared 'string' is not coercible from Parquet physical type BIGINT",
            "crosswalk.key",
            "declared 'integer' is not coercible from Parquet physical type VARCHAR",
            "1 of 3 non-null value(s) does not match ^-?[0-9]+$",
            "crosswalk.jurisdiction",
            "2 of 3 non-null value(s) does not match ^[A-Z]{2}$",
            "the join is described with mismatched types",
        )

    def test_json_report_lists_every_violation(self) -> None:
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT * FROM (VALUES ('AB'), ('US-DE')) t(code)",
            fields=[{"name": "code", "type": "string", "constraints": {"pattern": "^[A-Z]{2}$"}}],
        )
        report = Path(self._tmp.name) / "report.json"
        self.assertOutcome(run_check(self.datasets, "--json", str(report)), EXIT_VIOLATIONS, "widgets.code")
        document = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(
            [(v["field"], v["rule"]) for v in document["violations"]], [("code", "constraints.pattern")]
        )

    # ----------------------------------------------------------- the catalogue
    #
    # The table on the front page is the first quantitative claim a stranger meets
    # about these datasets, and it sits beside links to the bytes. Three of its four
    # row counts had drifted, the worst by a factor of 31, because nothing read it.
    # These cases redden when the reading stops.

    def readme(self) -> Path:
        return self.datasets.parent / "README.md"

    def test_catalogue_figure_matching_the_object_passes(self) -> None:
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT i AS n FROM range(3) t(i)",
            fields=[{"name": "n", "type": "integer"}],
            catalogue="3",
        )
        self.assertOutcome(run_check(self.datasets), EXIT_OK, "1 catalogue row count(s) measured")

    def test_catalogue_figure_understating_the_object_fails(self) -> None:
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT i AS n FROM range(1234) t(i)",
            fields=[{"name": "n", "type": "integer"}],
            catalogue="40",
        )
        self.assertOutcome(
            run_check(self.datasets),
            EXIT_VIOLATIONS,
            "catalogue.rows",
            "states 40",
            "the published object holds 1,234 rows",
        )

    def test_catalogue_figure_off_by_one_fails(self) -> None:
        """The EDGAR case: 10,415 stated against 10,414 published.

        A figure written in full claims every digit it carries, so a tolerance wide
        enough to swallow one row would swallow this and it is a real disagreement.
        """
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT i AS n FROM range(1234) t(i)",
            fields=[{"name": "n", "type": "integer"}],
            catalogue="1,235",
        )
        self.assertOutcome(
            run_check(self.datasets),
            EXIT_VIOLATIONS,
            "catalogue.rows",
            "states 1,235",
            "the published object holds 1,234 rows",
        )

    def test_rounded_figure_right_at_its_own_precision_passes(self) -> None:
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT i AS n FROM range(1234) t(i)",
            fields=[{"name": "n", "type": "integer"}],
            catalogue="1.2K",
        )
        self.assertOutcome(run_check(self.datasets), EXIT_OK, "agree with their data")

    def test_rounded_figure_wrong_at_its_own_precision_fails(self) -> None:
        """The GLEIF case: 3.36M stated against a count that rounds to 3.38M.

        Off by 0.6%, and still a disagreement — the figure is wrong at the precision
        its own author chose to write it to.
        """
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT i AS n FROM range(1234) t(i)",
            fields=[{"name": "n", "type": "integer"}],
            catalogue="1.3K",
        )
        self.assertOutcome(
            run_check(self.datasets),
            EXIT_VIOLATIONS,
            "catalogue.rows",
            "states 1.3K",
            "which is 1.2K at the precision 1.3K is written to",
        )

    def test_rounded_figure_right_at_a_coarser_precision_passes(self) -> None:
        """1K for 1,234 rows is a presentation choice, and the rule does not ban it."""
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT i AS n FROM range(1234) t(i)",
            fields=[{"name": "n", "type": "integer"}],
            catalogue="1K",
        )
        self.assertOutcome(run_check(self.datasets), EXIT_OK, "agree with their data")

    def test_rounded_figure_wrong_at_a_coarser_precision_fails(self) -> None:
        """2K for 1,234 rows is the same order of magnitude and still refused.

        This is the half of the rule that a bare order-of-magnitude tolerance would
        lose: 2K and 1K differ by one character and only one of them is what this
        count rounds to.
        """
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT i AS n FROM range(1234) t(i)",
            fields=[{"name": "n", "type": "integer"}],
            catalogue="2K",
        )
        self.assertOutcome(run_check(self.datasets), EXIT_VIOLATIONS, "catalogue.rows", "states 2K")

    def test_a_figure_rounded_half_up_at_the_boundary_passes(self) -> None:
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT i AS n FROM range(1250) t(i)",
            fields=[{"name": "n", "type": "integer"}],
            catalogue="1.3K",
        )
        self.assertOutcome(run_check(self.datasets), EXIT_OK, "agree with their data")

    def test_a_figure_rounded_down_at_the_boundary_fails(self) -> None:
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT i AS n FROM range(1250) t(i)",
            fields=[{"name": "n", "type": "integer"}],
            catalogue="1.2K",
        )
        self.assertOutcome(
            run_check(self.datasets), EXIT_VIOLATIONS, "catalogue.rows", "which is 1.3K"
        )

    def test_a_figure_the_rule_cannot_read_is_a_disagreement_not_a_skip(self) -> None:
        """Blanking a cell must not be a way to stop it being checked."""
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT i AS n FROM range(3) t(i)",
            fields=[{"name": "n", "type": "integer"}],
            catalogue="—",
        )
        self.assertOutcome(
            run_check(self.datasets),
            EXIT_VIOLATIONS,
            "catalogue.rows",
            "not a row count this check can read",
            "the published object holds 3 rows",
        )

    def test_published_dataset_absent_from_the_catalogue_fails(self) -> None:
        """Nor must deleting the row: the check is driven by the packages, not the text."""
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT i AS n FROM range(3) t(i)",
            fields=[{"name": "n", "type": "integer"}],
        )
        write_package(
            self.datasets,
            "gadgets",
            select_sql="SELECT i AS n FROM range(4) t(i)",
            fields=[{"name": "n", "type": "integer"}],
            catalogue=False,
        )
        self.assertOutcome(
            run_check(self.datasets),
            EXIT_VIOLATIONS,
            "catalogue.missing",
            "gadgets",
            "no catalogue row in README.md links to it",
        )

    def test_catalogue_row_naming_a_descriptor_that_is_not_there_is_an_error(self) -> None:
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT i AS n FROM range(3) t(i)",
            fields=[{"name": "n", "type": "integer"}],
        )
        write_catalogue_row(self.datasets, "ghosts", "7")
        self.assertOutcome(run_check(self.datasets), EXIT_ERROR, "ghosts/datapackage.json")

    def test_a_missing_catalogue_is_an_error_not_a_pass(self) -> None:
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT i AS n FROM range(3) t(i)",
            fields=[{"name": "n", "type": "integer"}],
        )
        self.readme().unlink()
        self.assertOutcome(run_check(self.datasets), EXIT_ERROR, "cannot read the catalogue")

    def test_a_catalogue_without_a_rows_column_is_an_error_not_a_pass(self) -> None:
        """Renaming the column stops the check, so it must stop the run too."""
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT i AS n FROM range(3) t(i)",
            fields=[{"name": "n", "type": "integer"}],
        )
        self.readme().write_text(
            "# scratch\n\n| Dataset | Size | Descriptor |\n|---|---|---|\n"
            "| widgets | 3 | [datapackage.json](datasets/widgets/datapackage.json) |\n",
            encoding="utf-8",
        )
        self.assertOutcome(run_check(self.datasets), EXIT_ERROR, "no table with a 'rows' column")

    def test_the_count_comes_from_the_object_not_from_the_descriptor(self) -> None:
        """A descriptor stating its own row count cannot make the catalogue agree.

        The descriptor here says 9 rows, the catalogue says 9, and the object holds 3.
        A check that took the count from the descriptor would call this conformant.
        """
        descriptor = write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT i AS n FROM range(3) t(i)",
            fields=[{"name": "n", "type": "integer"}],
            catalogue="9",
        )
        document = json.loads(descriptor.read_text(encoding="utf-8"))
        document["resources"][0]["rowCount"] = 9
        descriptor.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        self.assertOutcome(
            run_check(self.datasets),
            EXIT_VIOLATIONS,
            "catalogue.rows",
            "the published object holds 3 rows",
        )

    def test_an_escaped_pipe_cannot_smuggle_a_figure_into_the_rows_column(self) -> None:
        """The bypass: drop a column, escape a pipe, and a naive split still counts four.

        `\\|` is a literal pipe in a cell, not a delimiter. A splitter that divides on
        every `|` reads this row as four cells and finds `3` in the Rows slot, which
        agrees with the object — so the check goes green. Rendered by anything that
        honours the escape it is three cells, and the figure a reader sees against a
        three-row object is 6,570. Agreement on a number nobody is shown is worse than
        a skip, so the row is refused rather than measured.
        """
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT i AS n FROM range(3) t(i)",
            fields=[{"name": "n", "type": "integer"}],
            catalogue=False,
        )
        self.readme().write_text(
            "# scratch\n\n| Dataset | Rows | License | Descriptor |\n|---|---|---|---|\n"
            "| widgets \\| 3 | 6,570 | [datapackage.json](datasets/widgets/datapackage.json) |\n",
            encoding="utf-8",
        )
        self.assertOutcome(run_check(self.datasets), EXIT_ERROR, "3 cell(s) against a header of 4")

    def test_an_escaped_pipe_inside_a_cell_does_not_shift_the_columns(self) -> None:
        """The other direction: an honest cell carrying a pipe still lands in its own column."""
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT i AS n FROM range(3) t(i)",
            fields=[{"name": "n", "type": "integer"}],
            catalogue=False,
        )
        self.readme().write_text(
            "# scratch\n\n| Dataset | Rows | License | Descriptor |\n|---|---|---|---|\n"
            "| widgets \\| gadgets | 3 | CC0-1.0 | [datapackage.json](datasets/widgets/datapackage.json) |\n",
            encoding="utf-8",
        )
        self.assertOutcome(run_check(self.datasets), EXIT_OK, "1 catalogue row count(s) measured")

    def test_an_escaped_backslash_leaves_the_pipe_after_it_delimiting(self) -> None:
        r"""`\\|` is a literal backslash and then a real delimiter, not an escaped pipe.

        The rule has to consume the escape pair rather than react to every backslash,
        or the second backslash here swallows the pipe and the row loses a column.
        """
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT i AS n FROM range(3) t(i)",
            fields=[{"name": "n", "type": "integer"}],
            catalogue=False,
        )
        self.readme().write_text(
            "# scratch\n\n| Dataset | Rows | License | Descriptor |\n|---|---|---|---|\n"
            "| widgets \\\\| 3 | CC0-1.0 | [datapackage.json](datasets/widgets/datapackage.json) |\n",
            encoding="utf-8",
        )
        self.assertOutcome(run_check(self.datasets), EXIT_OK, "1 catalogue row count(s) measured")

    def test_a_row_that_cannot_be_framed_is_not_read_as_a_row(self) -> None:
        """A row missing its closing delimiter is refused, not guessed at.

        The format makes that delimiter optional and this check does not, because a
        row it cannot frame is a row whose columns it cannot trust. Refusing costs a
        catalogue.missing on the package the row was for, which is loud; reading it
        anyway would put the wrong cell in the Rows slot, which is not.
        """
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT i AS n FROM range(3) t(i)",
            fields=[{"name": "n", "type": "integer"}],
            catalogue=False,
        )
        self.readme().write_text(
            "# scratch\n\n| Dataset | Rows | License | Descriptor |\n|---|---|---|---|\n"
            "| widgets | 3 | CC0-1.0 | [datapackage.json](datasets/widgets/datapackage.json)\n",
            encoding="utf-8",
        )
        self.assertOutcome(
            run_check(self.datasets), EXIT_VIOLATIONS, "catalogue.missing", "widgets"
        )

    def test_a_catalogue_figure_over_a_multi_resource_package_is_an_error(self) -> None:
        """One figure cannot name two resources, so the check refuses instead of guessing."""
        descriptor = write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT i AS n FROM range(3) t(i)",
            fields=[{"name": "n", "type": "integer"}],
        )
        document = json.loads(descriptor.read_text(encoding="utf-8"))
        document["resources"].append(dict(document["resources"][0], name="widgets-again"))
        descriptor.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        self.assertOutcome(run_check(self.datasets), EXIT_ERROR, "declares 2 resource(s)")

    def test_write_catalogue_corrects_the_figure_from_the_measurement(self) -> None:
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT i AS n FROM range(1234) t(i)",
            fields=[{"name": "n", "type": "integer"}],
            catalogue="4,000",
        )
        self.assertOutcome(run_check(self.datasets, "--write-catalogue"), EXIT_OK, "4,000 -> 1,234")
        self.assertIn(
            "| widgets | 1,234 | CC0-1.0 | [datapackage.json](datasets/widgets/datapackage.json) |",
            self.readme().read_text(encoding="utf-8"),
        )
        self.assertOutcome(run_check(self.datasets), EXIT_OK, "agree with their data")

    def test_write_catalogue_keeps_the_form_the_figure_was_written_in(self) -> None:
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT i AS n FROM range(1234) t(i)",
            fields=[{"name": "n", "type": "integer"}],
            catalogue="9.9K",
        )
        self.assertOutcome(run_check(self.datasets, "--write-catalogue"), EXIT_OK, "9.9K -> 1.2K")
        self.assertIn("| 1.2K |", self.readme().read_text(encoding="utf-8"))

    def test_only_narrows_the_catalogue_to_the_datasets_it_names(self) -> None:
        write_package(
            self.datasets,
            "widgets",
            select_sql="SELECT i AS n FROM range(3) t(i)",
            fields=[{"name": "n", "type": "integer"}],
        )
        write_package(
            self.datasets,
            "gadgets",
            select_sql="SELECT i AS n FROM range(4) t(i)",
            fields=[{"name": "n", "type": "integer"}],
            catalogue="99",
        )
        self.assertOutcome(run_check(self.datasets, "--only", "widgets"), EXIT_OK, "1 catalogue row")
        self.assertOutcome(run_check(self.datasets), EXIT_VIOLATIONS, "catalogue.rows", "states 99")


if __name__ == "__main__":
    unittest.main(verbosity=2)
