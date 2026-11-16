# `census/fips` — FIPS state and county codes

The Census Bureau's FIPS codes (ANSI/INCITS 38 for states, 31 for counties) for US states and counties: one row per state or state-equivalent and per county or county-equivalent, with Census's name, the USPS abbreviation, the GNIS code, Census's class and functional-status codes, and whether the code is in Census's current vintage. The two-digit state code and the five-digit county code are the keys most US government data is joined on. Each snapshot records the two Census vintages it was built from. This directory holds the dataset's three orthogonal facets:

| File | Facet | Answers |
|---|---|---|
| `arcform.yaml` (+ `models/`) | **Protocol** — how it's made | run `arc run` → produces the Dataset |
| `datapackage.json` | **Descriptor** — what it is | schema, finetype labels, the code-list declaration |
| `../registry.json` (this dataset's entry) | **Address** — how it's found | stable `uid`, `census/fips`, manifest pointer |

`census/fips` was first published on 2026-11-16, after the Protocol merged; its descriptor and registry entry were committed with that publish, since CI holds every committed descriptor to the object the lake serves. Commit `datapackage.json` from the run that publishes.

## The Protocol

`arc run` (arcform) executes `arcform.yaml`. One **Run** → one Dataset version. The step DAG:

1. **fetch** — Census's ANSI state and county code lists (`national_state2020.txt`, `national_county2020.txt`) and the Gazetteer's county file (`2026_Gaz_counties_national.zip`).
2. **extract** — the Gazetteer's pipe-delimited county file from its zip.
3. **load** (`models/load.sql`) — read each file as whole lines split on `|`, check its header and every line's shape, check the files against each other, and build one row per state and county (see below). Stamps `release` from the files' names.
4. **package** (`models/package.sql`) — add the search `corpus`, hand off the terminal table.
5. **export** — write the zstd Parquet to `build/stage/`, ordered by `code`.
6. **describe** — emit the descriptor from the Parquet, typed by finetype with `nominations.finetype.json` and `descriptor.overrides.json` applied.
7. **check_code_list** — hold the Parquet to the shared code-list contract (`scripts/check_code_list.py`, see `docs/code-lists.md`).
8. **validate** — gate the Parquet against `schema.finetype.json`.
9. **stamp_descriptor** — write the published `build/fips.parquet` with the finished descriptor in its footer, and `datapackage.json` beside it.
10. **publish** — upsert `census.fips` into the open lake on `code`, with its descriptor in the lake's `meta` schema and the readable copy at `census/fips.parquet` (see *Publish*).

Produces `build/fips.parquet`.

<!-- protocol-steps: generated from arcform.yaml by scripts/check_protocol_readme.py — do not edit this block by hand -->

All 12 steps are `sql:` models or typed `op:` operators from the arcform catalog. This Protocol runs no opaque `command:`/shell step.

| # | Step | How it runs |
|---|---|---|
| 1 | `fetch_states` | `op: http_fetch@1` |
| 2 | `fetch_counties` | `op: http_fetch@1` |
| 3 | `fetch_gazetteer` | `op: http_fetch@1` |
| 4 | `extract_gazetteer` | `op: archive_extract@1` |
| 5 | `load` | `sql: models/load.sql` |
| 6 | `package` | `sql: models/package.sql` |
| 7 | `export_fips` | `op: parquet_export@1` |
| 8 | `describe` | `op: datapackage_describe@1` |
| 9 | `check_code_list` | `op: uv@1` |
| 10 | `validate` | `op: finetype_validate@1` |
| 11 | `stamp_descriptor` | `op: uv@1` |
| 12 | `publish` | `op: ducklake_write@1` |

<!-- /protocol-steps -->

## Two Census vintages

Census's ANSI code-list page (<https://www.census.gov/library/reference/code-lists/ansi.html>) serves the 2020 vintage, the latest it publishes. That vintage predates Connecticut's change of 2022, when Census replaced Connecticut's eight counties (`09001` to `09015`) with its nine planning regions (`09110` to `09190`) as county-equivalents. ACS 2022 and later, QCEW, BEA and most current federal data use the planning regions; older data uses the counties.

So the table is built from two Census files. The ANSI lists are the base: they are the code list Census publishes, they cover the Island Areas and the U.S. Minor Outlying Islands, and they carry Census's class code (`CLASSFP`) and functional status (`FUNCSTAT`). The Gazetteer's county file, which covers the 50 states, DC and Puerto Rico in Census's current vintage, adds any county the ANSI list lacks (today, the nine planning regions, with `text_source = 'census_gazetteer'` and no class or status) and decides `current`:

- true for a county the Gazetteer lists, and for a state with a county in it;
- false for a county in a state the Gazetteer covers that it no longer lists, which today is Connecticut's eight counties, kept because pre-2022 data uses them;
- NULL where the Gazetteer does not cover the state: American Samoa, Guam, the Northern Mariana Islands, the U.S. Virgin Islands and the Minor Outlying Islands.

Measured on 2026-11-16, the two files agree on the name and GNIS code of every county both list. A county the two list under a different name or GNIS code fails the run, naming the code, so a rename in a later vintage is decided by a person rather than published under the older name.

## What `load` refuses

The run fails on any of these, each with an `error()` naming what it found:

- a header that is not Census's, in any of the three files;
- a file name whose vintage does not parse, or ANSI state and county files of different vintages;
- a line with the wrong number of fields, or a field of the wrong shape: a state code that is not two digits, a county code not three, a Gazetteer GEOID not five, a GNIS code not eight, an abbreviation not two letters, a class code not a letter and a digit, a status not one letter, an empty name;
- a repeated code in any file;
- a county, in either county file, whose state is not in the state file, or whose state abbreviation differs from the state file's;
- a county both files list under a different name or GNIS code.

## The release

`release` names both vintages, `ANSI <year>, Gazetteer <year>` (`ANSI 2020, Gazetteer 2026`), read from the files' names, so it cannot disagree with the files the rows came from. Census names each file for its vintage, and arc does not substitute a param into an operator's `with:`, so a new vintage changes literals in `arcform.yaml`: the fetch's URL and `out`, and for the Gazetteer the member in `extract_gazetteer`; `load`'s `depends_on` names the same paths. Update the sources in `descriptor.overrides.json` at the same time.

## The code column

finetype has no FIPS type: a five-digit county code has a ZIP code's shape, so FIPS codes file under `representation.identifier.numeric_code`, which `code` and `code_display` are nominated as. `descriptor.overrides.json` declares the shape the column holds, two or five digits, and `schema.finetype.json` enforces the same.

## Tests

`tests.yaml` holds the Protocol's tests, which `arc test` runs from this folder against the cut-down Census files in `testdata/` (see `testdata/README.md`): each test runs the real steps in a copy of the Protocol, with the fixtures in place of the fetches and the publish left out. One case builds the fixture and holds the table to inline checks (row counts by level, source and `current`, Connecticut's codes, the Island Areas, the release, parents and shapes, accented names, Census's fields); one shows a county only the Gazetteer lists is added; eight breaks rewrite a fixture with SQL and show `load` refuses it with its own message. CI's `protocol-tests` job runs them at the arc revision the workflow pins. `check_code_list`'s own refusals are not tested here, since arc matches only `step 'check_code_list' failed (exit code n)` for a `uv` step; `scripts/test_check_code_list.py` tests the contract.

## Publish

The last step publishes `census.fips` into the open lake (<https://openlake.meridian.online/>), a DuckLake catalog on R2 that readers attach over https without a credential, and writes its readable copy, one Parquet file at <https://openlake.meridian.online/census/fips.parquet>. Each publish upserts the table on `code`: codes Census adds are inserted, changed codes updated, and codes it no longer lists deleted, all in one snapshot a reader can time-travel back through. With all three of `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` and `R2_ACCOUNT_ID` set, from a `.env` here (`cp .env.example .env`, fill it in; `.env` and its variants are gitignored) or from the environment, every `arc run` publishes into the live lake. `arc run --publish` only makes a missing credential fail the run instead of falling back. With any of the three unset, a plain `arc run` still succeeds: the step says it published locally, names the missing variables, and writes a lake into `build/lake/open.ducklake` that attaches and reads back offline. For local work, keep `.env` absent and the variables unset. Better still, keep the credential in a file outside the Protocol folder and export it only for the publishing command, in a subshell: `(set -a; . /path/outside/repo/openlake.env; set +a; arc run --publish)`. Readers need DuckDB 2.0 or later. An unchanged rebuild commits nothing. [`docs/publishing.md`](../../docs/publishing.md) says what else a publisher needs first.

## Run

Needs the `arc` binary at a revision with `arc test` (arcform main from `f57ff3c`; no tagged release has it yet), `duckdb`, `uv` (for `check_code_list` and the stamp), and `finetype` with its DuckDB extension (`FINETYPE_DUCKDB_EXT`): `arc run`, and `arc test` for the tests. The full run over Census's three files takes a few seconds; the tests take about two minutes.
