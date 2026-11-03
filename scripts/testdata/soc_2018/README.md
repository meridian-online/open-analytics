# SOC 2018 test fixtures

Cut copies of three workbooks the U.S. Bureau of Labor Statistics publishes at https://www.bls.gov/soc/, downloaded on 2026-11-03. BLS's files are U.S. Government works in the public domain, so the rows are committed as BLS gives them.

- `soc_2018_definitions.xlsx`: the sheet `2018 SOC`, from the 2018 SOC structure with definitions, "November 2017 (for reference year January 2018 - Updated April 15, 2020)".
- `soc_2018_direct_match_title_file.xlsx`: the sheet `Sorted by SOC`, from the direct match title file, "November 2017 (Updated April 15, 2020)".
- `soc_2010_to_2018_crosswalk.xlsx`: the sheet `Sorted by 2010`, from the 2010 to 2018 crosswalk, "November 2017".

Each copy keeps the sheet name and the title block and header rows exactly as BLS prints them (the first 8 rows, or the first 9 in the crosswalk), then only the rows for:

- major groups `11-0000` and `15-0000`;
- minor groups `11-3000`, `11-9000` and `15-1200`;
- every broad group and detailed occupation under those three minor groups.

The crosswalk keeps a row when either its 2010 or its 2018 code is one of those occupations, and the title file keeps every direct-match title of them.
