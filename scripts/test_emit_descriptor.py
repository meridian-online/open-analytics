#!/usr/bin/env python3
"""Self-test for emit_descriptor.py, against a scratch DuckLake and a scratch manifest.json."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import duckdb

EMITTER = Path(__file__).with_name("emit_descriptor.py")

SOC_PATH = "https://example.test/bls/soc_2018.parquet"
FIPS_PATH = "https://example.test/census/fips.parquet"
HEX = "ab" * 32


def descriptor(name: str, path: str) -> dict:
    return {
        "name": name,
        "licenses": [{"name": "other-pd"}],
        "resources": [
            {
                "name": name,
                "format": "parquet",
                "path": path,
                "schema": {"fields": [{"name": "code", "type": "string"}]},
            }
        ],
    }


STORED = {
    "census.fips": descriptor("fips", FIPS_PATH),
    "bls.soc_2018": descriptor("soc_2018", SOC_PATH),
}

ENTRIES = {
    "census/fips": {"bytes": 111, "published": "2026-11-16T00:16:54Z", "sha256": HEX},
    "bls/soc_2018": {"bytes": 222, "published": "2026-11-14T10:30:03Z", "sha256": "cd" * 32},
}


def arcform(tables: list[tuple[str, str]]) -> str:
    rows = "".join(
        f"        - table: {t}\n          descriptor: {d}\n" for t, d in tables
    )
    return (
        "name: scratch\nsteps:\n  - name: other\n    op: http_fetch@1\n"
        "  - name: publish\n    op: ducklake_write@1\n    with:\n      tables:\n" + rows
    )


class Scratch:
    """A DuckLake holding STORED, a manifest, and a datasets tree, in one temp dir."""

    def __init__(self, tmp: str, stored=None, entries=None) -> None:
        self.root = Path(tmp)
        stored = STORED if stored is None else stored
        entries = ENTRIES if entries is None else entries
        self.catalog = self.root / "t.ducklake"
        con = duckdb.connect()
        con.execute("INSTALL ducklake; LOAD ducklake")
        con.execute(f"ATTACH 'ducklake:{self.catalog}' AS l (DATA_PATH '{self.root / 'data'}/')")
        con.execute("CREATE SCHEMA l.meta")
        con.execute("CREATE TABLE l.meta.descriptors (table_name VARCHAR, descriptor JSON)")
        for table, d in stored.items():
            con.execute("INSERT INTO l.meta.descriptors VALUES (?, ?)", [table, json.dumps(d)])
        con.execute("DETACH l")
        self.manifest = self.root / "manifest.json"
        self.manifest.write_text(json.dumps({"datasets": entries}))
        self.datasets = self.root / "src" / "datasets"
        for slug, tables in {
            "fips": [("census.fips", "datapackage.json")],
            "soc_2018": [("bls.soc_2018", "soc_2018.datapackage.json")],
        }.items():
            d = self.datasets / slug
            (d / "build").mkdir(parents=True)
            (d / "build" / "leftover.parquet").write_text("x")
            (d / "arcform.yaml").write_text(arcform(tables))
            (d / "descriptor.overrides.json").write_text("{}")
            for _, name in tables:
                (d / name).write_text('{"committed": true}')
            (d / "other.datapackage.json").write_text('{"committed": true}')
        (self.datasets / "label-agreement.json").write_text("{}")

    def run(self, *args: str, catalog=None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                sys.executable,
                str(EMITTER),
                "--catalog",
                catalog or f"ducklake:{self.catalog}",
                "--manifest",
                str(self.manifest),
                "--datasets",
                str(self.datasets),
                *args,
            ],
            capture_output=True,
            text=True,
            check=False,
        )


class Emit(unittest.TestCase):
    def test_single_table_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            r = Scratch(tmp).run("census.fips")
            self.assertEqual(r.returncode, 0, r.stderr)
            got = json.loads(r.stdout)
            entry = ENTRIES["census/fips"]
            want = json.loads(json.dumps(STORED["census.fips"]))
            want["created"] = entry["published"]
            want["resources"][0]["bytes"] = entry["bytes"]
            want["resources"][0]["hash"] = f"sha256:{entry['sha256']}"
            self.assertEqual(got, want)

    def test_missing_manifest_entry_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            s = Scratch(tmp, entries={"bls/soc_2018": ENTRIES["bls/soc_2018"]})
            r = s.run("census.fips")
            self.assertEqual(r.returncode, 1)
            self.assertIn("census.fips", r.stderr)

    def test_unreachable_catalogue_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            r = Scratch(tmp).run("census.fips", catalog="ducklake:/nonexistent/x.ducklake")
            self.assertEqual(r.returncode, 1)
            self.assertIn("/nonexistent/x.ducklake", r.stderr)

    def test_tree_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            s = Scratch(tmp)
            out = Path(tmp) / "out"
            r = s.run("--all", "--tree", str(out))
            self.assertEqual(r.returncode, 0, r.stderr)
            d = out / "datasets"
            self.assertEqual(
                json.loads((d / "fips" / "datapackage.json").read_text())["name"], "fips"
            )
            soc = json.loads((d / "soc_2018" / "soc_2018.datapackage.json").read_text())
            self.assertEqual(soc["created"], ENTRIES["bls/soc_2018"]["published"])
            self.assertTrue((d / "label-agreement.json").is_file())
            self.assertTrue((d / "fips" / "descriptor.overrides.json").is_file())
            self.assertTrue((d / "soc_2018" / "descriptor.overrides.json").is_file())
            self.assertEqual(list(out.rglob("build")), [])

    def test_bypass_edge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            s = Scratch(tmp)
            plain = Path(tmp) / "plain"
            bypass = Path(tmp) / "bypass"
            self.assertEqual(s.run("--all", "--tree", str(plain)).returncode, 0)
            self.assertEqual(s.run("--all", "--tree", str(bypass), "--bypass-edge").returncode, 0)
            p = json.loads((plain / "datasets/fips/datapackage.json").read_text())
            self.assertEqual(p["resources"][0]["path"], FIPS_PATH)
            b = json.loads((bypass / "datasets/fips/datapackage.json").read_text())
            self.assertEqual(b["resources"][0]["path"], f"{FIPS_PATH}?v={HEX[:16]}")
            b = json.loads((bypass / "datasets/soc_2018/soc_2018.datapackage.json").read_text())
            self.assertEqual(b["resources"][0]["path"], f"{SOC_PATH}?v={'cd' * 8}")

    def test_unpublished_table_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            s = Scratch(
                tmp,
                stored={"census.fips": STORED["census.fips"]},
                entries={"census/fips": ENTRIES["census/fips"]},
            )
            out = Path(tmp) / "out"
            bundle = Path(tmp) / "bundle.json"
            r = s.run("--all", "--tree", str(out))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("not yet published: bls.soc_2018", r.stderr)
            self.assertFalse((out / "datasets/soc_2018/soc_2018.datapackage.json").exists())
            self.assertFalse((out / "datasets/soc_2018/other.datapackage.json").exists())
            self.assertEqual(list((out / "datasets/soc_2018").glob("*datapackage.json")), [])
            self.assertTrue((out / "datasets/fips/datapackage.json").is_file())
            r = s.run("--all", "--bundle", str(bundle))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("not yet published: bls.soc_2018", r.stderr)
            self.assertEqual(list(json.loads(bundle.read_text())), ["census.fips"])

    def test_bad_manifest_entries_refused(self) -> None:
        good = ENTRIES["census/fips"]
        cases = {
            "short hash": ({**good, "sha256": HEX[:16]}, "sha256"),
            "prefixed hash": ({**good, "sha256": "sha256:" + HEX}, "sha256"),
            "upper hash": ({**good, "sha256": HEX.upper()}, "sha256"),
            "no bytes": ({k: v for k, v in good.items() if k != "bytes"}, "`bytes`"),
            "no published": ({k: v for k, v in good.items() if k != "published"}, "`published`"),
            "no sha256": ({k: v for k, v in good.items() if k != "sha256"}, "`sha256`"),
        }
        for label, (entry, word) in cases.items():
            with self.subTest(label), tempfile.TemporaryDirectory() as tmp:
                r = Scratch(tmp, entries={"census/fips": entry}).run("census.fips")
                self.assertEqual(r.returncode, 1, r.stderr)
                self.assertIn("census.fips", r.stderr)
                self.assertIn(word, r.stderr)
                self.assertNotIn("Traceback", r.stderr)

    def test_manifest_entry_without_stored_descriptor_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            s = Scratch(tmp, stored={"census.fips": STORED["census.fips"]})
            r = s.run("--all", "--bundle", str(Path(tmp) / "b.json"))
            self.assertEqual(r.returncode, 1)
            self.assertIn("bls.soc_2018", r.stderr)

    def test_single_table_ignores_other_tables_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            s = Scratch(tmp, entries={"census/fips": ENTRIES["census/fips"]})
            r = s.run("census.fips")
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_one_encoding_for_every_mode(self) -> None:
        stored = {"census.fips": descriptor("fips", FIPS_PATH)}
        stored["census.fips"]["description"] = "Census\u2019s codes"
        with tempfile.TemporaryDirectory() as tmp:
            s = Scratch(tmp, stored=stored, entries={"census/fips": ENTRIES["census/fips"]})
            single = s.run("census.fips").stdout
            out = Path(tmp) / "out"
            s.run("--all", "--tree", str(out))
            tree = (out / "datasets/fips/datapackage.json").read_text(encoding="utf-8")
            self.assertEqual(single, tree)
            self.assertIn("Census\u2019s", single.encode().decode())


    def test_single_table_stdout_is_utf8_whatever_the_locale(self) -> None:
        stored = {"census.fips": descriptor("fips", FIPS_PATH)}
        stored["census.fips"]["description"] = "Census\u2019s codes"
        with tempfile.TemporaryDirectory() as tmp:
            s = Scratch(tmp, stored=stored, entries={"census/fips": ENTRIES["census/fips"]})
            out = Path(tmp) / "single.json"
            self.assertEqual(s.run("census.fips", "--out", str(out)).returncode, 0)
            env = {**os.environ, "PYTHONIOENCODING": "ascii", "LC_ALL": "C", "LANG": "C", "PYTHONUTF8": "0"}
            r = subprocess.run(
                [sys.executable, str(EMITTER), "--catalog", f"ducklake:{s.catalog}", "--manifest", str(s.manifest),
                 "--datasets", str(s.datasets), "census.fips"],
                capture_output=True, check=False, env=env,
            )
            self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
            self.assertEqual(r.stdout, out.read_bytes())
            self.assertIn("Census\u2019s".encode("utf-8"), r.stdout)


class LakeBundleFixtures(unittest.TestCase):
    """The lake bundle the tests read is one snapshot, copied into each Protocol whose
    `arc test` fixtures `emit_descriptors`, because a fixture lives in its Protocol's folder.
    A copy regenerated without the others would test that Protocol against another lake."""

    def test_every_protocol_copy_is_the_scripts_copy(self) -> None:
        root = Path(__file__).resolve().parent.parent
        canonical = (root / "scripts" / "testdata" / "lake_descriptors.json").read_bytes()
        copies = sorted((root / "datasets").glob("*/testdata/lake_descriptors.json"))
        self.assertTrue(copies, "no Protocol carries a lake bundle fixture")
        for copy in copies:
            with self.subTest(copy=str(copy.relative_to(root))):
                self.assertEqual(copy.read_bytes(), canonical, "regenerate every copy together")


if __name__ == "__main__":
    unittest.main()
