# Test fixtures for `census/naics_2017_to_2022`

Cut-down copies of the two Census workbooks the Protocol fetches, downloaded on 2026-11-17 and cut with `openpyxl`, for the tests in `../tests.yaml`. Each keeps Census's sheet name, its title and header rows and its rows exactly as Census stores them, codes as numbers and text with its spaces; only rows were removed.

| Fixture | Stands in for | Kept |
|---|---|---|
| `naics_2017_to_2022.xlsx` | `fetch_concordance`, Census's `2017_to_2022_NAICS.xlsx` | the title, note and header rows and ten pairings: 111110, 112120 (with Census's cell of whitespace in column E) and 721310 (whose titles end in spaces), one to one; 212111, 212112 and both pieces of 212113, the split that feeds the 212114 and 212115 merges; 334613 and 334614 (whose 2017 title ends in a space), the 334610 merge; and 531120, with Census's `excpet` |
| `naics_descriptions.xlsx` | `fetch_naics`, Census's `2022_NAICS_Descriptions.xlsx` | 30 rows: the seven 2022 codes those pairings name, every code above them, and the sector range 31-33 |
