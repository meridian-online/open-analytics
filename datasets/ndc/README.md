# `fda/ndc` — the National Drug Code directory

The FDA's National Drug Code directory, the codes that identify each finished drug sold in the United States: one row per labeler, product and package, each package under its product and each product under its labeler, with the FDA's fields for each product (names, dosage form, routes, active ingredients, pharmacologic classes, DEA schedule, marketing category and application, dates) and each package (description, dates, whether it is a sample). Each snapshot records the date openFDA last updated the directory. This directory holds the dataset's three orthogonal facets:

| File | Facet | Answers |
|---|---|---|
| `arcform.yaml` (+ `models/`) | **Protocol** — how it's made | run `arc run` → produces the Dataset |
| `build/datapackage.json` | **Descriptor** — what it is (built by `arc run`, not committed: the open lake holds the published copy) | schema, finetype labels, the code-list declaration |
| `../registry.json` (this dataset's entry) | **Address** — how it's found | stable `uid`, `fda/ndc`, manifest pointer |

## The Protocol

`arc run` (arcform) executes `arcform.yaml`. One **Run** → one Dataset version. The step DAG:

1. **fetch** — openFDA's NDC download, one zip at a fixed URL.
2. **extract** — the JSON file from the zip (`drug-ndc-0001-of-0001.json`).
3. **load** (`models/load.sql`) — read the finished drugs' listings into one row per labeler, product and package, with the codes padded to eleven digits and parents by prefix, and stamp `release` from the file's `meta`.
4. **package** (`models/package.sql`) — add the search `corpus`, hand off the terminal table.
5. **export** — write the zstd Parquet to `build/stage/`, ordered by `code`.
6. **describe** — emit the descriptor from the Parquet, typed by finetype with `nominations.finetype.json` and `descriptor.overrides.json` applied.
7. **check_code_list** — hold the Parquet to the shared code-list contract (`scripts/check_code_list.py`, see `docs/code-lists.md`).
8. **validate** — gate the Parquet against `schema.finetype.json`.
9. **stamp_descriptor** — write the published `build/ndc.parquet` with the finished descriptor in its footer, and `build/datapackage.json` beside it.
10. **check before publishing** — `emit_descriptors` writes every published table's descriptor, as the open lake holds it, to `build/lake_descriptors.json`. `check_before_publish` then holds the built descriptor to label agreement with them, to the frictionless reference implementation, and to `scripts/check_descriptors.py`'s rules read against the local Parquet. A failing check stops the run before publish.
11. **publish** — upsert `fda.ndc` into the open lake on `code`, with its descriptor in the lake's `meta` schema and the readable copy at `fda/ndc.parquet` (see *Publish*).

Produces `build/ndc.parquet`.

## The source

The FDA publishes the directory itself as two tab-separated files (`ndctext.zip`) and openFDA publishes the same listings as JSON. The Protocol reads openFDA's file: it is UTF-8 where the FDA's text files are Windows-1252, which DuckDB reads only through an extension installed at run time; it records the date it was last updated and its record count, so the release and the file's completeness are checked from the file itself; and openFDA states its CC0 dedication on its licence page. Its `openfda` block, which openFDA adds from other sources (RxNorm, UNII and others), is not read.

openFDA's download also holds unfinished drugs, bulk ingredients and drugs for further processing (21,645 listings on 2026-10-30). The NDC directory is the finished drugs, and the unfinished listings reuse product NDCs across firms, so `load` reads only listings with `finished = true`.

## Levels, codes and text

Three levels: `labeler` (depth 0), `product` (1) and `package` (2). An NDC is ten digits in three segments printed with hyphens in one of three layouts, 4-4-2, 5-3-2 or 5-4-1. Claims carry it as eleven digits, each segment padded with zeros to 5-4-2, so `code` is that form at every level: five digits for a labeler, nine for a product, eleven for a package, each a prefix of the codes under it. `code_display` is the FDA's hyphenated form. `load` fails the run if two different NDCs pad to the same code.

- A **labeler** row's text is the labeler's name. Labelers submit their names with each listing, and one code's listings can spell it more than one way, so the row takes the name given most often, the first alphabetically on a tie. Product and package rows keep their own listing's spelling in `labeler_name`.
- A **product** row's text is its brand name (openFDA's, which already ends with any suffix), its generic name in brackets where that differs, and its dosage form: `Zepbound (tirzepatide), INJECTION, SOLUTION`. A product NDC can have more than one listing (903 on 2026-10-30), one per labeling document; the row's fields come from the listing certified latest, then the latest marketing start, then the lowest `product_id`, and `listing_count` says how many there were.
- A **package** row's text is the FDA's package description. It carries its own listing's product fields, so a join on a package NDC gets the product's names, ingredients and classes on the same row. A package NDC listed twice keeps the entry with the earlier start date.

`text_source` is `fda` on every row. All of the text is submitted by labelers, and the FDA does not verify it.

## The release and the refresh

`release` is openFDA's `meta.last_updated`, such as `2026-10-30`. `load` fails the run if it is not a date, if the file's record count is not the count `meta` states, or if the directory holds more than one file. The URL does not change, so a refresh changes nothing in this directory. The FDA updates the directory daily; Meridian refreshes `fda/ndc` fortnightly, in place under one name.

## The code columns

`code` and `code_display` are nominated `identity.medical.ndc`. finetype's NDC pattern admits the ten-digit hyphenated layouts and eleven digits, which fit the package rows only, so `describe` widens it, and `descriptor.overrides.json` declares the shapes the columns hold, which `schema.finetype.json` enforces.

## Publish

The last step publishes `fda.ndc` into the open lake (<https://openlake.meridian.online/>), a DuckLake catalog on R2 that readers attach over https without a credential, and writes its readable copy, one Parquet file at <https://openlake.meridian.online/fda/ndc.parquet>. Each refresh upserts the table on `code`: packages openFDA adds are inserted, changed listings updated, and listings it no longer carries deleted, all in one snapshot a reader can time-travel back through. With all three of `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` and `R2_ACCOUNT_ID` set, from a `.env` here (`cp .env.example .env`, fill it in; `.env` and its variants are gitignored) or from the environment, every `arc run` publishes into the live lake. `arc run --publish` only makes a missing credential fail the run instead of falling back. With any of the three unset, a plain `arc run` still succeeds: the step says it published locally, names the missing variables, and writes a lake into `build/lake/open.ducklake` that attaches and reads back offline. For local work, keep `.env` absent and the variables unset. Better still, keep the credential in a file outside the Protocol folder and export it only for the publishing command, in a subshell: `(set -a; . /path/outside/repo/openlake.env; set +a; arc run --publish)`. Readers need DuckDB 2.0 or later. An unchanged rebuild commits nothing. [`docs/publishing.md`](../../docs/publishing.md) says what a publisher needs first.

The fortnightly refresh is that command after `arc run --force`: openFDA's URL does not change, so nothing in the Protocol changes for it.

## Run

Needs the `arc` binary, `duckdb`, `uv` (for `check_code_list` and the stamp), and `finetype` with its DuckDB extension (`FINETYPE_DUCKDB_EXT`): `arc run`. openFDA's zip is 26 MB and its JSON 243 MB; the full run takes under two minutes, most of it in `describe`.

## The step list

<!-- protocol-steps: generated from arcform.yaml by scripts/check_protocol_readme.py — do not edit this block by hand -->

All 12 steps are `sql:` models or typed `op:` operators from the arcform catalog. This Protocol runs no opaque `command:`/shell step.

| # | Step | How it runs |
|---|---|---|
| 1 | `fetch_ndc` | `op: http_fetch@1` |
| 2 | `extract_ndc` | `op: archive_extract@1` |
| 3 | `load` | `sql: models/load.sql` |
| 4 | `package` | `sql: models/package.sql` |
| 5 | `export_ndc` | `op: parquet_export@1` |
| 6 | `describe` | `op: datapackage_describe@1` |
| 7 | `check_code_list` | `op: uv@1` |
| 8 | `validate` | `op: finetype_validate@1` |
| 9 | `stamp_descriptor` | `op: uv@1` |
| 10 | `emit_descriptors` | `op: uv@1` |
| 11 | `check_before_publish` | `op: uv@1` |
| 12 | `publish` | `op: ducklake_write@1` |

<!-- /protocol-steps -->
