# Test fixtures for `census/naics_2017_to_2022`

Cut-down copies of the two Census workbooks the Protocol fetches, downloaded on 2026-11-17 and cut with `openpyxl`, for the tests in `../tests.yaml`. Each keeps Census's sheet name, its title and header rows and its rows exactly as Census stores them, codes as numbers and text with its spaces; only rows were removed.

| Fixture | Stands in for | Kept |
|---|---|---|
| `naics_2017_to_2022.xlsx` | `fetch_concordance`, Census's `2017_to_2022_NAICS.xlsx` | the title, note and header rows and ten pairings: 111110, 112120 (with Census's cell of whitespace in column E) and 721310 (whose titles end in spaces), one to one; 212111, 212112 and both pieces of 212113, the split that feeds the 212114 and 212115 merges; 334613 and 334614 (whose 2017 title ends in a space), the 334610 merge; and 531120, with Census's `excpet` |
| `naics_descriptions.xlsx` | `fetch_naics`, Census's `2022_NAICS_Descriptions.xlsx` | 30 rows: the seven 2022 codes those pairings name, every code above them, and the sector range 31-33 |

The lake fixtures were emitted from the open lake on 2026-11-17 with `python scripts/emit_descriptor.py --all --bundle <file>`, from the repository root, and need regenerating when a table they hold is republished with different declarations:

| Fixture | Stands in for | Kept |
|---|---|---|
| `lake_descriptors.json` | `emit_descriptors`, the bundle of every published table's descriptor | The twelve tables published with a `<slug>/datapackage.json`, which is every table the label check before publish compares; the six SOC tables, published with per-table descriptors, are left out because the check does not compare them. None of the twelve can go: `datasets/label-agreement.json` pins values from every one of them, and a pinned value that goes missing is a stale reason, which fails the check |
