# `bls/soc_2018` — the U.S. Standard Occupational Classification, 2018

The U.S. Bureau of Labor Statistics' 2018 Standard Occupational Classification, the federal statistical standard for classifying workers by occupation, as three tables built from BLS's own workbooks in one Run:

| Table | Address | Job | Rows (2026-11-03) |
|---|---|---|---|
| `soc_2018` | `bls/soc_2018` | code list: every major group, minor group, broad occupation and detailed occupation, with BLS's definitions | 1,447 |
| `soc_2018_titles` | `bls/soc_2018_titles` | job-title index: BLS's direct match titles, each pointing at a detailed occupation | 6,520 |
| `soc_2010_to_2018` | `bls/soc_2010_to_2018` | edition crosswalk: BLS's pairings of 2010 detailed occupations with 2018 ones | 900 |

None of the three is published yet, and none is in `../registry.json`. This directory holds their facets:

| File | Facet | Answers |
|---|---|---|
| `arcform.yaml` (+ `models/`) | **Protocol** — how they're made | run `arc run` → produces the three tables |
| `<table>.datapackage.json` | **Descriptor** — what each is | schema, finetype labels, the table's contract declaration |
| `<table>.overrides.json`, `<table>.nominations.finetype.json`, `<table>.schema.finetype.json` | the descriptor's inputs | the curated half, the declared finetype labels, the drift contract |

## The Protocol

`arc run` (arcform) executes `arcform.yaml`. One **Run** → one version of each table. The step DAG:

1. **fetch** — BLS's three workbooks: the definitions, the direct match title file and the 2010 to 2018 crosswalk.
2. **load** (`models/load.sql`) — check each workbook's header row, read its rows into staging tables, and read the releases from the title blocks.
3. **code_list**, **titles**, **crosswalk** (`models/`) — build each table: the code list with each row's parent and ancestors, the title index with each title's code description, the crosswalk as BLS prints it.
4. **export** — write each table's zstd Parquet to `build/stage/`, ordered by its primary key.
5. **describe** — emit each descriptor from its Parquet, typed by finetype with the table's nominations and overrides applied.
6. **check_code_list** — hold the code list to the shared code-list contract (`scripts/check_code_list.py`, see `docs/code-lists.md`).
7. **check_titles**, **check_crosswalk** — hold the title index and the crosswalk to their contracts (`contracts/check_titles.sql`, `contracts/check_crosswalk.sql`), each after a `check_inputs_*` model points it at the exported table, the code list exported in the same Run, and the level BLS codes to: every title and every 2018 code must be a detailed occupation in that code list.
8. **validate** — gate each Parquet against its `<table>.schema.finetype.json`.
9. **stamp** — write each published `build/<table>.parquet` with its finished descriptor in the footer, and `<table>.datapackage.json` beside the manifest.

Produces `build/soc_2018.parquet`, `build/soc_2018_titles.parquet` and `build/soc_2010_to_2018.parquet`.

## The sources

BLS publishes the 2018 SOC at <https://www.bls.gov/soc/2018/home.htm>, on behalf of the Office of Management and Budget and the Standard Occupational Classification Policy Committee. The Protocol reads three workbooks from it: `soc_2018_definitions.xlsx`, `soc_2018_direct_match_title_file.xlsx` and `soc_2010_to_2018_crosswalk.xlsx`. Each has a title block above its table; `load` reads every sheet as text from an explicit range and fails the run if a header row is not where it was measured, so a workbook whose layout has moved stops the run instead of staging the wrong cells. BLS's files are U.S. Government works in the public domain.

## Levels, codes and text

Four levels, as BLS names them: `major` (depth 0), `minor` (1), `broad` (2) and `detailed` (3). `code` keeps BLS's hyphen (`11-1011`), the form BLS's own OEWS, O*NET and the Department of Labor's filings carry, so `code_display` is the same. SOC codes are not plain prefixes: the broad occupation `11-3110` sits under the minor group `11-3000`, but `15-1210` sits under `15-1200`, and the detailed occupation `29-1221` sits under the broad occupation `29-1210`. So `parent_code` is the highest code at the level above, at or below the row's own, that shares its leading digits, and `code_list` fails the run if that is not also the group BLS prints last above the row in its sheet. Every row also carries `major_code`, `minor_code` and `broad_code`, the codes of the groups above it, so an occupation rolls up to its major group in one GROUP BY.

`description` is BLS's title and `definition` BLS's definition, which BLS gives for detailed occupations only. `text_source` is `bls` on every row.

The title index has one row per BLS direct match title, keyed `<code>|<title>` because BLS gives its titles no id; `illustrative` carries BLS's mark for a title that is an illustrative example rather than a direct match. Three detailed occupations have no titles. The crosswalk has one row per pairing BLS publishes, with its titles as BLS prints them, including the `(#)` and `(##)` BLS appends to an occupation that pairs with more than one on the other side; BLS gives no weights.

## The release

BLS's 2018 SOC is a fixed edition, revised in place under the same URLs. The definitions file's title block reads "November 2017 (for reference year January 2018 - Updated April 15, 2020)", and the code list's and title index's `release` is `2018/2020-04-15`, the reference year and the update date; `load` fails the run if the line does not parse, or if the direct match title file gives a different update date. The crosswalk's title block reads "November 2017", and its `release` is `2017-11`. A refresh changes nothing in this directory.

## The code columns

finetype has no type for hyphenated SOC codes, and infers them as Australian bank-state-branch numbers or as dates. Each code column is nominated `representation.identifier.numeric_code`, which `describe` widens for the hyphen, and its overrides declare the `^[0-9]{2}-[0-9]{4}$` shape, which the drift schema enforces.

## Boundary (deliberate)

**Publish** is not part of this Protocol, and none of the three tables is published yet. CI's descriptor sweeps read `datasets/*/datapackage.json`, so these `<table>.datapackage.json` files are held to their contracts by the Run's own steps and by `scripts/test_soc_2018_models.py` until they are.

## Run

Needs the `arc` binary, `duckdb` with its `excel` extension, `uv` (for `check_code_list` and the stamps), and `finetype` with its DuckDB extension (`FINETYPE_DUCKDB_EXT`): `arc run`. The three workbooks are under half a megabyte together, and the full run takes under a minute.

## The step list

<!-- protocol-steps: generated from arcform.yaml by scripts/check_protocol_readme.py — do not edit this block by hand -->

All 24 steps are `sql:` models or typed `op:` operators from the arcform catalog. This Protocol runs no opaque `command:`/shell step.

| # | Step | How it runs |
|---|---|---|
| 1 | `fetch_definitions` | `op: http_fetch@1` |
| 2 | `fetch_titles` | `op: http_fetch@1` |
| 3 | `fetch_crosswalk` | `op: http_fetch@1` |
| 4 | `load` | `sql: models/load.sql` |
| 5 | `code_list` | `sql: models/code_list.sql` |
| 6 | `titles` | `sql: models/titles.sql` |
| 7 | `crosswalk` | `sql: models/crosswalk.sql` |
| 8 | `export_soc_2018` | `op: parquet_export@1` |
| 9 | `export_soc_2018_titles` | `op: parquet_export@1` |
| 10 | `export_soc_2010_to_2018` | `op: parquet_export@1` |
| 11 | `describe_soc_2018` | `op: datapackage_describe@1` |
| 12 | `describe_soc_2018_titles` | `op: datapackage_describe@1` |
| 13 | `describe_soc_2010_to_2018` | `op: datapackage_describe@1` |
| 14 | `check_code_list` | `op: uv@1` |
| 15 | `check_inputs_titles` | `sql: models/check_inputs_titles.sql` |
| 16 | `check_titles` | `sql: ../../contracts/check_titles.sql` |
| 17 | `check_inputs_crosswalk` | `sql: models/check_inputs_crosswalk.sql` |
| 18 | `check_crosswalk` | `sql: ../../contracts/check_crosswalk.sql` |
| 19 | `validate_soc_2018` | `op: finetype_validate@1` |
| 20 | `validate_soc_2018_titles` | `op: finetype_validate@1` |
| 21 | `validate_soc_2010_to_2018` | `op: finetype_validate@1` |
| 22 | `stamp_soc_2018` | `op: uv@1` |
| 23 | `stamp_soc_2018_titles` | `op: uv@1` |
| 24 | `stamp_soc_2010_to_2018` | `op: uv@1` |

<!-- /protocol-steps -->
