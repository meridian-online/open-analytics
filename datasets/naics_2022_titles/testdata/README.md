# Test fixtures for `census/naics_2022_titles`

Cut-down copies of the two Census workbooks the Protocol fetches, downloaded on 2026-11-17 and cut with `openpyxl`, for the tests in `../tests.yaml`. Each keeps Census's sheet name, its header row and its rows exactly as Census stores them, codes as numbers and text with its spaces; only rows were removed.

| Fixture | Stands in for | Kept |
|---|---|---|
| `naics_index.xlsx` | `fetch_index`, Census's `2022_NAICS_Index_File.xlsx` | 19 entries: 111110 (1), 111120 (all 8), 221114 (all 3, one of them ending in spaces), 621399 (4, among them one with a curly apostrophe and one with a double space), 813410 (3, among them `Veterans'  membership organizations`); and three of Census's 25 `******` cross-references |
| `naics_descriptions.xlsx` | `fetch_naics`, Census's `2022_NAICS_Descriptions.xlsx` | 28 rows: those five national industries and every code above them, the sector range 31-33, and 541120 with its ancestors, a national industry with no index entries |
