# Test fixtures for `census/naics_2022_titles`

Cut-down copies of the two Census workbooks the Protocol fetches, downloaded on 2026-11-17 and cut with `openpyxl`, for the tests in `../tests.yaml`. Each keeps Census's sheet name, its header row and its rows exactly as Census stores them, codes as numbers and text with its spaces; only rows were removed.

| Fixture | Stands in for | Kept |
|---|---|---|
| `naics_index.xlsx` | `fetch_index`, Census's `2022_NAICS_Index_File.xlsx` | 19 entries: 111110 (1), 111120 (all 8), 221114 (all 3, one of them ending in spaces), 621399 (4, among them one with a curly apostrophe and one with a double space), 813410 (3, among them `Veterans'  membership organizations`); and three of Census's 25 `******` cross-references |
| `naics_descriptions.xlsx` | `fetch_naics`, Census's `2022_NAICS_Descriptions.xlsx` | 28 rows: those five national industries and every code above them, the sector range 31-33, and 541120 with its ancestors, a national industry with no index entries |

The lake fixtures were emitted from the open lake on 2026-11-17 with `python scripts/emit_descriptor.py --all --bundle <file>`, from the repository root, with `census.naics_2022` replaced on 2026-11-18 by the descriptor its Protocol builds on the code-list contract, which the lake has published since snapshot 19, and need regenerating when a table they hold is republished with different declarations:

| Fixture | Stands in for | Kept |
|---|---|---|
| `lake_descriptors.json` | `emit_descriptors`, the bundle of every published table's descriptor | The twelve tables published with a `<slug>/datapackage.json`, which is every table the label check before publish compares; the six SOC tables, published with per-table descriptors, are left out because the check does not compare them. None of the twelve can go: `datasets/label-agreement.json` pins values from every one of them, and a pinned value that goes missing is a stale reason, which fails the check |
| `described.json` | `describe`, in the test that runs the check before publish for real | The lake's descriptor of `census.naics_2022_titles`, as it is in `lake_descriptors.json`. finetype infers a descriptor from the rows it reads, so on the fixture's rows it labels some columns differently from the full table, and the label check would refuse that descriptor for a reason that is not a defect |
| `check_before_publish.json` | `check_before_publish`, in the tests that run `describe` on the fixture | A stand-in report, which no step left in a test reads |
