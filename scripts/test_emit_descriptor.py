#!/usr/bin/env python3
"""Self-test for emit_descriptor.py, against a scratch DuckLake and a scratch manifest.json."""

from __future__ import annotations

import json
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
            r = s.run("--all", "--bundle", str(bundle))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("not yet published: bls.soc_2018", r.stderr)
            self.assertEqual(list(json.loads(bundle.read_text())), ["census.fips"])


if __name__ == "__main__":
    unittest.main()
