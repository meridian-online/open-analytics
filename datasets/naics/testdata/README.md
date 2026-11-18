# Test fixtures for `census/naics_2022`

A cut-down copy of the Census workbook the Protocol fetches, downloaded on 2026-11-18 and cut with `openpyxl` by deleting rows from Census's own file, for the tests in `../tests.yaml`. It keeps Census's sheet name, its header row and its rows exactly as Census stores them, codes as numbers, titles with their trilateral `T` and paragraphs with their line breaks; only rows were removed.

| Fixture | Stands in for | Kept |
|---|---|---|
| `naics_descriptions.xlsx` | `fetch_naics`, Census's `2022_NAICS_Descriptions.xlsx` | 37 rows: the 28 of `../../naics_2022_titles/testdata/naics_descriptions.xlsx` (five national industries with index entries, 541120, every code above them, and the sector range 31-33), and the chains 311 to 311111 under 31-33 and 441 to 441110 under 44-45, with the range 44-45 itself. Two industry groups, 3111 and 8134, have no paragraph of their own |

The lake fixtures are the bundle `python scripts/emit_descriptor.py --all --bundle <file>` emitted from the open lake on 2026-11-17, from the repository root, with `census.naics_2022` replaced by the descriptor this Protocol builds on the code-list contract, which the lake has published since snapshot 19. They need regenerating when a table they hold is republished with different declarations:

| Fixture | Stands in for | Kept |
|---|---|---|
| `lake_descriptors.json` | `emit_descriptors`, the bundle of every published table's descriptor | The twelve tables published with a `<slug>/datapackage.json`, the same bundle as `../../naics_2022_titles/testdata/lake_descriptors.json`. None can go: `datasets/label-agreement.json` pins values from every one of them, and a pinned value that goes missing is a stale reason |
| `described.json` | `describe`, in the test that runs the check before publish for real | The descriptor of `census.naics_2022`, as it is in `lake_descriptors.json`. finetype infers a descriptor from the rows it reads, so on the fixture's rows it can label columns differently from the full table, and the label check would refuse that descriptor for a reason that is not a defect |
| `check_before_publish.json` | `check_before_publish`, in the tests that run `describe` on the fixture | A stand-in report, which no step left in a test reads |
