#!/usr/bin/env python3
"""The Schedule B year is written in four places, and this holds them to one another.

arc does not substitute a `params:` value into an operator's `with:`, so the year folder
appears as the manifest's `year` param, in the two Census URLs the manifest fetches,
and in the descriptor's source for the year's index (its URL and its title). A new
January folder changes all four. This test reads the shipped manifest and overrides
as documents and fails when any Census year folder, or the source's title, names a
year other than the param's default. load.sql refuses at run time an index page whose
title names another year than the param; this catches the files before a run.

Nothing here touches the network.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from typing import Any, Iterator

import yaml

DATASET = Path(__file__).resolve().parent.parent
CENSUS_FOLDER = re.compile(r"https://www\.census\.gov/foreign-trade/schedules/b/([^/]+)/")


def strings(node: Any) -> Iterator[str]:
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from strings(value)


class ProtocolYearTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = yaml.safe_load((DATASET / "arcform.yaml").read_text(encoding="utf-8"))
        cls.overrides = json.loads((DATASET / "descriptor.overrides.json").read_text(encoding="utf-8"))
        cls.year = str(cls.manifest["params"]["year"]["default"])

    def test_the_param_is_a_year(self) -> None:
        self.assertRegex(self.year, r"^\d{4}$")

    def test_both_census_fetches_use_the_params_year(self) -> None:
        urls = [
            step["with"]["url"]
            for step in self.manifest["steps"]
            if step.get("op") == "http_fetch@1" and "census.gov" in step["with"]["url"]
        ]
        self.assertEqual(len(urls), 2, urls)
        for url in urls:
            self.assertIn(f"/schedules/b/{self.year}/", url)

    def test_every_census_year_folder_in_the_manifest_is_the_params(self) -> None:
        folders = [m.group(1) for s in strings(self.manifest) for m in CENSUS_FOLDER.finditer(s)]
        self.assertTrue(folders)
        self.assertEqual(set(folders), {self.year})

    def test_the_descriptors_year_source_is_the_params(self) -> None:
        sources = [s for s in self.overrides["sources"] if CENSUS_FOLDER.search(s["path"])]
        self.assertEqual(len(sources), 1, sources)
        self.assertIn(f"/schedules/b/{self.year}/", sources[0]["path"])
        self.assertEqual(re.findall(r"\b(?:19|20)\d{2}\b", sources[0]["title"]), [self.year])

    def test_no_census_year_folder_elsewhere_in_the_overrides(self) -> None:
        folders = {m.group(1) for s in strings(self.overrides) for m in CENSUS_FOLDER.finditer(s)}
        self.assertEqual(folders, {self.year})


if __name__ == "__main__":
    unittest.main()
