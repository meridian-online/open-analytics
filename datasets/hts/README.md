# `usitc/hts` — Harmonized Tariff Schedule of the United States

The Harmonized Tariff Schedule of the United States, issued by the U.S. International Trade Commission (USITC): every chapter, heading, subheading, tariff line and statistical line as a row with its parent, so a ten-digit import code rolls up to its chapter without a second source. Each line carries its duty rates, units and footnotes as USITC prints them, and each snapshot records USITC's revision. This directory holds the dataset's three orthogonal facets:

| File | Facet | Answers |
|---|---|---|
| `arcform.yaml` (+ `models/`) | **Protocol** — how it's made | run `arc run` → produces the Dataset |
| `datapackage.json` | **Descriptor** — what it is | schema, finetype labels, the code-list declaration |
| `../registry.json` (this dataset's entry) | **Address** — how it's found | stable `uid`, `usitc/hts`, manifest pointer |

## The Protocol

`arc run` (arcform) executes `arcform.yaml`. One **Run** → one Dataset version. The step DAG:

1. **fetch** — three downloads from USITC: `currentRelease` (the revision's name), the whole schedule as one JSON export, and `currentRelease` again. The export serves only the current revision, so naming the revision on both sides of the download brackets it: `load` fails the run if the two names differ, and a schedule that changed mid-fetch is never published under one revision's name.
2. **outline** (`models/outline.sql`) — read the export as an outline: one row per USITC row in file order, with its indent chain, and for every 2, 4, 6, 8 and 10-digit prefix of a numbered code the row that names it and its text. The file is shared; the Schedule B Protocol runs it by its relative path.
3. **load** (`models/load.sql`) — build the ladder (every numbered code plus the prefixes USITC prints only through longer codes, with a parent one level up for each), the text and `description_full`, the rates, and the revision. Chapter and section titles come from `chapters.csv`. A printed line that prints none of the three rates takes all three from its nearest printed ancestor that prints a general rate, and `rate_from_code` names it; on 2026HTSRev22 only statistical lines do.
4. **package** (`models/package.sql`) — add the search `corpus`, hand off the terminal table.
5. **export** — write the terminal zstd Parquet, ordered by `code`.
6. **describe** — emit `datapackage.json` from the Parquet, typed by finetype with `nominations.finetype.json` and `descriptor.overrides.json` applied.
7. **check_code_list** — hold the Parquet to the shared code-list contract (`scripts/check_code_list.py`): the core columns, a parent one level up for every row, `depth` as the level's position.
8. **validate** — gate the Parquet against `schema.finetype.json`.
9. **stamp_descriptor** — write the finished descriptor into the Parquet's own footer.

Produces `build/hts.parquet`.

## Levels and text

`chapter` (2 digits), `heading` (4), `subheading` (6), `tariff_line` (8) and `statistical_line` (10). `text_source` says whose text a row's `description` is: `usitc` (USITC prints the code), `usitc_derived` (a level USITC prints only through longer codes, taking the text of its node in USITC's outline), `meridian_label` (USITC gives the level no text, as for heading 9903, so it is labelled by level and code), or `usitc_toc` (a chapter title from USITC's table of contents).

## Chapter titles

USITC's export has no chapter rows, and its chapter and section titles live only in its site's table of contents. `chapters.csv` carries them: 99 rows, copied on 2026-11-01 from https://hts.usitc.gov/main.05b4b9b929111922.js, the script that https://hts.usitc.gov/ loads. The run fails if a chapter in the data has no title in the file. HS chapter titles change at HS editions; the next takes effect on 1 January 2027, and the file is to be recopied from the site then.

## The code column

`code` is nominated as finetype's `geography.transportation.hs_code`. Inferred from the values it is `unknown` at 0.49 confidence, since a two-digit chapter and a ten-digit line share no shape. The label's canonical pattern, `^\d{4}\.?\d{2}(\.?\d{2}){0,2}$`, admits digits-only codes of 6, 8 and 10 digits but not the 2-digit chapter or 4-digit heading forms, which this dataset holds; `describe` widens it with `|^[0-9]{2}$|^[0-9]{4}$` (pattern-fit `widened` in the descriptor), and `schema.finetype.json` does the same. On `code` itself, `descriptor.overrides.json` then declares the shape the column holds, digits only at 2, 4, 6, 8 or 10 digits and bounded 2 to 10, so the published pattern refuses dotted forms and lengths no level has; `code_display`, which is dotted, keeps the widened pattern. `rate_from_code` is declared as 6, 8 or 10 digits, since only a subheading, tariff line or statistical line prints rates. The canonical pattern lacking the chapter and heading forms is a mismatch for a finetype issue. `nominations.finetype.json` gives the reason for each nomination.

## Boundary (deliberate)

**Publish** — the content-addressed R2 upload + `manifest.json` / catalog pointer flip — stays in the out-of-repo publish pipeline. It *reads* this Protocol's terminal output `build/hts.parquet` and the emitted `datapackage.json`; it is not an arcform step.

## Run

Needs the `arc` binary, `duckdb`, `uv` (for `check_code_list`), `finetype` with its DuckDB extension (`FINETYPE_DUCKDB_EXT`), and a `python3` that can import `duckdb` for the stamp: `arc run`. The full run over USITC's 12.6 MB export takes about 30 seconds in DuckDB.

## The step list

<!-- protocol-steps: generated from arcform.yaml by scripts/check_protocol_readme.py — do not edit this block by hand -->

11 steps, of which 1 runs through an opaque `command:`/shell step: `stamp_descriptor`. A shell step is invisible to the engine's staleness model and carries its interpreter and dependency pins inline, so what this Protocol guarantees stops short of it. Each shell step's row carries a fingerprint of its command line, so an edit to the line changes this block without the line being printed here.

| # | Step | How it runs |
|---|---|---|
| 1 | `fetch_release_before` | `op: http_fetch@1` |
| 2 | `fetch_hts` | `op: http_fetch@1` |
| 3 | `fetch_release_after` | `op: http_fetch@1` |
| 4 | `outline` | `sql: models/outline.sql` |
| 5 | `load` | `sql: models/load.sql` |
| 6 | `package` | `sql: models/package.sql` |
| 7 | `export_hts` | `op: parquet_export@1` |
| 8 | `describe` | `op: datapackage_describe@1` |
| 9 | `check_code_list` | `op: uv@1` |
| 10 | `validate` | `op: finetype_validate@1` |
| 11 | `stamp_descriptor` | `command:` (shell, sha256 `2be155648ced`) |

<!-- /protocol-steps -->
