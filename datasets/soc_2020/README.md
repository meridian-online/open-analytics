# `ons/soc_2020` — the UK Standard Occupational Classification 2020

The UK Office for National Statistics' Standard Occupational Classification 2020, the classification the Labour Force Survey and Census 2021 code occupations to, as three tables built from ONS's own workbooks in one Run:

| Table | Address | Job | Rows (2026-11-03) |
|---|---|---|---|
| `soc_2020` | `ons/soc_2020` | code list: every major, sub-major, minor and unit group, with ONS's descriptions, entry routes, tasks and related job titles | 551 |
| `soc_2020_titles` | `ons/soc_2020_titles` | job-title index: the coded entries of ONS's coding index, each pointing at a unit group | 32,439 |
| `soc_2010_to_2020` | `ons/soc_2010_to_2020` | edition crosswalk: ONS's pairings of 2010 unit groups with 2020 ones, with ONS's shares and base counts | 714 |

All three are registered in `../registry.json`. This directory holds their facets:

| File | Facet | Answers |
|---|---|---|
| `arcform.yaml` (+ `models/`) | **Protocol** — how they're made | run `arc run` → produces the three tables |
| `<table>.datapackage.json` | **Descriptor** — what each is | schema, finetype labels, the table's contract declaration |
| `<table>.overrides.json`, `<table>.nominations.finetype.json`, `<table>.schema.finetype.json` | the descriptor's inputs | the curated half, the declared finetype labels, the drift contract |

## The Protocol

`arc run` (arcform) executes `arcform.yaml`. One **Run** → one version of each table. The step DAG:

1. **fetch** — ONS's three workbooks: Volume 1 (structure and descriptions of unit groups), Volume 2's coding index, and the SOC 2010 to SOC 2020 relationship tables, each to a fixed path under `build/ons/`.
2. **load** (`models/load.sql`) — check each workbook's header row, read its rows into staging tables, and read the releases from the `.arcmeta` sidecars `http_fetch` writes beside the workbooks and from the coding index's INFO sheet.
3. **code_list**, **titles**, **crosswalk** (`models/`) — build each table: the code list with each row's parent and ancestors, the coding index's coded entries with each code's description, the relationship table with its shares and base counts.
4. **export** — write each table's zstd Parquet to `build/stage/`, ordered by its primary key.
5. **describe** — emit each descriptor from its Parquet, typed by finetype with the table's nominations and overrides applied.
6. **check_code_list** — hold the code list to the shared code-list contract (`scripts/check_code_list.py`, see `docs/code-lists.md`).
7. **check_titles**, **check_crosswalk** — hold the title index and the crosswalk to their contracts (`contracts/check_titles.sql`, `contracts/check_crosswalk.sql`), each after `check_inputs_code_list` points both at the code list exported in the same Run and a `check_inputs_*` model points it at its exported table and the level ONS codes to: every title and every 2020 code must be a unit group in that code list.
8. **validate** — gate each Parquet against its `<table>.schema.finetype.json`.
9. **stamp** — write each published `build/<table>.parquet` with its finished descriptor in the footer, and `<table>.datapackage.json` beside the manifest.
10. **publish** (`ducklake_write`) — write the three tables into the open lake as one snapshot, each upserted on its key, with each descriptor in the lake's `meta` schema and a readable Parquet copy per table at `ons/<table>.parquet`.

Produces `build/soc_2020.parquet`, `build/soc_2020_titles.parquet` and `build/soc_2010_to_2020.parquet`.

## The sources

ONS publishes SOC 2020 at <https://www.ons.gov.uk/methodology/classificationsandstandards/standardoccupationalclassificationsoc/soc2020>. The Protocol reads three workbooks from it: Volume 1 (`soc2020volume1structureanddescriptionofunitgroupsexcel20260827.xlsx`), the coding index from Volume 2 (`soc2020volume2thecodingindexexcel20260827v2.xlsx`, version 14) and the relationship tables (`soc2010soc2020relationshiptablesjuly2021.xlsx`). `load` reads every sheet as text from an explicit range and fails the run if a header row is not the text it was measured as, so a workbook whose layout has moved stops the run instead of staging the wrong cells.

Source: Office for National Statistics licensed under the Open Government Licence v3.0. Each descriptor's `licenses` is `OGL-UK-3.0`, and its description carries that attribution and says that Meridian prepared the table from ONS's files. The licence entry carries the same attribution as `x-attribution` and what Meridian did to the source as `x-changes`; [`docs/licence-notices.md`](../../docs/licence-notices.md) says why and how rule 10 holds them.

## Levels, codes and text

Four levels, as ONS names them: `major` (depth 0, one digit), `sub_major` (1, two digits), `minor` (2, three digits) and `unit` (3, four digits). `code` is ONS's plain digits stored as text (`1111`), the form the Labour Force Survey carries, so `code_display` is the same. Each code is its parent's code with one more digit, so `parent_code` is the code with its last digit dropped, and `code_list` fails the run if that is not a group at the level above. Every row also carries `major_code`, `sub_major_code` and `minor_code`, the codes of the groups above it, so a unit group rolls up to its major group in one GROUP BY.

`description` is ONS's title as Volume 1's descriptions sheet writes it, and `group_description` ONS's description of the group, on every row. Unit groups also carry `entry_routes`, and `tasks` and `related_job_titles` as lists of strings split from ONS's `~ `-bulleted cells. `text_source` is `ons` on every row.

The title index has one row per coded entry of ONS's coding index, keyed by ONS's `UNIQUE ID`. ONS codes many titles by a qualifier, so `additional_qualifier` and `industry_qualifier` are columns, and `corpus` holds the title with both. ONS's own coding fields follow: `default_flag` (the index's `D` column, whose values the field description gives from the index's file specification), the extended SOC sub-unit group and its title, the codes in SOC 2010, SOC 2000, SOC 90 and ISCO-08, and the versions that added or amended the entry. The index's 231 cross-references, which say "see" another title or ONS's notes instead of giving a code, are left out, and `load` fails the run on any other entry without a code: 32,439 of the 32,670 entries are kept.

The crosswalk has one row per pairing in the unit group sheet of the relationship tables. ONS built them from jobs coded to both editions in the Labour Force Survey (January 2014 to September 2016) and the 2011 Census's 1% sample of the economically active in England and Wales, and gives each pairing four shares, men and women in each source. Each 2010 group's "Total sample" row gives its four base counts; those 369 rows become the `*_base` columns on each of the group's pairings, not rows. The shares are fractions from 0 to 1; ONS leaves out pairings with only a few cases, so a group's shares can add up to a little less than 1.

## The release

ONS issues each revision of a workbook under a new URL, and the URL carries its date. `load` reads each release from the `url:` line of the `.arcmeta` sidecar `http_fetch` writes beside the workbook, so it is the release of the file actually fetched: the code list's is `2020/2026-08-27` (the edition and Volume 1's date), the index's `v14/2026-08-27` (its version, from the INFO sheet, and its date) and the crosswalk's `2021-07`. `load` fails the run if any of them does not parse. A refresh changes the URL in `arcform.yaml`, that workbook's source entry in the overrides, any field or table description that quotes today's release or counts, and in `scripts/test_soc_2020_models.py` that workbook's URL constant (`VOLUME1_URL`, `INDEX_URL` or `RELATIONSHIP_URL`, which a test holds to the manifest) and the releases `test_release` and `test_staging_releases` expect.

## The code columns

finetype infers the code list's codes as numeric codes, but the one-digit `major_code` and the title index's SOC 2010, SOC 2000, SOC 90 and ISCO-08 codes as integers, and the extended sub-unit group (`1171/02`) as unknown. Those columns are nominated `representation.identifier.numeric_code`, which `describe` widens for the slash in the sub-unit group, and their overrides declare each code's shape, which the drift schema enforces.

## Publish

The last step publishes the three tables into the open lake (<https://openlake.meridian.online/>), a DuckLake catalog on R2 that readers attach over https without a credential. With all three of `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` and `R2_ACCOUNT_ID` set, from a `.env` here (`cp .env.example .env`, fill it in; `.env` and its variants are gitignored) or from the environment, every `arc run` publishes into the live lake. `arc run --publish` only makes a missing credential fail the run instead of falling back. With any of the three unset, a plain `arc run` still succeeds: the step says it published locally, names the missing variables, and writes a lake into `build/lake/open.ducklake` that attaches and reads back offline. For local work, keep `.env` absent and the variables unset. Better still, keep the credential in a file outside the Protocol folder and export it only for the publishing command, in a subshell: `(set -a; . /path/outside/repo/openlake.env; set +a; arc run --publish)`. Readers need DuckDB 2.0 or later. An unchanged rebuild commits nothing. [`docs/publishing.md`](../../docs/publishing.md) says what a publisher needs first (finetype's DuckDB 2.0 extension in `FINETYPE_DUCKDB_EXT`, and a forced local rebuild after an engine move) and how the open lake was cut over to this step. The Run's own steps hold each table to its contract, and `scripts/test_soc_2020_models.py` holds each committed descriptor to the table the models build.

## Run

Needs the `arc` binary, `duckdb` with its `excel` extension, `uv` (for `check_code_list` and the stamps), and `finetype` with its DuckDB extension (`FINETYPE_DUCKDB_EXT`): `arc run`. The coding index is 3.5 MB of the 3.9 MB fetched, and the full run takes under a minute.

## The step list

<!-- protocol-steps: generated from arcform.yaml by scripts/check_protocol_readme.py — do not edit this block by hand -->

All 26 steps are `sql:` models or typed `op:` operators from the arcform catalog. This Protocol runs no opaque `command:`/shell step.

| # | Step | How it runs |
|---|---|---|
| 1 | `fetch_volume1` | `op: http_fetch@1` |
| 2 | `fetch_coding_index` | `op: http_fetch@1` |
| 3 | `fetch_relationship` | `op: http_fetch@1` |
| 4 | `load` | `sql: models/load.sql` |
| 5 | `code_list` | `sql: models/code_list.sql` |
| 6 | `titles` | `sql: models/titles.sql` |
| 7 | `crosswalk` | `sql: models/crosswalk.sql` |
| 8 | `export_soc_2020` | `op: parquet_export@1` |
| 9 | `export_soc_2020_titles` | `op: parquet_export@1` |
| 10 | `export_soc_2010_to_2020` | `op: parquet_export@1` |
| 11 | `describe_soc_2020` | `op: datapackage_describe@1` |
| 12 | `describe_soc_2020_titles` | `op: datapackage_describe@1` |
| 13 | `describe_soc_2010_to_2020` | `op: datapackage_describe@1` |
| 14 | `check_code_list` | `op: uv@1` |
| 15 | `check_inputs_code_list` | `sql: models/check_inputs_code_list.sql` |
| 16 | `check_inputs_titles` | `sql: models/check_inputs_titles.sql` |
| 17 | `check_titles` | `sql: ../../contracts/check_titles.sql` |
| 18 | `check_inputs_crosswalk` | `sql: models/check_inputs_crosswalk.sql` |
| 19 | `check_crosswalk` | `sql: ../../contracts/check_crosswalk.sql` |
| 20 | `validate_soc_2020` | `op: finetype_validate@1` |
| 21 | `validate_soc_2020_titles` | `op: finetype_validate@1` |
| 22 | `validate_soc_2010_to_2020` | `op: finetype_validate@1` |
| 23 | `stamp_soc_2020` | `op: uv@1` |
| 24 | `stamp_soc_2020_titles` | `op: uv@1` |
| 25 | `stamp_soc_2010_to_2020` | `op: uv@1` |
| 26 | `publish` | `op: ducklake_write@1` |

<!-- /protocol-steps -->
