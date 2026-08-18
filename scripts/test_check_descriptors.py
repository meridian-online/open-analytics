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
foreign-key resource-ambiguity, arity-mismatch or unknown-local-field branches, or
the no-schema-fields branch, leaves this suite green. Each of those four behaves
correctly in the shipped checker — the gap is here, not there.

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


def write_parquet(target: Path, select_sql: str) -> None:
    import duckdb

    target.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"COPY ({select_sql}) TO '{target}' (FORMAT parquet)")
    con.close()


def write_package(
    root: Path,
    slug: str,
    *,
    select_sql: str,
    fields: list[dict[str, Any]],
    foreign_keys: list[dict[str, Any]] | None = None,
    declared_bytes: int | None = None,
    resource_path: str | None = None,
    resource_name: str | None = None,
) -> Path:
    """Lay down datasets/<slug>/{<slug>.parquet, datapackage.json} and return the descriptor."""
    package_dir = root / slug
    parquet = package_dir / f"{slug}.parquet"
    write_parquet(parquet, select_sql)

    schema: dict[str, Any] = {"fields": fields}
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
